#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 sources/ 里的声明清单拉取上游数据，归一化成 mihomo 规则集文本，写到 build/。

设计要点
--------
1. **声明式**：`sources/domain/<分类名>.src` 里一行一个来源，分类名就是文件名——
   这就是「分类整合」的全部机制，不需要改任何上游。
2. **语法对齐 dlc**：来源行直接沿用 v2fly/domain-list-community 的语义，
   `v2fly:apple @cn` 和 dlc 里的 `include:apple @cn` 含义一致（带 @cn 属性的才要）。
3. **属性过滤**：`@a` = 必须有 a；`@-b` = 必须没有 b。多个条件是与关系。
4. **归一化 + 压缩**：dlc 的 `domain:` → `+.x`（含自身与所有子域），`full:` → `x`（精确）。
   然后做后缀冗余消除：`+.x` 已经覆盖的条目直接删掉，列表能明显变小。
5. **keyword / regexp 不丢**：mihomo 的 `behavior: domain` 装不下它们（没有对应语法），
   所以另存 `build/classical/<分类>.list`，需要的人用 classical provider 引。

零依赖（只用标准库），带本地缓存，重复跑不会反复拉上游。

用法：
    python scripts/fetch-sources.py                # 全部
    python scripts/fetch-sources.py telegram cn    # 只跑指定分类
    python scripts/fetch-sources.py --no-cache
"""
import os
import re
import sys
import time
import shutil
import hashlib
import pathlib
import urllib.request
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / 'sources'
BUILD = ROOT / 'build'
CACHE = ROOT / '.cache'
UA = {'User-Agent': 'mihomo_Rules-fetch/1.0'}

V2FLY_RAW = 'https://raw.githubusercontent.com/v2fly/domain-list-community/master/data/{}'

ATTR_RE = re.compile(r'^@(-?)([\w\-!]+)$')


# ----------------------------------------------------------------- 工具

def fetch(url, use_cache=True):
    """下载并缓存。返回文本；失败抛异常。"""
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    suffix = pathlib.Path(url.split('?')[0]).suffix or '.txt'
    cached = CACHE / (key + suffix)
    if use_cache and cached.exists() and cached.stat().st_size > 0:
        return cached.read_text('utf-8', 'replace')
    req = urllib.request.Request(url, headers=UA)
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode('utf-8', 'replace')
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(text, 'utf-8')
            return text
        except Exception as e:                       # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError('拉取失败 %s: %s' % (url, last))


class Remote:
    """v2fly data/ 的取数代理，带缓存 + 递归 include。"""

    def __init__(self, use_cache=True):
        self.use_cache = use_cache
        self.files = {}

    def raw(self, name):
        if name not in self.files:
            if not re.fullmatch(r'[\w\-.!]+', name):
                raise RuntimeError('非法分类名: %r' % name)
            self.files[name] = fetch(V2FLY_RAW.format(name), self.use_cache)
        return self.files[name]


# ----------------------------------------------------------------- dlc 语法

def parse_attr_filter(tokens):
    """['@cn', '@-ads'] -> (必须有, 必须没有)"""
    want, deny = set(), set()
    for t in tokens:
        m = ATTR_RE.match(t)
        if not m:
            raise RuntimeError('看不懂的属性条件: %r' % t)
        (deny if m.group(1) else want).add(m.group(2))
    return frozenset(want), frozenset(deny)


def attr_ok(attrs, want, deny):
    return want <= attrs and not (deny & attrs)


def parse_dlc(text, remote, _want=frozenset(), _deny=frozenset(), _stack=()):
    """解析一个 dlc data 文件（含 include 递归），产出 (rules, skipped)。

    rules   : list of (kind, value, frozenset(attrs))，kind ∈ domain/full/keyword/regexp
    skipped : 被属性条件过滤掉的条数
    """
    out, skipped = [], 0
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        head, rest = parts[0], parts[1:]

        if head.startswith('include:'):
            target = head[len('include:'):]
            if not target:
                raise RuntimeError('空的 include:')
            if target in _stack:
                raise RuntimeError('循环 include: %s' % ' -> '.join(_stack + (target,)))
            iw, idn = parse_attr_filter(rest)
            sub, sub_skipped = parse_dlc(remote.raw(target), remote, iw, idn,
                                         _stack + (target,))
            # include 的条件和当前文件的条件是叠加的
            for kind, value, attrs in sub:
                if attr_ok(attrs, _want, _deny):
                    out.append((kind, value, attrs))
                else:
                    skipped += 1
            skipped += sub_skipped
            continue

        if head.startswith('&'):          # affiliation，纯数据管理，对我们无意义
            continue

        kind, value = 'domain', head
        for prefix in ('domain:', 'full:', 'keyword:', 'regexp:'):
            if head.startswith(prefix):
                kind, value = prefix[:-1], head[len(prefix):]
                break
        attrs = frozenset()
        for t in rest:
            m = ATTR_RE.match(t)
            if m and not m.group(1):
                attrs |= {m.group(2)}
            elif not t.startswith('&'):
                # full:/keyword: 后面可能跟注释性 token，忽略即可
                pass
        if attr_ok(attrs, _want, _deny):
            out.append((kind, value, attrs))
        else:
            skipped += 1
    return out, skipped


# ----------------------------------------------------------------- 其它格式

CLASH_LINE = re.compile(r'^(DOMAIN|DOMAIN-SUFFIX|DOMAIN-KEYWORD|DOMAIN-REGEX|IP-CIDR6?|'
                        r'PROCESS-NAME|PROCESS-PATH|SRC-IP-CIDR|DST-PORT|SRC-PORT),')


def parse_plain_or_clash(text, bare_is_suffix=False):
    """吃两种东西：Clash 规则 list，和一行一个域名/CIDR 的纯文本。

    bare_is_suffix：裸域名怎么解释。本地手写文件按 mihomo 语义（裸 = 精确，`+.` 才是后缀），
    远端抓来的陌生纯文本列表按「一行一个后缀」解释（这是绝大部分公开列表的惯例）。
    """
    rules, other = [], []
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line or line.startswith('//') or line.startswith(';'):
            continue
        if line.startswith('payload:') or line in ('[]',):
            continue
        line = line.lstrip('- ').strip().strip('"\'')
        if line.startswith('[') or line.startswith('"') or line.startswith('-'):
            line = line.strip('[]",')
            line = line.strip()
        if not line:
            continue
        if CLASH_LINE.match(line):
            kind, _, val = line.partition(',')
            val = val.split(',')[0].strip()
            if kind == 'DOMAIN':
                rules.append(('full', val, frozenset()))
            elif kind == 'DOMAIN-SUFFIX':
                rules.append(('domain', val, frozenset()))
            elif kind == 'DOMAIN-KEYWORD':
                rules.append(('keyword', val, frozenset()))
            elif kind == 'DOMAIN-REGEX':
                rules.append(('regexp', val, frozenset()))
            elif kind.startswith('IP-CIDR'):
                other.append('IP-CIDR,%s' % val + (',no-resolve' if 'no-resolve' in line else ''))
            else:
                other.append(line)
        elif '/' in line and re.fullmatch(r'[0-9a-fA-F:.]+/\d+', line):
            other.append('IP-CIDR,%s' % line)
        elif line.startswith('+.'):
            rules.append(('domain', line[2:], frozenset()))
        elif'.' in line or re.fullmatch(r'[a-z0-9\-]+', line):
            if bare_is_suffix:
                rules.append(('domain', line, frozenset()))
            else:
                rules.append(('full', line, frozenset()))
    return rules, other


# ----------------------------------------------------------------- 归一化

def normalize(rules):
    """→ (exact, suffix, keyword, regexp)。做后缀冗余消除。"""
    exact, suffix, keyword, regexp = set(), set(), set(), set()
    for kind, value, _ in rules:
        v = value.strip().strip('.').lower()
        if not v:
            continue
        if kind == 'full':
            exact.add(v)
        elif kind == 'domain':
            suffix.add(v)
        elif kind == 'keyword':
            keyword.add(value)
        elif kind == 'regexp':
            regexp.add(value)

    # `+.host.example.com` 覆盖 `+.example.com` 之外的二级域 → 若父后缀在集合里，子项冗余
    def redundant(host):
        labels = host.split('.')
        return any('.'.join(labels[i:]) in suffix for i in range(1, len(labels)))

    suffix = {s for s in suffix if not redundant(s)}

    def covered(host):
        labels = host.split('.')
        return any('.'.join(labels[i:]) in suffix for i in range(len(labels)))

    exact = {e for e in exact if not covered(e)}
    return exact, suffix, keyword, regexp


# ----------------------------------------------------------------- 主流程

def read_sources(path):
    """返回 [(kind, spec, want, deny)]"""
    items = []
    for raw in path.read_text('utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        spec, rest = parts[0], parts[1:]
        want, deny = parse_attr_filter(rest)
        if spec.startswith('v2fly:'):
            items.append(('v2fly', spec[len('v2fly:'):], want, deny))
        elif spec.startswith('url:'):
            items.append(('url', spec[len('url:'):], want, deny))
        elif spec.startswith('file:'):
            items.append(('file', spec[len('file:'):], want, deny))
        else:
            raise RuntimeError('%s: 不认识的来源 %r' % (path.name, spec))
    return items


def build_one(name, behavior, src_file, remote, use_cache):
    all_rules, extra, stats = [], [], []
    for kind, spec, want, deny in read_sources(src_file):
        if kind == 'v2fly':
            rules, skipped = parse_dlc(remote.raw(spec), remote, want, deny, (spec,))
            stats.append('v2fly:%s%s=%d%s' % (spec, ''.join(' @' + a for a in sorted(want)),
                                              len(rules),
                                              ' (+%d过滤)' % skipped if skipped else ''))
        elif kind == 'url':
            text = fetch(spec, use_cache)
            rules, other = parse_plain_or_clash(text, bare_is_suffix=True)
            extra += other
            stats.append('url:%s=%d' % (spec.split('/')[-1], len(rules)))
        else:
            text = (ROOT / spec).read_text('utf-8', 'replace')
            rules, other = parse_plain_or_clash(text)
            extra += other
            stats.append('file:%s=%d' % (spec, len(rules)))
        all_rules += rules

    exact, suffix, keyword, regexp = normalize(all_rules)

    # other 里混着 IP-CIDR / 进程名等；ipcidr 行为的主产物就是那些 CIDR
    ips, misc = [], []
    for item in extra:
        if item.startswith('IP-CIDR,'):
            ips.append(item.split(',')[1])
        else:
            misc.append(item)

    outdir = BUILD / behavior
    outdir.mkdir(parents=True, exist_ok=True)
    header = ('# 由 scripts/fetch-sources.py 从 sources/%s/%s 生成 —— 不要手改'
              % (behavior, src_file.name))
    if behavior == 'ipcidr':
        body = sorted(set(ips))
    else:
        body = ['+.%s' % s for s in sorted(suffix)] + sorted(exact)
    note = '  ← ' + ' | '.join(stats) if stats else ''
    (outdir / (name + '.txt')).write_text('\n'.join([header] + body) + '\n', 'utf-8')

    if keyword or regexp or misc:
        cdir = BUILD / 'classical'
        cdir.mkdir(parents=True, exist_ok=True)
        clines = ['# classical：装 domain 规则集装不下的东西（keyword / regexp / 进程）']
        clines += ['DOMAIN-KEYWORD,%s' % k for k in sorted(keyword)]
        clines += ['DOMAIN-REGEX,%s' % r for r in sorted(regexp)]
        clines += misc
        (cdir / (name + '.list')).write_text('\n'.join(clines) + '\n', 'utf-8')

    print('%-22s %6d 条  (suffix %d / exact %d / keyword %d / regexp %d)%s' % (
        name, len(body), len(suffix), len(exact), len(keyword), len(regexp), note))
    return len(body)


def main():
    argv = sys.argv[1:]
    use_cache = '--no-cache' not in argv
    wanted = {a for a in argv if not a.startswith('--')}
    if not SRC.exists():
        print('没有 sources/ 目录')
        return 1
    if BUILD.exists():                                # 全量重建，避免残留旧分类
        shutil.rmtree(BUILD)
    remote = Remote(use_cache)
    total = 0
    for behavior in ('domain', 'ipcidr'):
        d = SRC / behavior
        if not d.is_dir():
            continue
        print('=== %s ===' % behavior)
        for src_file in sorted(d.glob('*.src')):
            name = src_file.stem
            if wanted and name not in wanted:
                continue
            try:
                total += build_one(name, behavior, src_file, remote, use_cache)
            except Exception as e:                   # noqa: BLE001
                print('!! %-20s %s' % (name, e))
    print('\n合计 %d 条，产物在 build/' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main())
