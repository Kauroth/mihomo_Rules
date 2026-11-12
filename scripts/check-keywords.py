# -*- coding: utf-8 -*-
"""候选关键词的假阳性检查：打印每个词命中的域名，人工挑出误伤。

用法：python scripts/check-keywords.py bet jav sex cum slot
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = 'proxy'


def load(name):
    p = ROOT / 'build' / 'domain' / (name + '.txt')
    return [l[2:] if l.startswith('+.') else l
            for l in p.read_text('utf-8').splitlines() if l and not l.startswith('#')]


def main():
    words = sys.argv[1:]
    if not words:
        print(__doc__)
        return 1
    hosts = load(TARGET)
    print('列表 %s：%d 条\n' % (TARGET, len(hosts)))
    for w in words:
        hit = [h for h in hosts if w in h.lower()]
        print('== %s：%d 条 ==' % (w, len(hit)))
        for h in sorted(hit)[:60]:
            print('   ', h)
        if len(hit) > 60:
            print('    ... 另有 %d' % (len(hit) - 60))
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
