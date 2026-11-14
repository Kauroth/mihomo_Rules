#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 build/ 里的文本规则集转成 sing-box 的 rule-set，并编译成 .srs。

为什么不用退化成 regex
----------------------
sing-box 的 headless rule 和 mihomo 的 domain 规则集在语义上刚好一一对应：

    +.x   →  {"domain_suffix": ["x"]}   命中 x 本身和所有子域
    x     →  {"domain": ["x"]}          只命中 x

已用 `sing-box rule-set match` 实测确认：`domain_suffix: "a.com"` 命中 a.com、
x.a.com、x.y.a.com，不命中 nota.com；`domain: "a.com"` 只命中 a.com。
所以不需要 regex 兜底，两个内核的行为能严格对齐。

本仓库的 domain 产物只允许 `x` 和 `+.x` 两种写法（由 fetch-sources.py 保证），
这里再强制检查一遍 —— 出现别的写法直接让构建失败，而不是悄悄转错。

另一个好处：mihomo 那边 keyword / regexp 装不进 domain 规则集，只能另存
classical 让别人自己引；sing-box 有 domain_keyword / domain_regex 字段，
可以直接合并进同一个 .srs，客户端只引一个文件就够了。

用法：
    SINGBOX=/path/to/sing-box python3 scripts/build-singbox.py

环境变量：
    SINGBOX                   sing-box 可执行文件；不设就只生成 json 不编译
    SINGBOX_RULESET_VERSION   rule-set 版本，默认 3（sing-box 1.11+）
    DIST                      产物目录，默认 dist/
"""
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / 'build'
DIST = pathlib.Path(os.environ.get('DIST') or ROOT / 'dist')
SINGBOX = os.environ.get('SINGBOX', '')
VERSION = int(os.environ.get('SINGBOX_RULESET_VERSION', '3'))

SUFFIX_PREFIX = '+.'
CIDR_RE = re.compile(r'^[0-9a-fA-F:.]+/\d{1,3}$')
HOST_RE = re.compile(r'^[a-z0-9][a-z0-9.\-]*$')


def read_lines(path):
    out = []
    for line in path.read_text('utf-8', 'replace').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            out.append(line)
    return out


def build_domain(name):
    """domain 分类 → rules 数组。"""
    suffix, exact = [], []
    for line in read_lines(BUILD / 'domain' / (name + '.txt')):
        if line.startswith(SUFFIX_PREFIX):
            suffix.append(line[len(SUFFIX_PREFIX):])
        elif CIDR_RE.match(line):
            raise SystemExit('!! %s: domain 产物里出现了 CIDR：%s' % (name, line))
        elif HOST_RE.match(line):
            exact.append(line)
        else:
            raise SystemExit('!! %s: 看不懂的写法 %r —— 本仓库只允许 x 和 +.x，'
                             '转 sing-box 前必须先修掉' % (name, line))

    keyword, regexp, proc, skipped = [], [], [], []
    classical = BUILD / 'classical' / (name + '.list')
    if classical.exists():
        for line in read_lines(classical):
            if line.startswith('DOMAIN-KEYWORD,'):
                keyword.append(line.split(',', 1)[1])
            elif line.startswith('DOMAIN-REGEX,'):
                regexp.append(line.split(',', 1)[1])
            elif line.startswith('PROCESS-NAME,'):
                proc.append(line.split(',', 1)[1])
            else:
                skipped.append(line)

    rules = []
    if suffix:
        rules.append({'domain_suffix': sorted(suffix)})
    if exact:
        rules.append({'domain': sorted(exact)})
    if keyword:
        rules.append({'domain_keyword': sorted(keyword)})
    if regexp:
        rules.append({'domain_regex': sorted(regexp)})
    if proc:
        rules.append({'process_name': sorted(proc)})
    for line in skipped:
        print('   ! %s: classical 里这行 sing-box 没有对应写法，已跳过：%s' % (name, line))
    return rules


def build_ipcidr(name):
    cidrs = [l for l in read_lines(BUILD / 'ipcidr' / (name + '.txt'))]
    bad = [l for l in cidrs if not CIDR_RE.match(l)]
    if bad:
        raise SystemExit('!! %s: ipcidr 产物里有非 CIDR 行：%s' % (name, bad[:3]))
    return [{'ip_cidr': sorted(cidrs)}] if cidrs else []


def write_ruleset(kind, name, rules):
    src = BUILD / 'singbox' / kind / (name + '.json')
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(json.dumps({'version': VERSION, 'rules': rules},
                              ensure_ascii=False, indent=1) + '\n', 'utf-8')
    count = sum(len(v) for r in rules for v in r.values())
    out_dir = DIST / 'singbox' / kind
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / (name + '.json')).write_text(src.read_text('utf-8'), 'utf-8')

    srs_note = ''
    if SINGBOX:
        srs = out_dir / (name + '.srs')
        r = subprocess.run([SINGBOX, 'rule-set', 'compile',
                            '--output', str(srs), str(src)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout, r.stderr)
            raise SystemExit('!! %s: sing-box 编译失败' % name)
        srs_note = '  srs %s B' % f'{srs.stat().st_size:,}'
    print('%-22s %6d 条  (%s)%s' % (
        name, count,
        ' + '.join('%s %d' % (k, len(v)) for r in rules for k, v in r.items()),
        srs_note))


def main():
    if not BUILD.exists():
        print('!! 没有 build/ 目录 —— 先跑 scripts/fetch-sources.py')
        return 1
    print('sing-box rule-set version %d%s' % (VERSION, '' if SINGBOX else '（未设 SINGBOX，只出 json 不编译）'))
    total = 0
    for kind, builder in (('domain', build_domain), ('ipcidr', build_ipcidr)):
        d = BUILD / kind
        if not d.is_dir():
            continue
        print('=== %s ===' % kind)
        for src in sorted(d.glob('*.txt')):
            rules = builder(src.stem)
            if not rules:
                print('%-22s 空分类，跳过' % src.stem)
                continue
            write_ruleset(kind, src.stem, rules)
            total += 1
    print('\n共 %d 个 rule-set 写到 %s/singbox/' % (total, DIST.name))
    return 0


if __name__ == '__main__':
    sys.exit(main())
