# mihomo 自建规则集

自己用的 mihomo 规则集。源文件都是纯文本，GitHub Actions 每天编译成 `.mrs` 推到 `dist` 分支，客户端从 `dist` 取，改主分支不影响已经在用的配置。

## 目录

```
sources/domain/*.src     来源清单，一行一个上游；文件名即分类名
sources/ipcidr/*.src     同上
rules/domain/*.txt       手写的例外补丁，behavior: domain
rules/ipcidr/*.txt       手写的例外补丁，behavior: ipcidr
scripts/fetch-sources.py 按 sources/ 拉取、归一化、去冗余 → build/
scripts/build.sh         CI 用（bash）
scripts/build.ps1        本地用（PowerShell）
scripts/dump-geosite.py  解析 geosite.dat，看分类和属性
scripts/report-sources.py 打印每个分类的条目数和样例
config-snippet.yaml      贴进主配置的 rule-providers 片段
build/                   fetch-sources.py 的产物，不入库
dist/                    编译产物，不入库，CI 推到 dist 分支
```

## 两个来源

`rules/` 放手工维护的、量小又需要盯的东西（比如实测过「国内有 CDN」的域名）。分类名就是文件名，一行一条。

`sources/` 放大块的、能从上游拉的东西。一行一个来源：

```
data:telegram                  # 上游 data 目录里的 telegram，自动递归解析 include
data:apple @cn                 # 只要带 @cn 属性的；@-ads 是排除
url:https://.../OpenAI.list    # 远端 Clash 格式 list，或一行一个域名的纯文本
file:rules/domain/xxx.txt      # 本地文件
```

`fetch-sources.py` 读这些清单，拉回来之后做两件事：

- 归一化。上游的 `domain:` 转成 `+.x`（含自身和所有子域），`full:` 转成 `x`（精确）。
- 去冗余。被 `+.x` 覆盖的条目直接删掉。`proxy` 这一项从 26732 条压到 23703 条。

同名分类如果 `rules/` 和 `build/` 里都有，编译时按顺序拼起来，`build/` 在前、`rules/` 追加在后。

`@cn` 这个属性有实际用处：上游把「国内有接入点」的域名单独标出来，所以 `data:geolocation-!cn @-cn` 拿到的国外域名集合里不含那些域名，不会出现「域名被解析到海外节点、然后 DIRECT 直连」的情况。

keyword 和 regexp 在 `behavior: domain` 里没有对应语法，会另存到 `build/classical/<分类名>.list`，需要的话用 classical provider 引。

## 文本格式

内核版本按 v1.19.12 实测。注释行和空行都会被忽略，可以放心写。

domain 类型里几种写法的实际行为：

| 写法 | 匹配 |
|---|---|
| `example.com` | 只有 `example.com` 本身 |
| `+.example.com` | `example.com`、`sub.example.com`、`a.b.example.com` |
| `.example.com` | 任意层级子域，但不含 `example.com` 本身 |
| `*.example.com` | 只有一级子域，不含 `example.com`，也不含 `a.b.example.com` |
| `..example.com` | 无效，内核丢弃并报 `invalid domain` |

写后缀统一用 `+.`。`.x` 和 `*.x` 在 sing-box 里没有对应写法，一旦用了，两个内核的行为就对不上。

classical 类型一行一条完整规则，除了 rule-set 和 sub-rule 什么都能放：

```
DOMAIN-SUFFIX,google.com
DOMAIN-KEYWORD,kwtest
IP-CIDR,1.2.3.0/24,no-resolve
PROCESS-NAME,qbittorrent.exe
```

逐条匹配，比 domain / ipcidr 慢，只在必须混装的时候用。

## 本地构建

```powershell
# 拉上游，生成 build/
python scripts\fetch-sources.py

# 编译。第一次加 -FetchCore 会自动下内核到 .tools\
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -FetchCore

# 用机器上已有的内核
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Mihomo "D:\path\to\mihomo.exe"
```

想单独跑一个分类，把名字接在后面：`python scripts\fetch-sources.py telegram cn`。

`report-sources.py` 会列出每个分类的条目数和几条样例，用来核对产物对不对。`dump-geosite.py` 用来分析别人的 geosite.dat，看里面有哪些分类、有没有属性。

反解校验：

```powershell
mihomo convert-ruleset domain mrs dist\domain\proxy.mrs dump.yaml
```

## CI

`push` 到 `Kauroth` 分支、改动 `rules/` `sources/` `scripts/` 时触发，每天 03:00（北京时间）也会跑一次。流程是下内核、拉上游、编译、把 `dist/` 强推到 `dist` 分支。

## 客户端引用

见 `config-snippet.yaml`。几个注意点：

- 用 jsDelivr：`https://cdn.jsdelivr.net/gh/Kauroth/mihomo_Rules@dist/domain/xxx.mrs`。raw.githubusercontent.com 在国内经常拉不动。
- 非要拉 raw 就得给 provider 指定出口（`proxy: 节点选择`），不写这一项走 DIRECT，会出现「网能上但规则集永远不更新」。
- `path` 必须落在 mihomo 的 HomeDir（`-d` 指定）里，否则要设 `SAFE_PATHS`。
- jsDelivr 对分支引用有缓存。改完发现客户端拿到的还是旧内容，去 purge 页面刷一下，或者临时换 raw URL 验证。
- 只有几个域名的小例外不值得进仓库，用 `type: inline` + `payload` 写在主配置里（mihomo ≥ v1.19.1）。

## 几个坑

**`+` 后面必须跟 `.`。** 写成 `+cn.bing.com` 的时候，v1.19.12 会把它当废条目静默吞掉，v1.19.33 以上报 `"+" wildcard must occupy the entire label 1`。CI 用的内核版本比本地新，就是靠它把这个 typo 抓出来的。

**非法行只 warning，不报错。** `convert-ruleset` 遇到非法条目打一条 warning 然后跳过，退出码仍然是 0。两个构建脚本都会 grep `invalid` 并主动失败，否则坏行会静默进 `dist`。

**classical 不要编译成 mrs。** v1.19.25 上 `convert-ruleset classical text` 必定 panic（`classical_strategy.go:57`），而且 panic 之后退出码还是 0。所以 classical 只出文本，脚本里把 `panic` 和 `invalid` 一起 grep。

**mrs 跨版本。** v1.19.33 生成的 mrs 能被 v1.19.12 正常读取，反过来也可以，命中结果一致。跨大版本没测过，保守做法是用接近客户端内核的版本生成；拿不准就直接用 `format: text`，同一份列表两种格式的命中结果是一样的。

**PowerShell 5.1 读 .ps1 的编码。** Windows PowerShell 5.1 读无 BOM 的 `.ps1` 会按 GBK 解析，中文注释会把脚本解析坏。`build.ps1` 存的是 UTF-8 with BOM，改它的时候别把 BOM 去掉。

## 和别的规则集混用时的顺序

如果同时引了别的集合，注意它们里面会有 `+.apple.com`、`+.microsoft.com`、`+.live.com` 这类父域。这些父域一旦先命中，本仓库里分好的「国内 CDN 直连」就全部失效。直连集合要排在前面，`cn-cdn-extra` 就是为这个准备的：

```yaml
rules:
  - RULE-SET,private-extra,DIRECT,no-resolve
  - RULE-SET,cn-ip,DIRECT,no-resolve
  - RULE-SET,cn-cdn-extra,DIRECT
  - RULE-SET,apple-cn,DIRECT
  - RULE-SET,microsoft-cn,DIRECT
  - RULE-SET,google-cn,DIRECT
  - RULE-SET,steam-cn,DIRECT
  - RULE-SET,cn,DIRECT
  # 之后才是各平台策略组，最后 MATCH
```
