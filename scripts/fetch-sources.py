#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 sources/ 里的声明清单拉取上游数据，归一化成 mihomo 规则集文本，写到 build/。

设计要点
--------
1. **声明式**：`sources/domain/<分类名>.src` 里一行一个来源，分类名就是文件名——
   这就是「分类整合」的全部机制，不需要改任何上游。
2. **语法沿用上游数据集**：来源行直接沿用上游 data 目录的语法，
   `data:apple @cn` 和上游的 `include:apple @cn` 含义一致（带 @cn 属性的才要）。
3. **属性过滤**：`@a` = 必须有 a；`@-b` = 必须没有 b。多个条件是与关系。
4. **归一化 + 压缩**：上游的 `domain:` → `+.x`（含自身与所有子域），`full:` → `x`（精确）。
   然后做后缀冗余消除：`+.x` 已经覆盖的条目直接删掉，列表能明显变小。
5. **keyword / regexp 不丢**：mihomo 的 `behavior: domain` 装不下它们（没有对应语法），
   所以另存 `build/classical/<分类>.list`，需要的人用 classical provider 引。
6. **排除垃圾**：`sources/exclude.txt` 定义剔除规则，对所有 domain 分类生效；
   被剔掉的条目单独收集成 `build/domain/junk.txt`，可以整集指到 REJECT。

来源类型：
    data:分类名 [@属性]      上游 data 目录，自动递归解析 include
    url:地址                 远端 Clash list，或一行一个域名 / CIDR 的纯文本
    json:地址|选择器         JSON 接口，选择器如 prefixes[].ipv4Prefix / [].ipv4
    asn:2906                 按 ASN 取该 AS 公布的 IP 段
    file:相对路径            仓库里的本地文件

零依赖（只用标准库），带本地缓存，重复跑不会反复拉上游。

用法：
    python scripts/fetch-sources.py                # 全部
    python scripts/fetch-sources.py telegram cn    # 只跑指定分类
    python scripts/fetch-sources.py --no-cache
"""
import os
import re
import sys
import json
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

DATA_RAW = 'https://raw.githubusercontent.com/v2fly/domain-list-community/master/data/{}'
RIPESTAT = 'https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS{}'

# 这些分类本身就在装垃圾，不必再被 exclude 过滤一遍
EXCLUDE_SKIP = {'ads', 'anti-ad', 'junk'}

ATTR_RE = re.compile(r'^@(-?)([\w\-!]+)$')
CIDR_RE = re.compile(r'[0-9a-fA-F:.]+/\d{1,3}$')


# ----------------------------------------------------------------- 工具

def jsonpath(data, sel):
    """极简 JSON 取值：`.key` 取字段，`key[]` / `[]` 展开数组。

    'prefixes[].ipv4Prefix'  → 取 prefixes 数组里每项的 ipv4Prefix
    '[].ipv4'                → 顶层就是数组时用
    """
    cur = [data]
    for part in sel.split('.'):
        arr = part.endswith('[]')
        key = part[:-2] if arr else part
        nxt = []
        for node in cur:
            if key == '':
                vals = node if isinstance(node, list) else [node]
            elif isinstance(node, dict) and key in node:
                vals = node[key]
            else:
                continue
            if arr:
                nxt.extend(vals if isinstance(vals, list) else [vals])
            else:
                nxt.append(vals)
        cur = nxt
    out = []
    for node in cur:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, list):
            out.extend(x for x in node if isinstance(x, str))
    return out


VOWELS = set('aeiou')
WORDLIKE = re.compile(r'^[a-z0-9-]+$')


class Excluder:
    """按 sources/exclude.txt 判断一个域名要不要剔掉。

    规则按顺序求值，后面的可以推翻前面的，所以「先粗筛、再放行」是这么写的：

        ~jav        子串
        !java       子串命中就放行
        +.a.com     后缀（含自身）
        a.com       精确

    曾经试过用「首标签像随机串」的启发式来兜垃圾站，实测误杀太重
    （appldnld.apple.com、360buyimg.com、17173cdn.com、51jobcdn.com 这类
    数字前缀 + 缩写在国内域名里太常见），已经去掉。关键词规则虽然要人工维护，
    但每一条都能解释、能 review。
    """

    def __init__(self, rules):
        self.rules = rules

    def __bool__(self):
        return bool(self.rules)

    def hit(self, host):
        host = host.lower()
        state = False
        for kind, val in self.rules:
            if kind == 'sub':
                if val in host:
                    state = True
            elif kind == 'not':
                if val in host:
                    state = False
            elif kind == 'exact':
                if host == val:
                    state = True
            elif kind == 'suffix':
                if host == val or host.endswith('.' + val):
                    state = True
        return state


def load_excluder():
    path = SRC / 'exclude.txt'
    if not path.exists():
        return Excluder([])
    rules = []
    for raw in path.read_text('utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('~'):
            rules.append(('sub', line[1:].lower()))
        elif line.startswith('!'):
            rules.append(('not', line[1:].lower()))
        elif line.startswith('+.'):
            rules.append(('suffix', line[2:].lower()))
        else:
            rules.append(('exact', line.lower()))
    print('exclude.txt: %d 条规则' % len(rules))
    return Excluder(rules)


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
    """上游 data 目录的取数代理，带缓存 + 递归 include。"""

    def __init__(self, use_cache=True):
        self.use_cache = use_cache
        self.files = {}

    def raw(self, name):
        if name not in self.files:
            if not re.fullmatch(r'[\w\-.!]+', name):
                raise RuntimeError('非法分类名: %r' % name)
            self.files[name] = fetch(DATA_RAW.format(name), self.use_cache)
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
        if spec.startswith('data:'):
            items.append(('data', spec[len('data:'):], want, deny))
        elif spec.startswith('url:'):
            items.append(('url', spec[len('url:'):], want, deny))
        elif spec.startswith('json:'):
            url, _, sel = spec[len('json:'):].partition('|')
            if not sel:
                raise RuntimeError('json: 需要选择器，例如 json:https://x/y.json|a[].b')
            items.append(('json', (url, sel), want, deny))
        elif spec.startswith('asn:'):
            items.append(('asn', spec[len('asn:'):].lstrip('AS'), want, deny))
        elif spec.startswith('file:'):
            items.append(('file', spec[len('file:'):], want, deny))
        else:
            raise RuntimeError('%s: 不认识的来源 %r' % (path.name, spec))
    return items


def build_one(name, behavior, src_file, remote, use_cache, excluder, junk):
    all_rules, extra, stats = [], [], []
    for kind, spec, want, deny in read_sources(src_file):
        if kind == 'data':
            rules, skipped = parse_dlc(remote.raw(spec), remote, want, deny, (spec,))
            stats.append('data:%s%s=%d%s' % (spec, ''.join(' @' + a for a in sorted(want)),
                                              len(rules),
                                              ' (+%d过滤)' % skipped if skipped else ''))
        elif kind == 'url':
            text = fetch(spec, use_cache)
            rules, other = parse_plain_or_clash(text, bare_is_suffix=True)
            extra += other
            stats.append('url:%s=%d' % (spec.split('/')[-1], len(rules)))
        elif kind == 'json':
            url, sel = spec
            data = json.loads(fetch(url, use_cache))
            found = jsonpath(data, sel)
            rules = []
            for v in found:
                if CIDR_RE.fullmatch(v.strip()):
                    extra.append('IP-CIDR,%s' % v.strip())
                else:
                    rules.append(('domain', v, frozenset()))
            stats.append('json:%s#%s=%d' % (url.split('/')[-1][:22], sel, len(found)))
        elif kind == 'asn':
            data = json.loads(fetch(RIPESTAT.format(spec), use_cache))
            found = jsonpath(data, 'data.prefixes[].prefix')
            rules = []
            for v in found:
                if CIDR_RE.fullmatch(v.strip()):
                    extra.append('IP-CIDR,%s' % v.strip())
                else:
                    rules.append(('domain', v, frozenset()))
            stats.append('asn:AS%s=%d' % (spec, len(found)))
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
        if excluder and name not in EXCLUDE_SKIP:
            kept, dropped = [], []
            for line in body:
                host = line[2:] if line.startswith('+.') else line
                (dropped if excluder.hit(host) else kept).append(line)
            if dropped:
                junk.update(dropped)
                stats.append('exclude=-%d' % len(dropped))
                body = kept
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
    excluder = load_excluder()
    junk = set()
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
                total += build_one(name, behavior, src_file, remote, use_cache, excluder, junk)
            except Exception as e:                   # noqa: BLE001
                print('!! %-20s %s' % (name, e))

    if junk:                       # 被剔掉的条目单独成集，方便直接指到 REJECT
        out = BUILD / 'domain' / 'junk.txt'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('\n'.join(['# 被 sources/exclude.txt 剔出来的条目 —— 不要手改'] +
                                 sorted(junk)) + '\n', 'utf-8')
        print('\njunk %d 条（从各分类里剔除，单独成集）' % len(junk))
    print('\n合计 %d 条，产物在 build/' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main())
