# mihomo 自建规则集

自己用的 mihomo 规则集。源文件都是纯文本，GitHub Actions 每天编译成 `.mrs` 推到 `dist` 分支，客户端从 `dist` 取，改主分支不影响已经在用的配置。

## 目录

```
sources/domain/*.src     来源清单，一行一个上游；文件名即分类名
sources/ipcidr/*.src     同上
sources/exclude.txt      垃圾域名剔除规则
rules/domain/*.txt       手写的例外补丁，behavior: domain
rules/ipcidr/*.txt       手写的例外补丁，behavior: ipcidr
scripts/fetch-sources.py 按 sources/ 拉取、归一化、去冗余、剔垃圾 → build/
scripts/build.sh         CI 用（bash）
scripts/build.ps1        本地用（PowerShell）
scripts/report-sources.py 打印每个分类的条目数和样例
scripts/check-keywords.py 看某个关键词会命中哪些域名，用来查误杀
scripts/check-junk.py    看 junk 是被哪条规则抓的
scripts/dump-geosite.py  解析 geosite.dat，看分类和属性
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
url:https://.../OpenAI.list    # 远端 Clash 格式 list，或一行一个域名或 CIDR 的纯文本
json:https://.../goog.json|prefixes[].ipv4Prefix   # JSON 接口，| 后面是取值路径
asn:2906                       # 按 ASN 取该 AS 公布的所有前缀（RIPE Stat 接口）
asnfile:2906,40027             # 按 ASN 从 iptoasn 离线表反查（批量、不受接口限速）
rir:CN                         # RIR 官方委派记录里该国的全部分配段
ct:netflix.com                 # CT 日志枚举该公司真实签过证书的子域
file:rules/domain/xxx.txt      # 本地文件
```

`json:` 的取值路径用 `.` 分隔字段，`[]` 表示展开数组，顶层就是数组时写 `[].ips`。`asn:` 走 RIPE 的接口，一次一个 AS；`asnfile:` 把整张 iptoasn 表下到本地再查，一次能给多个 AS，也不怕接口限速。`rir:` 默认只查 APNIC，写 `rir:CN,all` 会合并 5 个 RIR（更全，但要多下 4 个文件）。`rir4:` / `rir6:` / `asnfile4:` / `asnfile6:` 可以只要其中一族。

行尾加 `!opt` 来源就变成可选的：拉不到只告警跳过，不让整个分类构建失败。

```
rir:CN                                                    # 自主源，不能失败
url:https://.../gaoyifan/china-operator-ip/.../china.txt !opt   # 交叉源，挂了无所谓
```

## 哪些上游是一手的

第三方规则仓库（blackmatrix7、Loyalsoldier、ispip.clang.cn、gaoyifan…）本质都是对下面这些东西的二次加工，而且随时可能删库停更。能直接采源头的就采源头：

| 前缀 | 数据源 | 为什么死不了 |
|---|---|---|
| `rir:` | APNIC / ARIN / RIPE / LACNIC / AFRINIC 的委派记录 | RIR 是 IP 分配的注册机构本身，所有「中国 IP 库」都派生自它 |
| `asnfile:` | iptoasn 每小时的 ASN ↔ 前缀快照 | 单文件，下到本地可无限次反查，脚本不足 30 行可自建 |
| `ct:` | 证书透明度日志（crt.sh） | 签 TLS 证书必须记录，公司绕不过去，比人工抓包整理的域名表更全 |
| `json:` / `url:` | 厂商自己公布的网段（Google / Cloudflare / Microsoft / Telegram） | 一手且稳定，没有中间商 |

`ct:` 的实测效果：`netflix.com` 能枚举出 630 个子域，`openai.com` 默认深度出 188 条，放到 `maxdepth=3` 是 389 条。

但别指望产物条数跟着涨——归一化的后缀冗余消除会把 `xxx.openai.com` 全部吸进 `+.openai.com` 一条里。CT 真正的价值是**发现新的顶级域**：openai 那次就补出了 `oaistatic.com`、`oaiusercontent.com`、`chatgpt.livekit.cloud` 这些 v2fly 人工分类里没有的域，顺带也验证了父域规则是不是完整的。代价是会掺进 `test` / `dev` / `staging` 这类内部域名，脚本按标签过滤掉了，还嫌脏就调小 `maxdepth`。crt.sh 是免费公共服务，偶尔 502，所以这个来源都该标 `!opt`。

真正采不到的只剩两样：**gfwlist**（要靠墙内外双向探测，是测量不是推导）和**分类语义**（`category-ai-!cn` 里到底装什么，是社区共识）。

`fetch-sources.py` 读这些清单，拉回来之后做三件事：

- 归一化。上游的 `domain:` 转成 `+.x`（含自身和所有子域），`full:` 转成 `x`（精确）。
- 去冗余。被 `+.x` 覆盖的条目直接删掉。
- 剔垃圾。见下面一节。

同名分类如果 `rules/` 和 `build/` 里都有，编译时按顺序拼起来，`build/` 在前、`rules/` 追加在后。

`@cn` 这个属性有实际用处：上游把「国内有接入点」的域名单独标出来，所以 `data:geolocation-!cn @-cn` 拿到的国外域名集合里不含那些域名，不会出现「域名被解析到海外节点、然后 DIRECT 直连」的情况。

keyword 和 regexp 在 `behavior: domain` 里没有对应语法，会另存到 `build/classical/<分类名>.list`，需要的话用 classical provider 引。

## 剔垃圾

`sources/exclude.txt` 里的规则对所有 domain 分类生效，被剔掉的条目会集中到 `build/domain/junk.txt`，客户端可以整集指到 REJECT。

```
~porn        子串命中
!java        子串命中就放行（例外）
+10bet.com   后缀命中，含域名自身
example.com  精确命中
```

按顺序求值，后面的能推翻前面的，所以「先粗筛再放行」写成 `~jav` 后面跟 `!java`。`ads`、`anti-ad`、`junk` 三个分类本身就在装垃圾，不做剔除。

试过用「首标签像随机串」的启发式自动兜那些随机域名，误杀太重就撤了：`appldnld.apple.com`、`360buyimg.com`、`17173cdn.com`、`51jobcdn.com` 这类数字前缀加缩写在国内域名里到处都是。所以现在只留关键词规则，每条都能解释、能 review。要查某条关键词误伤了谁，用 `scripts/check-keywords.py`；要看 junk 是被哪条规则抓的，用 `scripts/check-junk.py`。

## sing-box 输出

同一份文本还会出一套 sing-box 的 rule-set（`dist/singbox/`）。语义和 mihomo 那边严格对齐，**不用退化成 regex**：

| 本仓库写法 | mihomo | sing-box |
|---|---|---|
| `+.x` | `+.x`，命中 x 自身和所有子域 | `{"domain_suffix": ["x"]}` |
| `x` | 只命中 x | `{"domain": ["x"]}` |
| `CIDR` | `IP-CIDR` | `{"ip_cidr": [...]}` |
| `DOMAIN-KEYWORD` / `DOMAIN-REGEX` | 另存 `classical/` | `domain_keyword` / `domain_regex` |

已用 `sing-box rule-set match` 逐条实测确认：`domain_suffix: "a.com"` 命中 `a.com`、`x.a.com`、`x.y.a.com`，不命中 `nota.com`；`domain: "a.com"` 只命中 `a.com`。所以两个内核的匹配行为是一致的。

**为什么强制只允许 `x` 和 `+.x` 两种写法**：sing-box 没有 `.x`（仅子域）和 `*.x`（仅一级子域）的对应语法，一旦出现就只能退化成 regex，两个内核的行为立刻对不上。转换脚本会直接拦下别的写法让构建失败，而不是悄悄转错。

sing-box 这边还比 mihomo 少一个坑：mihomo 的 `behavior: domain` 装不下 keyword / regexp，只能另存 `classical/` 让人自己引；sing-box 有 `domain_keyword` / `domain_regex` 字段，所以转换时直接合并进同一个 `.srs`，客户端引一个文件就够了。

客户端配置：

```json
{
  "route": {
    "rule_set": [
      {
        "tag": "cn",
        "type": "remote",
        "format": "binary",
        "url": "https://cdn.jsdelivr.net/gh/Kauroth/mihomo_Rules@dist/singbox/domain/cn.srs",
        "update_interval": "1d"
      }
    ]
  }
}
```

`format: binary` 读 `.srs`；想直接吃文本就换成 `format: source` 并指向同目录的 `.json`（体积大些，但能直接看）。rule-set 版本默认 **3**（= sing-box 1.11+），客户端更旧就在 workflow 里改 `SINGBOX_RULESET_VERSION`。

## 构建门禁

构建成功不等于数据是对的。上游改格式、把 `@cn` 标注丢了、或者某个源被 `!opt` 跳过，产出的规则集看起来都正常，但内容已经缩水了。所以 CI 里加了两道断言：

**`scripts/check-regression.py`——环比检查**

| 情况 | 判据 |
|---|---|
| 条数突降 | 掉到基准的 60% 以下，且基准本身有 200 条以上 |
| 条数突增 | 涨到 4 倍以上且净增 5000 条以上（通常是被灌了垃圾） |
| 属性退化 | 基准里过滤掉了一批 `@属性` 条目，这次一条都没过滤 —— 上游把属性标注丢了 |
| 分类缺失 / 构建失败 | 直接拦住 |

**`scripts/check-hit.py`——命中回归**

条数环比只证明「产物没缩水」，不证明「规则写得对」。这一步拿真实产物喂 `sing-box rule-set match`，每个分类抽样断言三种映射：`+.x` 必须命中自身和子域、`x` 必须命中且不命中子域、CIDR 必须命中段内地址且不命中 TEST-NET 段。样本直接从 `build/` 的源文本里取，和产物同源 —— 通不过就是转换或编译出了问题。

（mihomo 那边不需要这套：它的规则集是文本直接喂内核的，没有转换环节。有转换风险的是 sing-box 这一侧。）

两道都过不了就 `dist` 停在上一版，客户端不受影响。

**关于失败邮件**：门禁拦住时这个 workflow 会失败，GitHub 会发邮件——**那封信的意思是「数据变化超出预期，需要你确认」，不是构建崩了**。日志和 Step Summary 里会写清楚被拦的原因。

确认是上游的合理变化后，重新跑一次并显式接受：

```bash
gh workflow run build-ruleset.yml -f accept_baseline=true
```

**有意扩大某个分类时（比如第一次接上国内域名表，条数会翻十几倍），注定会失败一次**——这是设计使然，不是可以绕过的。`baseline.json` 只有全部通过才会更新，所以「拦住 → 确认 → 接受」是必经流程。

构建之间有并发保护：同一分支的 run 会排队而不是并行，避免两个都成功的 run 同时 force push `dist` 导致状态错乱。

## 上游快照

每次构建结束，这次拉到的上游原文（`.cache/`，不含 iptoasn 那两个十几 MB 的 `.gz`）会打包成 artifact 保留 14 天，名字是 `upstream-YYYY-MM-DD.tar.gz`。包里带一份 `MANIFEST.json` 记录「哪个哈希文件对应哪个 URL」，拆开就能还原。

两个用处：上游哪天删库跑路，手里还有原文能顶上；门禁拦住了某次构建，可以翻出当天的快照对比，看是哪家变了。

## IP 分流

ipcidr 分类都是现成的，来源要么是 RIR 委派记录自采，要么是官方公布的网段，要么是按 AS 反查：

| 分类 | 来源 | 条数 |
|---|---|---|
| `cn-ip` | `rir:CN`（APNIC 委派记录自采）+ BGP 交叉源 | 8842 |
| `oracle-ip` | AS31898 | 3877 |
| `facebook-ip` | AS32934 | 560 |
| `netflix-ip` | AS2906 / 40027 / 55095（RIPE 主源 + iptoasn 离线表交叉） | 560 |
| `google-ip` | 官方 goog.json | 148 |
| `microsoft-ip` | 微软端点接口 | 93 |
| `steam-ip` | AS32590 | 78 |
| `cloudflare-ip` | 官方两份纯文本 | 22 |
| `telegram-ip` | 官方 cidr.txt | 14 |
| `private-extra` | 手写 | 14 |

IP 规则和域名规则混用时的注意点：

- 域名规则在前、IP 规则在后。反过来会让每个请求都先做一次 DNS 解析。
- IP 规则后面加 `no-resolve`，除非你确实想让内核为了这条规则去解析域名。
- IP 规则是「解析之后才匹配」，所以它兜的是域名规则漏掉的连接（比如直连 IP 的 CDN、QUIC），不能拿它替代域名规则。

```yaml
rules:
  - RULE-SET,private-extra,DIRECT,no-resolve
  - RULE-SET,cn-ip,DIRECT,no-resolve
  - RULE-SET,cn-cdn-extra,DIRECT
  # ... 各平台域名规则
  - RULE-SET,netflix,🎥 奈飞视频
  - RULE-SET,telegram,📲 电报消息
  # ... 域名规则走完，再上 IP 规则
  - RULE-SET,netflix-ip,🎥 奈飞视频,no-resolve
  - RULE-SET,telegram-ip,📲 电报消息,no-resolve
  - RULE-SET,google-ip,🇬 谷歌服务,no-resolve
  - RULE-SET,microsoft-ip,🪟 微软服务,no-resolve
  - RULE-SET,cloudflare-ip,🚀 节点选择,no-resolve
  - RULE-SET,junk,REJECT
  - MATCH,🐟 漏网之鱼
```

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

`push` 到 `main`、改动 `rules/` `sources/` `scripts/` 时触发，每天 03:00（北京时间）也会跑一次。流程是下内核、拉上游、编译、把 `dist/` 强推到 `dist` 分支。

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
