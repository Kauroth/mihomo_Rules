# mihomo 自建规则集

给 mihomo / sing-box 用的分流规则集。源文件都是纯文本，GitHub Actions 每天 03:00（北京时间）自动编译成 `.mrs`（mihomo）和 `.srs`（sing-box）推到 `dist` 分支——客户端从 `dist` 取，改主分支不影响正在用的配置。

现在的规模：**51 个 domain 分类 + 10 个 ipcidr 分类**，另外出一套 sing-box 的 rule-set。

几个特点：

- **IP 侧自采**。中国 IP 直接从 APNIC 官方委派记录生成，按 AS 反查走 iptoasn 离线表。第三方「中国 IP 库」本质都是对这些的二次加工，能采源头就不引二传手。
- **有质量门禁**。构建成功不等于数据是对的——条数突降突增、属性标注丢失、规则转换错误，都会被拦下并且不发布，`dist` 停在上一版。
- **两个内核语义对齐**。同一份文本出 `.mrs` 和 `.srs`，映射是一比一，不用退化成 regex。

## 快速开始

最小可用配置（5 个集合）：

```yaml
rule-providers:
  my-private:
    {type: http, behavior: domain, format: mrs, interval: 86400, proxy: DIRECT,
     url: "https://cdn.jsdelivr.net/gh/Kauroth/mihomo_Rules@dist/domain/private.mrs",
     path: ./ruleset/private.mrs}
  my-ads:
    {type: http, behavior: domain, format: mrs, interval: 86400, proxy: DIRECT,
     url: "https://cdn.jsdelivr.net/gh/Kauroth/mihomo_Rules@dist/domain/ads.mrs",
     path: ./ruleset/ads.mrs}
  my-cn:
    {type: http, behavior: domain, format: mrs, interval: 86400, proxy: DIRECT,
     url: "https://cdn.jsdelivr.net/gh/Kauroth/mihomo_Rules@dist/domain/cn.mrs",
     path: ./ruleset/cn.mrs}
  my-proxy:
    {type: http, behavior: domain, format: mrs, interval: 86400, proxy: 节点选择,
     url: "https://cdn.jsdelivr.net/gh/Kauroth/mihomo_Rules@dist/domain/proxy.mrs",
     path: ./ruleset/proxy.mrs}
  my-cn-ip:
    {type: http, behavior: ipcidr, format: mrs, interval: 86400, proxy: DIRECT,
     url: "https://cdn.jsdelivr.net/gh/Kauroth/mihomo_Rules@dist/ipcidr/cn-ip.mrs",
     path: ./ruleset/cn-ip.mrs}

rules:
  - RULE-SET,my-private,DIRECT
  - RULE-SET,my-ads,REJECT
  - RULE-SET,my-cn,DIRECT                  # ← 必须在 my-proxy 之前
  - RULE-SET,my-proxy,🚀 节点选择
  - RULE-SET,my-cn-ip,DIRECT,no-resolve    # IP 兜底
  - MATCH,🚀 节点选择
```

要流媒体 / AI 分流、按平台拆策略组，抄 `config-snippet.yaml`（23 个集合，按用途分好组、顺序标好）。

**`my-cn` 一定要排在 `my-proxy` / `my-gfw` 前面。** 实测这两集有 214 条重叠（`+.aaplimg.com` 这类「国内有 CDN 的国外服务」），谁在前谁赢——`cn` 在前走直连才是对的。

## 分类清单

### domain（51 个）

| 类别 | 分类 | 条数 | 说明 |
|---|---|---:|---|
| **国内** | `cn` | 110,474 | 国内域名总集合 |
| | `cn-lite` | 6,265 | 只要 v2fly 社区分类，路由器/小内存用 |
| | `cn-cdn-extra` | 24 | 手写：实测有国内 CDN、必须直连的域名 |
| | `apple-cn` `google-cn` `microsoft-cn` `samsung-cn` `steam-cn` `games-cn` | 189 / 121 / 241 / 4 / 17 / 418 | 各国外服务的国内部分 |
| **国外** | `proxy` | 24,237 | 国外域名总集合 |
| | `gfw` | 4,337 | 被墙域名清单 |
| | `tld-proxy` | 824 | 国外顶级域名（**别直接引，见下**） |
| | `ai` | 231 | AI 平台 |
| **平台** | `apple` `google` `microsoft` `samsung` | 1584 / 846 / 640 / 27 | 各厂商全家桶 |
| | `github` `gitlab` `discord` `facebook` `instagram` `twitter` `whatsapp` | 60 / 5 / 28 / 390 / 72 / 24 / 11 | |
| | `openai` `anthropic` `telegram` `onedrive` | 24 / 21 / 21 / 11 | |
| **流媒体** | `media` | 1307 | 国外流媒体总集（与下面各集重叠） |
| | `netflix` `youtube` `disney` `hbo` `primevideo` `appletv` `spotify` `tiktok` | 24 / 177 / 222 / 65 / 24 / 8 / 25 / 35 | 按平台细分 |
| | `bilibili` | 120 | 哔哩哔哩 |
| **游戏** | `games` | 938 | 海外游戏平台 |
| | `epicgames` `steam` | 30 / 60 | |
| **工具** | `private` | 139 | 保留域 + 在线面板 |
| | `fakeip-filter` | 485 | NTP/STUN，必须走 real-ip |
| | `networktest` | 2009 | 测速站 / IP 归属检测 |
| | `trackerslist` | 88 | BT tracker |
| | `applications` | 1 | 应用下载域名（进程名部分在 `classical/`） |
| **垃圾** | `ads` | 904 | 广告（v2fly 的 category-ads-all） |
| | `anti-ad` | 102,695 | anti-AD 主列表完整版 |
| | `junk` | 2483 | 从各分类里剔出来的，可以整集 REJECT |
| **手写补丁** | `private-extra` | 7 | 主流在线面板域名 |
| | `fakeip-filter-extra` | 16 | |

### ipcidr（10 个）

| 分类 | 来源 | 条数 |
|---|---|---:|
| `cn-ip` | `rir:CN`（APNIC 官方委派记录自采）+ BGP 交叉源 | 12,755 |
| `oracle-ip` | AS31898 | 3,881 |
| `netflix-ip` | AS2906 / 40027 / 55095（RIPE 主源 + iptoasn 离线表交叉） | 1,101 |
| `facebook-ip` | AS32934 | 560 |
| `google-ip` | 官方 `goog.json` | 148 |
| `microsoft-ip` | 微软端点接口 | 93 |
| `steam-ip` | AS32590 | 78 |
| `cloudflare-ip` | 官方两份纯文本 | 19 |
| `private-extra` | 手写 | 14 |
| `telegram-ip` | 官方 `cidr.txt` | 13 |

### classical（18 个，只出文本）

`ads` `ai` `applications` `bilibili` `cn` `cn-lite` `disney` `epicgames` `games` `games-cn` `google` `google-cn` `media` `netflix` `networktest` `openai` `private` `proxy`

装的是 `DOMAIN-KEYWORD` / `DOMAIN-REGEX` / `PROCESS-NAME` 这些 **`behavior: domain` 装不下的东西**。需要就用 `behavior: classical` 的 provider 引，不引也不影响。

### 两个「看着奇怪」的分类

**`applications` 只有 1 条域名**——因为它上游（clash-rules 的 `applications.txt`）是 98 条 `PROCESS-NAME,frpc` 这种**进程名**规则，一条域名都没有。能进 domain 规则集的域名来自另一个源；进程名那部分落在 `classical/applications.list`。分开是对的，别以为是漏了。

**`tld-proxy` 824 条，但建议别引**——它是「按顶级域名」判定的（`.io` / `.me` / `.gg` 这类），粒度极粗。一旦指向代理，**国内的 `.io` / `.me` 站点也会被带走**。数据放在这儿，用不用由你决定。

## 目录结构

```
sources/domain/*.src        来源清单，一行一个上游；文件名即分类名
sources/ipcidr/*.src        同上
sources/exclude.txt         垃圾域名剔除规则
rules/domain/*.txt          手写的例外补丁（private-extra / cn-cdn-extra / fakeip-filter-extra）
rules/ipcidr/*.txt          同上
docs/roadmap.md             设计决定与取舍的记录

scripts/fetch-sources.py    按 sources/ 拉取、归一化、去冗余、剔垃圾 → build/
scripts/build.sh            编译 mihomo 的 .mrs（CI 用）
scripts/build-singbox.py    编译 sing-box 的 .srs + .json
scripts/build.ps1           本地用（PowerShell）
scripts/check-regression.py 环比门禁：条数突降突增、属性退化就拦
scripts/check-hit.py        命中回归：用 sing-box rule-set match 验匹配语义
scripts/report-sources.py   打印每个分类的条目数和样例
scripts/check-keywords.py   看某个关键词会命中哪些域名，用来查误杀
scripts/check-junk.py       看 junk 是被哪条规则抓的
scripts/dump-geosite.py     解析 geosite.dat，看分类和属性

config-snippet.yaml         贴进主配置的 rule-providers 片段
baseline.json               环比基准，CI 自动维护
build/                      fetch-sources.py 的产物，不入库
dist/                       编译产物，不入库，CI 强推到 dist 分支
```

## 数据来源

### sources/ 的写法

`sources/` 放大块的、能从上游拉的东西，一行一个来源；`rules/` 放手工维护的、量小又需要盯的东西（分类名就是文件名）。同名分类两边都有的话会被拼起来，`build/` 在前、`rules/` 追加在后。

```
data:telegram                  # 上游 data 目录里的 telegram，自动递归解析 include
data:apple @cn                 # 只要带 @cn 属性的；@-ads 是排除
url:https://.../OpenAI.list    # 远端 Clash list，或一行一个域名 / CIDR 的纯文本
json:https://.../goog.json|prefixes[].ipv4Prefix   # JSON 接口，| 后面是取值路径
asn:2906                       # 按 ASN 取前缀（RIPE Stat 接口）
asnfile:2906,40027             # 按 ASN 从 iptoasn 离线表反查（批量、不限速）
rir:CN                         # RIR 官方委派记录里该国的全部分配段
ct:netflix.com                 # CT 日志枚举该公司签过证书的子域
file:rules/domain/xxx.txt      # 本地文件
```

`json:` 的取值路径用 `.` 分隔字段，`[]` 表示展开数组，顶层就是数组时写 `[].ips`。`rir:` 默认只查 APNIC，`rir:CN,all` 合并 5 个 RIR（更全，多下 4 个文件）；`rir4:` / `rir6:` / `asnfile4:` / `asnfile6:` 可以只要一族。

**行尾加 `!opt` 就变成可选来源**：拉不到只告警跳过，不让整个分类构建失败。

```
rir:CN                                                          # 自主源，不该失败
url:https://.../china-operator-ip/ip-lists/china.txt !opt        # 交叉源，挂了无所谓
```

### 哪些是一手的

第三方规则仓库（blackmatrix7、Loyalsoldier、ispip.clang.cn、gaoyifan…）本质都是对下面这些的二次加工，而且随时可能删库停更。能采源头的就采源头：

| 前缀 | 数据源 | 为什么死不了 |
|---|---|---|
| `rir:` | APNIC / ARIN / RIPE / LACNIC / AFRINIC 的委派记录 | RIR 是 IP 分配的注册机构本身，所有「中国 IP 库」都派生自它 |
| `asnfile:` | iptoasn 每小时的 ASN ↔ 前缀快照 | 单文件，下到本地可无限次反查 |
| `ct:` | 证书透明度日志（crt.sh） | 签 TLS 证书必须记录，公司绕不过去 |
| `json:` / `url:` | 厂商自己公布的网段（Google / Cloudflare / Microsoft / Telegram） | 一手且稳定，没有中间商 |

**`cn-ip` 换自采之后的实测效果**：拿 RIPE 的 CN 权威列表（8,815 条）当基准，按地址覆盖率算，未覆盖的国内段从 **403 段 / 931 万地址（2.71%）降到 13 段 / 1 万地址（0.005%）**。剩下那 13 段基本是中国公司在海外 RIR 注册的（`91.233.17.0`、`178.236.224.0` 这些），因为 `rir:CN` 只查 APNIC。

**`ct:` 的坦白话**：它在当前配置里**对产物零贡献**。枚举出的子域全被已有的 `+.主域` 吸收了——`openai.com` 实测枚举 188 条，逐行 diff 后新增 0 条。它真正能发力的场景是「v2fly 没有对应分类」或「服务域挂在不同顶级域下」。留着是因为成本低（一个 `!opt` 源），但别指望它扩容。crt.sh 是免费公共服务，偶尔 502，所以这个源都标 `!opt`。

**采不到的只剩两样**：`gfwlist`（要靠墙内外双向探测，是测量不是推导）和**分类语义**（`category-ai-!cn` 里到底装什么，是社区共识）。

### 拉回来之后做三件事

- **归一化**：上游的 `domain:` → `+.x`（含自身和所有子域），`full:` → `x`（精确）。
- **去冗余**：被 `+.x` 覆盖的条目直接删掉。
- **剔垃圾**：见下面一节。

`@cn` 这个属性有实际用处：上游把「国内有接入点」的域名单独标出来，所以 `data:geolocation-!cn @-cn` 拿到的国外域名集合里不含那些域名，不会出现「域名解析到海外节点、然后 DIRECT 直连」。

## 剔垃圾

`sources/exclude.txt` 的规则对所有 domain 分类生效，被剔掉的条目集中到 `build/domain/junk.txt`，客户端可以整集指到 REJECT。

```
~porn        子串命中
!java        子串命中就放行（例外）
+10bet.com   后缀命中，含域名自身
example.com  精确命中
```

按顺序求值，后面的能推翻前面的，所以「先粗筛再放行」写成 `~jav` 后面跟 `!java`。`ads`、`anti-ad`、`junk` 三个分类本身就在装垃圾，不做剔除。

试过用「首标签像随机串」的启发式自动兜随机域名，误杀太重就撤了：`appldnld.apple.com`、`360buyimg.com`、`17173cdn.com`、`51jobcdn.com` 这类数字前缀加缩写在国内域名里到处都是。所以现在只留关键词规则，每条都能解释、能 review。查某条关键词误伤了谁用 `check-keywords.py`，看 junk 是被哪条规则抓的用 `check-junk.py`。

## 构建与发布

### CI 流程

`push` 到 `main`（改动 `rules/` `sources/` `scripts/`）+ 每天 03:00（北京时间）+ 手动触发：

```
下载 mihomo 内核（v1.19.34）
下载 sing-box 内核（v1.14.4）
  ↓
按 sources/ 拉上游并归一化          → build/report.json
  ↓
环比检查（门禁）                     ← 拦住就不往下走，dist 保持旧版
  ↓
快照原始上游                         → artifact 留 14 天
  ↓
编译 mihomo 的 .mrs
编译 sing-box 的 .srs + .json
  ↓
命中回归（sing-box 匹配语义）
  ↓
强推 dist 分支
  ↓
更新环比基准                         → 机器人自动提交 baseline.json
  ↓
刷新 jsDelivr 缓存（118 个文件）
```

同分支的 run **排队而不是并行**（`concurrency`），避免两个都成功的 run 同时 force push `dist` 导致状态错乱。

### 质量门禁

构建成功不等于数据是对的。上游改格式、把 `@cn` 标注丢了、某个源被 `!opt` 跳过——产出的规则集看起来都正常，但内容已经缩水了。所以有两道断言：

**① 环比检查**（`check-regression.py`）

| 情况 | 判据 |
|---|---|
| 条数突降 | 掉到基准的 60% 以下，且基准本身有 200 条以上 |
| 条数突增 | 涨到 4 倍以上**且**净增 5000 条以上（两个条件都满足才拦） |
| 属性退化 | 基准里过滤掉过 `@属性` 条目，这次一条都没过滤——上游把属性标注丢了 |
| 分类缺失 / 构建失败 | 直接拦住 |

**属性退化那条是重点**：上游哪天把 `@cn` 标注丢了（退化成 Clash 格式），产物看着完全正常，但 `data:geolocation-!cn @-cn` 悄悄失效，国内域名会被塞进 proxy。条数检查发现不了，只有盯过滤计数才行。

**② 命中回归**（`check-hit.py`）

条数环比只证明「产物没缩水」，不证明「规则写得对」。这一步拿真实产物喂 `sing-box rule-set match`，每个分类抽样断言三种映射的正反例：`+.x` 必须命中自身和子域、`x` 必须命中且不命中子域、CIDR 必须命中断内地址且不命中 TEST-NET 段。

样本直接从 `build/` 的源文本里取，和产物同源——通不过就是转换或编译出了问题。（mihomo 那边不需要这套：它的规则集是文本直接喂内核的，没有转换环节。有转换风险的是 sing-box 这一侧。）

### 关于失败邮件

**门禁拦住时 workflow 会失败，GitHub 会发邮件——那封信的意思是「数据变化超出预期，需要你确认」，不是构建崩了。** 日志和 Step Summary 里会写清原因。

确认是上游的合理变化后，显式接受：

```bash
gh workflow run build-ruleset.yml -f accept_baseline=true
```

**有意扩大某个分类时（比如第一次接上国内域名表，条数翻了 17 倍），注定会失败一次**——这是设计使然，不是可以绕过的。`baseline.json` 只有全部通过才会更新，所以「拦住 → 确认 → 接受」是必经流程。

本地确认：`python3 scripts/check-regression.py --update`。

### 上游快照

每次构建结束，这次拉到的上游原文（`.cache/`，不含 iptoasn 那两个十几 MB 的 `.gz`）打包成 artifact 保留 14 天，名字是 `upstream-YYYY-MM-DD.tar.gz`。包里带一份 `MANIFEST.json` 记录「哪个哈希文件对应哪个 URL」，拆开就能还原。

两个用处：上游哪天删库跑路，手里还有原文能顶上；门禁拦住了某次构建，可以翻出当天的快照对比，看是哪家变了。

## 双内核输出

### sing-box

同一份文本出一套 `.srs` + `.json`（`dist/singbox/`），映射是一比一，**不用退化成 regex**：

| 本仓库写法 | mihomo | sing-box |
|---|---|---|
| `+.x` | `+.x`，命中 x 自身和所有子域 | `{"domain_suffix": ["x"]}` |
| `x` | 只命中 x | `{"domain": ["x"]}` |
| `CIDR` | `IP-CIDR` | `{"ip_cidr": [...]}` |
| `DOMAIN-KEYWORD` / `DOMAIN-REGEX` | 另存 `classical/` | `domain_keyword` / `domain_regex` |

已用 `sing-box rule-set match` 逐条实测：`domain_suffix: "a.com"` 命中 `a.com`、`x.a.com`、`x.y.a.com`，不命中 `nota.com`；`domain: "a.com"` 只命中 `a.com`。

**为什么强制只允许 `x` 和 `+.x` 两种写法**：sing-box 没有 `.x`（仅子域）和 `*.x`（仅一级子域）的对应语法，一旦出现就只能退化成 regex，两个内核的行为立刻对不上。转换脚本会直接拦下别的写法让构建失败，而不是悄悄转错。

sing-box 这边还比 mihomo 少一个坑：mihomo 的 `behavior: domain` 装不下 keyword / regexp，只能另存 `classical/` 让人自己引；sing-box 有对应字段，转换时直接合并进同一个 `.srs`，客户端引一个文件就够了。

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

`format: binary` 读 `.srs`；想直接吃文本就换成 `format: source` 指向同目录的 `.json`。rule-set 版本默认 **3**（= sing-box 1.11+），客户端更旧就在 workflow 里改 `SINGBOX_RULESET_VERSION`。

### 为什么不做 geodata

同机实测（Debian / mihomo v1.19.34，稳态 VmRSS，扣掉 44.8 MB 的空配置基线）：

| 方案 | 数据文件 | 净内存开销 |
|---|---|---:|
| geodata（25 条 `GEOSITE`） | `geosite-all.dat` 5.1 MB | **45.4 MB** |
| rule-provider（25 个 `.mrs`） | 896 KB | **8.3 MB** |

**rule-provider 省 37 MB。** 原因不是每字节的膨胀率（两边都约 9.3 倍），而是 **geodata 只能全量加载整个文件**——配置里只写一条 `GEOSITE,cn`，mihomo 还是会把 `geosite-all.dat` 里 33 个分类的数据全部编译进内存。

再做一个严格对等的对照（同一份 `cn` 数据，10.97 万 vs 11.05 万条）：

```
GEOSITE,cn  →  geosite-all.dat 全量 4.9 MB  →  净开销 24.4 MB
RULE-SET,cn →  cn.mrs 524 KB                →  净开销 10.2 MB
```

匹配速度上两者没有可测差异（都是内存查表）。所以没必要为了「性能」做 geodata；**唯一值得做的理由是跨内核通用**（v2ray / xray 只认 geosite.dat）。另外自己拼的 geosite 里属性一定是空的，`GEOSITE,apple@cn` 这种写法匹配不到任何东西而且不报错——本仓库那 6 个 `*-cn` 分类到 geodata 里会重新变回没有属性的大类。

## 文本格式

注释行和空行都会被忽略。domain 类型里几种写法的实际行为：

| 写法 | 匹配 |
|---|---|
| `example.com` | 只有 `example.com` 本身 |
| `+.example.com` | `example.com`、`sub.example.com`、`a.b.example.com` |
| `.example.com` | 任意层级子域，但不含 `example.com` 本身 |
| `*.example.com` | 只有一级子域，不含 `example.com`，也不含 `a.b.example.com` |
| `..example.com` | 无效，内核丢弃并报 `invalid domain` |

写后缀统一用 `+.`。`.x` 和 `*.x` 在 sing-box 里没有对应写法，一旦用了，两个内核的行为就对不上。

classical 一行一条完整规则，除了 rule-set 和 sub-rule 什么都能放：

```
DOMAIN-SUFFIX,google.com
DOMAIN-KEYWORD,kwtest
IP-CIDR,1.2.3.0/24,no-resolve
PROCESS-NAME,qbittorrent.exe
```

逐条匹配，比 domain / ipcidr 慢，只在必须混装的时候用。

## IP 分流注意点

- 域名规则在前、IP 规则在后。反过来会让每个请求都先做一次 DNS 解析。
- IP 规则后面加 `no-resolve`，除非你确实想让内核为了这条规则去解析域名。
- IP 规则是「解析之后才匹配」，兜的是域名规则漏掉的连接（直连 IP 的 CDN、QUIC），**不能拿它替代域名规则**。

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

## 本地构建

```powershell
# 拉上游，生成 build/
python scripts\fetch-sources.py

# 编译 mihomo 的 .mrs。第一次加 -FetchCore 会自动下内核到 .tools\
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -FetchCore

# 用机器上已有的内核
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Mihomo "D:\path\to\mihomo.exe"
```

sing-box 那半在 Linux/WSL 上跑（`build.ps1` 还没覆盖）：

```bash
SINGBOX=/path/to/sing-box python3 scripts/build-singbox.py   # 生成 + 编译
SINGBOX=/path/to/sing-box python3 scripts/check-hit.py       # 命中回归
```

想单独跑一个分类，把名字接在后面：`python scripts/fetch-sources.py telegram cn`。

**注意**：`fetch-sources.py` 要访问 `raw.githubusercontent.com`、RIR 的 ftp、iptoasn 等，国内直连多半不通。本地跑不动不是脚本的问题，挂代理或者直接推上去让 CI 拉。

`report-sources.py` 列出每个分类的条目数和样例，用来核对产物对不对。`dump-geosite.py` 用来分析别人的 geosite.dat。

反解校验：

```powershell
mihomo convert-ruleset domain mrs dist\domain\proxy.mrs dump.yaml
```

## 客户端引用注意点

- 用 jsDelivr：`https://cdn.jsdelivr.net/gh/Kauroth/mihomo_Rules@dist/domain/xxx.mrs`。raw.githubusercontent.com 在国内经常拉不动。
- **jsDelivr 的缓存 CI 会自动刷**（每次构建后 purge 118 个文件），不用手动去 purge 页面点。刚改完立刻要看效果的话，等几十秒或者临时换 raw URL 验证。
- 非要拉 raw 就得给 provider 指定出口（`proxy: 节点选择`），不写这一项走 DIRECT，会出现「网能上但规则集永远不更新」。
- `path` 必须落在 mihomo 的 HomeDir（`-d` 指定）里，否则要设 `SAFE_PATHS`。CI 上实测过：`path is not subpath of home directory or SAFE_PATHS` 会让配置校验直接失败。
- 只有几个域名的小例外不值得进仓库，用 `type: inline` + `payload` 写在主配置里（mihomo ≥ v1.19.1）。

## 几个坑

**`+` 后面必须跟 `.`。** 写成 `+cn.bing.com` 时 v1.19.12 会当废条目静默吞掉，v1.19.33 以上报 `"+" wildcard must occupy the entire label 1`。CI 用的内核比本地新，就是靠它把这个 typo 抓出来的。

**非法行只 warning，不报错。** `convert-ruleset` 遇到非法条目打一条 warning 然后跳过，退出码仍是 0。构建脚本会 grep `invalid` 并主动失败，否则坏行会静默进 `dist`。

**classical 不要编译成 mrs。** v1.19.25 上 `convert-ruleset classical text` 必定 panic（`classical_strategy.go:57`），而且 panic 之后退出码还是 0。所以 classical 只出文本，脚本里把 `panic` 和 `invalid` 一起 grep。

**mrs 跨版本。** v1.19.33 生成的 mrs 能被 v1.19.12 正常读取，反过来也可以，命中结果一致。跨大版本没测过，保守做法是用接近客户端内核的版本生成；拿不准就用 `format: text`，同一份列表两种格式命中结果一样。

**`sing-box rule-set match` 把结果打到 stderr。** stdout 永远是空的，按 stdout 判空会得出「全都不命中」的错误结论（写 `check-hit.py` 时踩过）。

**iptoasn 的 AS 号是纯数字。** 日志格式是 `start / end / 13335 / US / 描述`，不是 `AS13335`。按 `AS2906` 匹配会一条都对不上，而且**静默产出 0 条不报错**。

**PowerShell 5.1 读 .ps1 的编码。** Windows PowerShell 5.1 读无 BOM 的 `.ps1` 会按 GBK 解析，中文注释会把脚本解析坏。`build.ps1` 存的是 UTF-8 with BOM，改它的时候别把 BOM 去掉。

**`git diff --quiet` 看不到未跟踪文件。** 首次运行时 `baseline.json` 是新建的未跟踪文件，用它判断「有没有变化」会永远判成无变化、基准建不起来。要先 `git add` 再 `git diff --cached --quiet`。

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
