# -*- coding: utf-8 -*-
"""看 junk.txt 是被哪些规则抓进来的：每条规则命中多少，并打印样例，用来查误杀。"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
junk = [l for l in (ROOT / 'build' / 'domain' / 'junk.txt').read_text('utf-8').splitlines()
        if l and not l.startswith('#')]
hosts = [l[2:] if l.startswith('+.') else l for l in junk]

rules = []
for raw in (ROOT / 'sources' / 'exclude.txt').read_text('utf-8').splitlines():
    line = raw.split('#', 1)[0].strip()
    if line and line[0] in '~!' and not line.startswith('!.'):
        rules.append(line)

print('junk 共 %d 条\n' % len(hosts))
sample = int(sys.argv[1]) if len(sys.argv) > 1 else 8
for rule in rules:
    word = rule[1:].lower()
    hit = sorted(h for h in hosts if word in h)
    print('%-16s %5d   %s' % (rule, len(hit), ' | '.join(hit[:sample])))
