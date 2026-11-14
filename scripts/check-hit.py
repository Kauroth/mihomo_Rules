#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命中回归：拿真实产物喂 `sing-box rule-set match`，断言匹配语义和预期一致。

为什么需要它
------------
条数环比只证明「产物没缩水」，不证明「规则写得对」。这个脚本回答的是另一个问题：
把一个域名喂进去，到底会不会命中？覆盖三种容易写错的映射：

    +.x    →  domain_suffix   必须命中 x 自身、x 的子域，不能命中 not-x
    x      →  domain          必须命中 x，不能命中子域 x.sub
    CIDR   →  ip_cidr         必须命中段内地址，不能命中 TEST-NET 段

抽样跑（每个分类取前几条），全量会太慢。样本直接从 build/ 的源文本里取，
和产物同源，所以只要转换逻辑对就必然通过 —— 通不过就是转换或编译出了问题。

mihomo 那边不需要这套：它的规则集是文本直接喂给内核的，没有转换环节。
真正有转换风险的是 sing-box 这一侧。

用法：
    SINGBOX=/path/to/sing-box python3 scripts/check-hit.py
"""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / 'build'
DIST = pathlib.Path(os.environ.get('DIST') or ROOT / 'dist')
SINGBOX = os.environ.get('SINGBOX', '')
SAMPLE = 3                       # 每个分类抽几条
SUFFIX_PREFIX = '+.'
# TEST-NET-2/-3，文档专用段，不会出现在任何真实 IP 列表里
NEG_IPS = ['198.51.100.7', '203.0.113.7']


def read_lines(path):
    out = []
    for line in path.read_text('utf-8', 'replace').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            out.append(line)
    return out


def matches(kind, name, value):
    """跑一次 sing-box rule-set match，返回是否命中。

    注意：命中信息是 sing-box 当日志打到 **stderr** 的（`match rules.[0]: ...`），
    stdout 始终为空；不命中则两边都空。所以判空要把两路合起来看 —— 只看 stdout
    会得出「全都不命中」的错误结论（这个坑踩过了）。
    """
    srs = DIST / 'singbox' / kind / (name + '.srs')
    r = subprocess.run([SINGBOX, 'rule-set', 'match', '-f', 'binary', str(srs), value],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('match 执行失败 %s %s: %s' % (name, value, r.stderr.strip()))
    return bool((r.stdout + r.stderr).strip())


def suffix_covers(lines, domain):
    """本地判断 `+.x` 规则是否会覆盖这个域名（只看 domain 规则，不含 keyword/regexp）。"""
    for l in lines:
        if l.startswith(SUFFIX_PREFIX):
            s = l[len(SUFFIX_PREFIX):]
            if domain == s or domain.endswith('.' + s):
                return True
    return False


def neg_probe(name):
    """.invalid 是保留 TLD，不可能出现在任何真实规则集里 —— 用它验证「不过度匹配」。"""
    return '%s-negative-probe.invalid' % name


def main():
    if not SINGBOX:
        print('!! 没有设 SINGBOX，跳不过命中回归就没意义了')
        return 1
    fails, checked = [], 0

    print('=== domain ===')
    for src in sorted((BUILD / 'domain').glob('*.txt')):
        name = src.stem
        lines = read_lines(src)
        suffix = [l[len(SUFFIX_PREFIX):] for l in lines if l.startswith(SUFFIX_PREFIX)][:SAMPLE]
        exact = [l for l in lines if not l.startswith(SUFFIX_PREFIX)][:SAMPLE]
        n = skipped = 0

        # +.x 必须命中自身和子域
        for x in suffix:
            for probe in (x, 'www.' + x):
                checked += 1
                n += 1
                if not matches('domain', name, probe):
                    fails.append('%s: %s ← 来自 +.%s，期望命中，实际不命中' % (name, probe, x))

        # x（精确）必须命中自身；子域不该命中 —— 但同分类里若有 +.父域 覆盖它，
        # 这条反例就不成立（不是 bug，是规则本身更宽），跳过而不是误报。
        for x in exact:
            checked += 1
            n += 1
            if not matches('domain', name, x):
                fails.append('%s: %s ← exact 规则，期望命中，实际不命中' % (name, x))
            sub = 'sub.' + x
            if suffix_covers(lines, sub):
                skipped += 1
            else:
                checked += 1
                n += 1
                if matches('domain', name, sub):
                    fails.append('%s: %s ← exact 规则不该匹配子域，实际命中了' % (name, sub))

        # 分类级反例：确认规则集不是「什么都匹配」。
        # 有些分类本身就覆盖了探测域名 —— private 里有 `invalid` / `test` 这些保留 TLD，
        # 所以 `xxx.invalid` 命中是正常行为，不算问题。以本地规则判定为准：
        # 本地说不该覆盖、实际却命中了，才是转换出错（比如字段写成了匹配一切）。
        neg = neg_probe(name)
        if suffix_covers(lines, neg):
            skipped += 1
        else:
            checked += 1
            n += 1
            if matches('domain', name, neg):
                fails.append('%s: %s 期望不命中，实际命中（规则集过度匹配）' % (name, neg))

        bad = any(f.startswith(name + ':') for f in fails)
        print('  %s %-22s %d 项%s' % ('⚠️' if bad else '✅', name, n,
                                      '（%d 条反例因同分类有更宽的规则而跳过）' % skipped if skipped else ''))

    print('=== ipcidr ===')
    import ipaddress
    for src in sorted((BUILD / 'ipcidr').glob('*.txt')):
        name = src.stem
        lines = read_lines(src)
        nets = []
        for l in lines:
            try:
                nets.append(ipaddress.ip_network(l))
            except ValueError:
                pass
        n = 0
        for net in nets[:SAMPLE]:
            for probe, want in ((str(net.network_address), True),
                                (str(net.broadcast_address), True)):
                checked += 1
                got = matches('ipcidr', name, probe)
                n += 1
                if got != want:
                    fails.append('%s: %s 期望命中，实际不命中（%s）' % (name, probe, net))
        for probe in NEG_IPS:
            checked += 1
            got = matches('ipcidr', name, probe)
            n += 1
            if got:
                fails.append('%s: %s 期望不命中，实际命中' % (name, probe))
        bad = any(f.startswith(name + ':') for f in fails)
        print('  %s %-22s %d 项' % ('⚠️' if bad else '✅', name, n))

    print('\n共检查 %d 项' % checked)
    if fails:
        print('\n!! 命中回归失败 %d 项：' % len(fails))
        for f in fails[:20]:
            print('   -', f)
        if len(fails) > 20:
            print('   ... 还有 %d 项' % (len(fails) - 20))
        return 1
    print('全部通过：sing-box 的 domain_suffix / domain / ip_cidr 语义与预期一致')
    return 0


if __name__ == '__main__':
    sys.exit(main())
