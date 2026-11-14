#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比 build/report.json 和 baseline.json，拦住「上游悄悄变质」。

为什么需要它
------------
构建成功 ≠ 数据是对的。上游常见几种坏法，都不会让构建失败：

  1. 改格式 / 删分类 —— 产物还在，但只剩几十条，客户端照用，分流全错
  2. 把 @cn 这类属性标注丢了（退化成 Clash 格式）—— 产物看着正常，
     但 `data:geolocation-!cn @-cn` 这类过滤悄悄失效，国内域名被塞进 proxy
  3. 某个源挂掉被 !opt 跳过 —— 覆盖缩水，日志里只是一行告警

所以必须有环比断言：涨跌超阈值就不发布，宁可让 dist 停在上一版。

用法：
    python3 scripts/check-regression.py            # 检查（CI 里用这个）
    python3 scripts/check-regression.py --update   # 只重建基准，不检查
"""
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORT = ROOT / 'build' / 'report.json'
BASELINE = ROOT / 'baseline.json'

# 阈值。误报了就调这里，别绕过检查。
DROP_RATIO = 0.6      # 掉到基准 60% 以下 → 异常
DROP_MIN = 200        # 且基准至少要有这么多条（小分类本来就会抖）
RISE_RATIO = 4.0      # 涨到 4 倍以上
RISE_MIN = 5000       # 且净增超过 5000 条 → 异常（通常是被塞了垃圾）
ATTR_MIN = 5          # 基准过滤条数少于这个就不查属性退化（小分类噪音大）


def load(path):
    try:
        return json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return None


def save_baseline(rep):
    """写基准。刻意不带时间戳字段，否则每天都会产生一次无意义的提交。"""
    BASELINE.write_text(json.dumps({
        'total': rep.get('total', 0),
        'categories': rep.get('categories', {}),
    }, ensure_ascii=False, indent=2, sort_keys=True) + '\n', 'utf-8')


def main():
    argv = sys.argv[1:]
    rep = load(REPORT)
    if rep is None:
        print('!! 找不到 build/report.json，要先跑 scripts/fetch-sources.py')
        return 1
    cats = rep.get('categories', {})
    baseline_raw = load(BASELINE)
    base = (baseline_raw or {}).get('categories', {})

    if '--update' in argv:
        save_baseline(rep)
        print('基准已重建：%d 个分类，共 %d 条' % (len(cats), rep.get('total', 0)))
        return 0

    problems, notes, changed = [], [], []

    for f in rep.get('failed', []):
        problems.append('分类构建失败：%s' % f)

    for key in sorted(set(base) | set(cats)):
        b, n = base.get(key), cats.get(key)
        if n is None:
            problems.append('%s 这次没产出来（基准 %d 条）' % (key, b['count']))
            continue
        if b is None:
            notes.append('%s 是新分类：%d 条' % (key, n['count']))
            continue
        bc, nc = b['count'], n['count']
        if bc != nc:
            changed.append((key, bc, nc, nc - bc))
        if bc >= DROP_MIN and nc < bc * DROP_RATIO:
            problems.append('%s 条数突降：%d → %d（-%d%%）'
                            % (key, bc, nc, round((1 - nc / bc) * 100)))
        if bc > 0 and nc > bc * RISE_RATIO and nc - bc > RISE_MIN:
            problems.append('%s 条数突增：%d → %d（+%d%%），查上游是不是被塞了垃圾'
                            % (key, bc, nc, round((nc / bc - 1) * 100)))
        battr, nattr = b.get('attr_skipped', 0), n.get('attr_skipped', 0)
        if battr >= ATTR_MIN and nattr == 0:
            problems.append('%s 的属性过滤失效：基准过滤掉 %d 条，现在 0 条'
                            '（上游可能把 @属性 标注丢了）' % (key, battr))

    lines = []
    if not base:
        lines.append('首次运行，没有基准可比 —— 这次的结果会作为基准。')
    else:
        lines.append('基准 %d 个分类 / %d 条，本次 %d 个分类 / %d 条'
                     % (len(base), baseline_raw.get('total', 0), len(cats), rep.get('total', 0)))
    if changed:
        lines.append('')
        lines.append('| 分类 | 基准 | 本次 | 增减 |')
        lines.append('|---|---:|---:|---:|')
        for key, bc, nc, d in sorted(changed, key=lambda r: -abs(r[3])):
            lines.append('| `%s` | %d | %d | %+d |' % (key, bc, nc, d))
    else:
        lines.append('所有分类条数与基准一致。')
    if notes:
        lines.append('')
        lines.append('注意：' + '；'.join(notes))

    if problems:
        lines.append('')
        lines.append('**拦住 %d 个问题：**' % len(problems))
        for p in problems:
            lines.append('- %s' % p)
        lines.append('')
        lines.append('---')
        lines.append('')
        lines.append('这次失败是**门禁在正常工作，不是构建崩了** —— GitHub 会因此发一封失败邮件，')
        lines.append('那封信的意思是「数据变化超出预期，需要你确认」。')
        lines.append('')
        lines.append('`dist` 不会更新，客户端继续用上一版。确认是上游的合理变化后，')
        lines.append('用 workflow_dispatch 勾选 `accept_baseline` 重新跑一次即可（会打印一条 warning 并重建基准）。')
        lines.append('本地确认：`python3 scripts/check-regression.py --update`。')

    body = '\n'.join(lines)
    print('=== 环比检查 ===')
    print(body)

    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as fh:
            fh.write('## 环比检查\n\n%s\n' % body)

    if problems:
        return 1
    save_baseline(rep)
    return 0


if __name__ == '__main__':
    sys.exit(main())
