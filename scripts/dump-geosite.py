#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零依赖 geosite.dat 解析器 —— 用来核对一个 geosite.dat 里到底有什么。

用法:
    python dump_geosite.py geosite.dat                 # 分类清单 + 属性统计
    python dump_geosite.py geosite.dat --cat apple     # 看某个分类的域名类型与属性分布
    python dump_geosite.py geosite.dat --attrs         # 只列各分类出现的 attribute

protobuf 结构（geosite.dat）:
    GeoSiteList { repeated GeoSite entry = 1 }
    GeoSite     { string country_code = 1; repeated Domain domain = 2 }
    Domain      { Type type = 1; string value = 2; repeated string attribute = 3 }
    Type: 0=Plain 1=Regex 2=Domain(后缀) 3=Full
"""
import sys
from collections import Counter, defaultdict

TYPE_NAME = {0: 'Plain', 1: 'Regex', 2: 'Domain(suffix)', 3: 'Full'}


def read_varint(buf, i):
    r = 0
    s = 0
    while True:
        c = buf[i]
        i += 1
        r |= (c & 0x7F) << s
        if not (c & 0x80):
            return r, i
        s += 7


def fields(buf):
    """把一段 protobuf 拆成 [(field_no, wire, value), ...]"""
    i, n, out = 0, len(buf), []
    while i < n:
        key, i = read_varint(buf, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = read_varint(buf, i)
        elif w == 2:
            ln, i = read_varint(buf, i)
            v, i = buf[i:i + ln], i + ln
        elif w == 5:
            v, i = buf[i:i + 4], i + 4
        elif w == 1:
            v, i = buf[i:i + 8], i + 8
        else:
            raise ValueError('unsupported wire type %d at %d' % (w, i))
        out.append((f, w, v))
    return out


def parse(path):
    """返回 {分类: {'n': 条数, 'types': Counter, 'attrs': Counter, 'attr_by_type': {}}}}"""
    with open(path, 'rb') as fh:
        blob = fh.read()
    cats = {}
    for f, w, v in fields(blob):
        if f != 1 or w != 2:
            continue
        code, doms = None, []
        for f2, w2, v2 in fields(v):
            if f2 == 1 and w2 == 2:
                code = v2.decode('utf-8', 'replace')
            elif f2 == 2 and w2 == 2:
                doms.append(v2)
        if code is None:
            continue
        rec = cats.setdefault(code, {'n': 0, 'types': Counter(), 'attrs': Counter(),
                                     'attr_by_type': defaultdict(Counter), 'samples': []})
        for d in doms:
            t, val, attrs = 0, '', []
            for f3, w3, v3 in fields(d):
                if f3 == 1 and w3 == 0:
                    t = v3
                elif f3 == 2 and w3 == 2:
                    val = v3.decode('utf-8', 'replace')
                elif f3 == 3 and w3 == 2:
                    attrs.append(v3.decode('utf-8', 'replace'))
            rec['n'] += 1
            rec['types'][t] += 1
            for a in attrs:
                rec['attrs'][a] += 1
                rec['attr_by_type'][t][a] += 1
            if len(rec['samples']) < 5:
                rec['samples'].append((t, val, attrs))
    return cats


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    cats = parse(path)

    if '--cat' in sys.argv:
        name = sys.argv[sys.argv.index('--cat') + 1]
        rec = cats.get(name)
        if not rec:
            near = [c for c in cats if name in c]
            print('没有分类 %r；相近的有：%s' % (name, near or '（无）'))
            return 1
        print('分类 %s：%d 条' % (name, rec['n']))
        print('  类型分布：', dict(rec['types']))
        print('  属性分布：', dict(rec['attrs']) or '（无任何 attribute）')
        for t, val, attrs in rec['samples']:
            print('  样例  %-14s %-40s %s' % (TYPE_NAME.get(t, t), val, attrs or ''))
        return 0

    if '--attrs' in sys.argv:
        for name in sorted(cats):
            a = cats[name]['attrs']
            print('%-28s %6d 条  属性: %s' % (name, cats[name]['n'], dict(a) or '无'))
        return 0

    print('文件: %s' % path)
    print('分类数: %d  条目总数: %d\n' % (len(cats), sum(r['n'] for r in cats.values())))
    print('%-28s %8s  %-34s %s' % ('分类', '条数', '类型分布', '属性'))
    for name in sorted(cats):
        rec = cats[name]
        types = ' '.join('%s=%d' % (TYPE_NAME.get(k, k), v) for k, v in sorted(rec['types'].items()))
        print('%-28s %8d  %-34s %s' % (name, rec['n'], types, dict(rec['attrs']) or '-'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
