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
    asn:2906                 按 ASN 取该 AS 公布的 IP 段（RIPE Stat 接口）
    asnfile:2906,40027       按 ASN 从 iptoasn 离线表反查前缀（批量、无限速）
    asnfile4: / asnfile6:    只要 IPv4 / 只要 IPv6
    rir:CN                   RIR 官方委派记录里该国全部分配段（默认查 APNIC）
    rir:CN,all               合并 5 个 RIR 查（更全，但要下 5 个文件）
    rir4: / rir6:            只要 IPv4 / 只要 IPv6
    ct:netflix.com           CT 日志枚举该公司真实签过证书的子域（可选 maxdepth=N）
    file:相对路径            仓库里的本地文件

行尾加 `!opt` 表示可选来源：拉不到就跳过并告警，不让整个分类构建失败。
自主源（rir:/asnfile:/官方接口）不要标，第三方交叉源应该标。

哪些是「一手」的，上游死了也不影响你：
  rir:     RIR（APNIC/ARIN/RIPE…）的官方分配记录，所有「中国 IP 库」的共同源头
  asnfile: iptoasn 每小时的 ASN ↔ 前缀快照，可自建，替代需要逐个请求的 RIPE 接口
  ct:      证书透明度日志，公司签证书必须记录，无法伪造也无法绕过
  json:/url: 指向厂商自己公布的网段表（Google/Cloudflare/Microsoft 等）

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
import gzip
import time
import shutil
import hashlib
import pathlib
import ipaddress
import urllib.request
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / 'sources'
BUILD = ROOT / 'build'
CACHE = ROOT / '.cache'
UA = {'User-Agent': 'mihomo_Rules-fetch/1.0'}

DATA_RAW = 'https://raw.githubusercontent.com/v2fly/domain-list-community/master/data/{}'
RIPESTAT = 'https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS{}'

# RIR 官方委派记录：所有「中国 IP 库」的共同祖先（ispip / gaoyifan / chnroutes2 都派生自它）
RIR_FILES = {
    'apnic': 'https://ftp.apnic.net/stats/apnic/delegated-apnic-latest',
    'arin': 'https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest',
    'ripencc': 'https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-latest',
    'lacnic': 'https://ftp.lacnic.net/pub/stats/lacnic/delegated-lacnic-latest',
    'afrinic': 'https://ftp.afrinic.net/pub/stats/afrinic/delegated-afrinic-latest',
}

# iptoasn：每小时的 ASN ↔ 前缀离线表，下载一次可无限次反查，不受 API 限速。
# 行格式：start_ip \t end_ip \t AS号（纯数字，如 13335）\t 国家码 \t AS 描述
IPTOASN_V4 = 'https://iptoasn.com/data/ip2asn-v4.tsv.gz'
IPTOASN_V6 = 'https://iptoasn.com/data/ip2asn-v6.tsv.gz'
IPTOASN_COMBINED = 'https://iptoasn.com/data/ip2asn-combined.tsv.gz'

# 证书透明度日志：任何公司签 TLS 证书都必须记录，绕不过去，是一手数据
CRTSH = 'https://crt.sh/?q=%25.{}&output=json'

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


def fetch_raw(url, use_cache=True, timeout=30):
    """下载并缓存原始字节。返回 bytes；失败抛异常。"""
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    suffix = pathlib.Path(url.split('?')[0]).suffix or '.txt'
    cached = CACHE / (key + suffix)
    if use_cache and cached.exists() and cached.stat().st_size > 0:
        return cached.read_bytes()
    req = urllib.request.Request(url, headers=UA)
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
            return data
        except Exception as e:                       # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError('拉取失败 %s: %s' % (url, last))


def fetch(url, use_cache=True, timeout=30):
    """下载并缓存。返回文本；失败抛异常。"""
    return fetch_raw(url, use_cache, timeout).decode('utf-8', 'replace')


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


# ----------------------------------------------------------------- 一手数据源

def parse_rir(text, cc, vers, registry):
    """解析 RIR 委派记录，取某国家的 IPv4/IPv6 段。

    行格式：registry|cc|type|start|value|date|status[|extensions...]
      type=ipv4 时 value 是地址数量（256 这种），要换算成前缀长度
      type=ipv6 时 value 就是前缀长度
    只收 allocated / assigned，available / reserved 是没分出去的。
    """
    cc = cc.upper()
    out = []
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        f = line.split('|')
        if len(f) < 7:
            continue
        _reg, country, typ, start, value, _date, status = f[:7]
        if country.upper() != cc or typ not in vers:
            continue
        if status not in ('allocated', 'assigned'):
            continue
        if typ == 'ipv4':
            try:
                n = int(value)
            except ValueError:
                continue
            if n < 1 or n & (n - 1):        # 不是 2 的幂就没法写成前缀，跳过
                continue
            out.append('%s/%d' % (start, 32 - (n.bit_length() - 1)))
        else:
            try:
                plen = int(value)
            except ValueError:
                continue
            if not 0 <= plen <= 128:
                continue
            out.append('%s/%d' % (start, plen))
    return out


def normalize_asns(asns):
    """'2906,AS40027' → {'2906', '40027'}"""
    return {re.sub(r'^AS', '', a.strip().upper()) for a in asns.split(',') if a.strip()}


def parse_ip2asn(text, want):
    """从 iptoasn 的 TSV 里切出指定 AS 公布的前缀。

    行格式（tab 分隔）：start_ip  end_ip  AS号  国家码  AS 描述
       例：1.0.0.0 <TAB> 1.0.0.255 <TAB> 13335 <TAB> US <TAB> CLOUDFLARENET - Cloudflare, Inc.
    注意 AS 号是**纯数字**（13335），不是 AS13335 —— 官网只写 AS_number 不说格式，
    这里两种写法都收。起止 IP 要用 ipaddress 合成最小前缀集合。
    """
    out = []
    for line in text.splitlines():
        f = line.replace('\t', ' ').split()
        if len(f) < 3:
            continue
        if re.sub(r'^AS', '', f[2].upper()) not in want:
            continue
        try:
            a = ipaddress.ip_address(f[0])
            b = ipaddress.ip_address(f[1])
        except ValueError:
            continue
        if a.version != b.version:
            continue
        try:
            out += [str(n) for n in ipaddress.summarize_address_range(a, b)]
        except (ValueError, TypeError):
            continue
    return out


# 内部用途的标签，命中就丢掉：CT 日志里塞满了 test/dev/staging 这类不该进分流规则的域名
CT_INTERNAL = re.compile(
    r'(^|[.\-_])'
    r'(test|tests|testing|dev|devel|develop|development|staging|stage|stg|preprod|preview|'
    r'sandbox|qa|uat|internal|intranet|corp|local|localhost|jenkins|jira|confluence|'
    r'nagios|grafana|kibana|prometheus|vault|consul)'
    r'([.\-_]|$)', re.I)
CT_HOST = re.compile(r'^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}$')


def parse_ct(text, domain, maxdepth=2):
    """解析 crt.sh 的 JSON，枚举该域名下真实签发过证书的子域。

    maxdepth 是相对层数：maxdepth=2 时，a.b.netflix.com 收，a.b.c.netflix.com 不收。
    """
    domain = domain.strip().lower().lstrip('.').rstrip('.')
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    base = domain.count('.')
    names = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        for field in ('name_value', 'common_name'):
            for name in str(entry.get(field) or '').split('\n'):
                n = name.strip().lower().rstrip('.')
                if n.startswith('*.'):
                    n = n[2:]
                if not n or n == domain or not CT_HOST.match(n):
                    continue
                if not n.endswith('.' + domain):
                    continue
                if n.count('.') - base > maxdepth:
                    continue
                if CT_INTERNAL.search(n):
                    continue
                names.add(n)
    return sorted(names)


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
    """返回 [(kind, spec, want, deny, optional)]

    行尾的 `!opt` 表示「可选来源」：拉不到就跳过并告警，不让整个分类构建失败。
    用在第三方交叉源上——自主源必须成功，别人的源挂了不该拖垮你。
    """
    items = []
    for raw in path.read_text('utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        spec, optional, rest = parts[0], False, []
        for t in parts[1:]:
            if t == '!opt':
                optional = True
            else:
                rest.append(t)
        want, deny = parse_attr_filter(rest)
        if spec.startswith('data:'):
            items.append(('data', spec[len('data:'):], want, deny, optional))
        elif spec.startswith('url:'):
            items.append(('url', spec[len('url:'):], want, deny, optional))
        elif spec.startswith('json:'):
            url, _, sel = spec[len('json:'):].partition('|')
            if not sel:
                raise RuntimeError('json: 需要选择器，例如 json:https://x/y.json|a[].b')
            items.append(('json', (url, sel), want, deny, optional))
        elif spec.startswith('asnfile:'):
            items.append(('asnfile', (spec[len('asnfile:'):], ('ipv4', 'ipv6')), want, deny, optional))
        elif spec.startswith('asnfile4:'):
            items.append(('asnfile', (spec[len('asnfile4:'):], ('ipv4',)), want, deny, optional))
        elif spec.startswith('asnfile6:'):
            items.append(('asnfile', (spec[len('asnfile6:'):], ('ipv6',)), want, deny, optional))
        elif spec.startswith('asn:'):
            items.append(('asn', spec[len('asn:'):].lstrip('AS'), want, deny, optional))
        elif spec.startswith('rir:'):
            items.append(('rir', (spec[len('rir:'):], ('ipv4', 'ipv6')), want, deny, optional))
        elif spec.startswith('rir4:'):
            items.append(('rir', (spec[len('rir4:'):], ('ipv4',)), want, deny, optional))
        elif spec.startswith('rir6:'):
            items.append(('rir', (spec[len('rir6:'):], ('ipv6',)), want, deny, optional))
        elif spec.startswith('ct:'):
            items.append(('ct', spec[len('ct:'):], want, deny, optional))
        elif spec.startswith('file:'):
            items.append(('file', spec[len('file:'):], want, deny, optional))
        else:
            raise RuntimeError('%s: 不认识的来源 %r' % (path.name, spec))
    return items


def build_one(name, behavior, src_file, remote, use_cache, excluder, junk):
    all_rules, extra, stats = [], [], []
    for kind, spec, want, deny, optional in read_sources(src_file):
        rules, other, note = [], [], ''
        try:
            if kind == 'data':
                rules, skipped = parse_dlc(remote.raw(spec), remote, want, deny, (spec,))
                note = 'data:%s%s=%d%s' % (spec, ''.join(' @' + a for a in sorted(want)),
                                           len(rules),
                                           ' (+%d过滤)' % skipped if skipped else '')
            elif kind == 'url':
                text = fetch(spec, use_cache)
                rules, other = parse_plain_or_clash(text, bare_is_suffix=True)
                note = 'url:%s=%d' % (spec.split('/')[-1], len(rules))
            elif kind == 'json':
                url, sel = spec
                data = json.loads(fetch(url, use_cache))
                found = jsonpath(data, sel)
                for v in found:
                    if CIDR_RE.fullmatch(v.strip()):
                        other.append('IP-CIDR,%s' % v.strip())
                    else:
                        rules.append(('domain', v, frozenset()))
                note = 'json:%s#%s=%d' % (url.split('/')[-1][:22], sel, len(found))
            elif kind == 'asn':
                data = json.loads(fetch(RIPESTAT.format(spec), use_cache))
                found = jsonpath(data, 'data.prefixes[].prefix')
                for v in found:
                    if CIDR_RE.fullmatch(v.strip()):
                        other.append('IP-CIDR,%s' % v.strip())
                    else:
                        rules.append(('domain', v, frozenset()))
                note = 'asn:AS%s=%d' % (spec, len(found))
            elif kind == 'rir':
                cc, vers = spec
                all_rirs = cc.lower().endswith(',all')
                if all_rirs:
                    cc = cc[:-len(',all')]
                srcs = list(RIR_FILES.items()) if all_rirs else [('apnic', RIR_FILES['apnic'])]
                found, failed = [], []
                for reg, url in srcs:
                    try:
                        found += parse_rir(fetch(url, use_cache, timeout=180), cc, vers, reg)
                    except Exception as e:                      # noqa: BLE001
                        failed.append('%s(%s)' % (reg, e))
                if not found:
                    raise RuntimeError('rir:%s 全部失败: %s' % (cc, '; '.join(failed)))
                found = sorted(set(found))
                other += ['IP-CIDR,%s' % v for v in found]
                note = 'rir:%s %s=%d%s' % (cc, '+'.join(vers), len(found),
                                           ' [!%s]' % ','.join(failed) if failed else '')
            elif kind == 'asnfile':
                asns, vers = spec
                want_as = normalize_asns(asns)
                if not want_as:
                    raise RuntimeError('asnfile: 没写 AS 号')
                # 两族都要时下合并文件（一次请求 10MB），只要一族就下对应的单文件
                if len(vers) == 2:
                    srcs = [(IPTOASN_COMBINED, 'combined')]
                elif vers == ('ipv4',):
                    srcs = [(IPTOASN_V4, 'ipv4')]
                else:
                    srcs = [(IPTOASN_V6, 'ipv6')]
                found, failed = [], []
                for url, label in srcs:
                    try:
                        raw = fetch_raw(url, use_cache, timeout=300)
                        try:
                            text = gzip.decompress(raw).decode('utf-8', 'replace')
                        except OSError:
                            text = raw.decode('utf-8', 'replace')
                        found += parse_ip2asn(text, want_as)
                    except Exception as e:                      # noqa: BLE001
                        failed.append('%s(%s)' % (label, e))
                if not found:
                    raise RuntimeError('asnfile 全部失败: %s' % '; '.join(failed))
                found = sorted(set(found))
                other += ['IP-CIDR,%s' % v for v in found]
                note = 'asnfile:AS%s=%d%s' % (','.join(sorted(want_as)), len(found),
                                              ' [!%s]' % ','.join(failed) if failed else '')
            elif kind == 'ct':
                domain, _, params = spec.partition(',')
                maxdepth = 2
                for p in params.split(','):
                    if p.startswith('maxdepth='):
                        maxdepth = int(p.split('=', 1)[1])
                found = parse_ct(fetch(CRTSH.format(domain), use_cache, timeout=180),
                                 domain, maxdepth)
                rules = [('domain', v, frozenset()) for v in found]
                note = 'ct:%s=%d' % (domain, len(found))
            else:
                text = (ROOT / spec).read_text('utf-8', 'replace')
                rules, other = parse_plain_or_clash(text)
                note = 'file:%s=%d' % (spec, len(rules))
        except Exception as e:                                # noqa: BLE001
            if optional:
                print('   ! 可选来源跳过（%s）：%s' % (kind, e))
                stats.append('%s:跳过' % kind)
                continue
            raise
        extra += other
        stats.append(note)
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
