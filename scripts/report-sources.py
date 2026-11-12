# -*- coding: utf-8 -*-
"""打印 build/ 下每个分类的条目数与样例，用来核对「这些规则里都有什么」。"""
import pathlib
import sys

BUILD = pathlib.Path(__file__).resolve().parent.parent / 'build'

for behavior in ('domain', 'ipcidr'):
    d = BUILD / behavior
    if not d.is_dir():
        continue
    print('===== %s =====' % behavior)
    for p in sorted(d.glob('*.txt')):
        lines = [l for l in p.read_text('utf-8').splitlines() if l and not l.startswith('#')]
        n = len(lines)
        try:
            idx = [int(x) for x in sys.argv[1:]] or [0, 1, 2, n // 2, n - 1]
        except ValueError:
            idx = [0, 1, 2, n // 2, n - 1]
        samples = [lines[i] for i in idx if 0 <= i < n]
        print('%-16s %7d  %s' % (p.stem, n, ' | '.join(samples)))
