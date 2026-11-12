# mihomo 自建规则集

自己维护的 mihomo / Clash.Meta 规则集仓库。**源文件是纯文本**（人可读、可 diff、可写注释），由 GitHub Actions 编译成二进制 `.mrs` 发到 `dist` 分支，客户端按需取文本版或 mrs 版。

## 目录

```
rules/domain/*.txt      behavior: domain   —— 一行一个域名
rules/ipcidr/*.txt      behavior: ipcidr   —— 一行一个 CIDR
scripts/build.sh        CI 用（Ubuntu / bash）
scripts/build.ps1       本地用（Windows / PowerShell）
config-snippet.yaml     贴进 mihomo 主配置的 rule-providers 片段
.github/workflows/      编译 + 发布到 dist 分支
dist/                   构建产物（不入库，CI 自动推到 dist 分支）
```

## 文件内容规范（mihomo v1.19.12 实测，不是抄文档）

### domain / ipcidr 用 `format: text`：一行一条

`#` 开头的注释行和空行都会被内核忽略，实测不产生 warning——**放心写注释**。

### 通配符语义（domain 类型，实测结果）

| 写法 | 匹配 | 不匹配 |
|---|---|---|
| `example.com` | `example.com` 精确 | `sub.example.com` ✘ |
| `+.example.com` | `example.com`、`sub.example.com`、`a.b.example.com`（含自身 + 任意层级子域） | — |
| `.example.com` | `sub.example.com`、`a.b.example.com`、`x.y.z.example.com`（任意层级子域） | `example.com` 自身 ✘ |
| `*.example.com` | `sub.example.com`（**只有一级**） | `example.com` ✘、`a.b.example.com` ✘ |
| `..example.com` | **无效写法**，内核直接丢弃并报 `invalid domain:[..example.com]` | 全部 ✘ |

实测方法：起 mihomo + `rule-providers(type: file, behavior: domain, format: text)` + `RULE-SET` 指到 `REJECT`，逐个域名打请求后看日志里命中的规则名。日志原文：

```
--> bare.com:80     match RuleSet(lab) using REJECT     # 列表里写的是 bare.com
--> sub.bare.com:80 match Match using REJECT
--> suffix.com:80   match RuleSet(lab) using REJECT     # 列表里写的是 +.suffix.com
--> sub.dot.com:80  match RuleSet(lab) using REJECT     # 列表里写的是 .dot.com
--> dot.com:80      match Match using REJECT
--> a.b.star.com:80 match Match using REJECT            # 列表里写的是 *.star.com
```

**结论：写后缀用 `+.`，不要用 `.` 或 `*.`。** 原 Clash wiki 里 `..domain` 那种写法已经不被 mihomo 接受。

### classical 类型

一行一条完整规则，除了 rule-set / sub-rule 之外什么都能放：

```
DOMAIN-SUFFIX,google.com
DOMAIN-KEYWORD,kwtest
IP-CIDR,1.2.3.0/24,no-resolve
PROCESS-NAME,qbittorrent.exe
```

代价是逐条匹配，比 domain / ipcidr 慢，只在必须混装时才用（实测 `DOMAIN` / `DOMAIN-SUFFIX` / `DOMAIN-KEYWORD` 三种在 text 列表里都正常命中）。

## 本地构建（Windows）

```powershell
# 第一次：把 mihomo 内核放到 .tools\mihomo.exe（下面这条自动下 windows zip）
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -FetchCore

# 之后每次改完 rules\ 就跑
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

也可以用机器上已有的内核（例如 v2rayN 自带的）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Mihomo "D:\Applications\v2rayN-windows-64\bin\mihomo\mihomo.exe"
```

脚本会：`convert-ruleset` 编译每个 `.txt` → `dist/<behavior>/<name>.mrs`，同时把文本版一起拷进 `dist/`；**任何一行非法（`invalid ...`）就整体失败**，不会把坏规则推上去。

产物校验（可选）：`mihomo convert-ruleset domain mrs dist\domain\xxx.mrs dump.yaml` 能把 mrs 反解成 yaml，肉眼看一下内核实际收下了哪些条目。

## CI（GitHub Actions）

`push` 到 `main`（改动了 `rules/`）或手动 `workflow_dispatch` 触发：
1. 下载 mihomo linux-amd64（`-compatible` 版，兼容性最好）
2. `scripts/build.sh` 编译 → `dist/`
3. 把 `dist/` 强推到仓库的 **`dist` 分支**（单文件一次提交，带 `GITHUB_TOKEN`，不需要额外 secret）

客户端永远指向 `dist` 分支，`main` 上怎么改都不影响使用。

> mrs 带格式版本号，高版本内核生成的 mrs 老内核可能拒绝加载。保守做法：生成 mrs 的内核版本 ≤ 你客户端内核版本。拿不准就用 `format: text` 直接吃文本，行为完全一致（同一份列表，text 和 mrs 实测命中结果逐条相同）。

## 客户端怎么引

见 `config-snippet.yaml`。要点：

- `url` 优先用 **jsDelivr**：`https://cdn.jsdelivr.net/gh/Kauroth/mihomo_Rules@dist/<path>`
  `raw.githubusercontent.com` 在国内常被 reset，不用代理基本拉不下来；jsDelivr 一般直连可达。
- 真要拉 raw，就给它指定出口：`proxy: 节点选择`（不写这一项走 DIRECT）。少写会踩"能上网但规则集永远不更新"的坑。
- `path` 只能落在 mihomo 的 HomeDir（`-d` 指定的目录）里，否则要设 `SAFE_PATHS` 环境变量。
- `interval: 86400` 够用；文本集合几十 KB，一天拉一次没负担。
- jsDelivr 对分支引用有缓存，改完发现客户端拿到的还是旧内容 → 去 jsdelivr 的 purge 页面刷一下，或临时换 raw URL 验证。
- 只有几个域名的小例外，不值得进仓库：用 `type: inline` + `payload` 直接写在主配置里（mihomo ≥ v1.19.1）。

## 构建链路实测（本仓库已跑通）

本机内核 mihomo v1.19.12（windows amd64），用 v2rayN 自带的 `mihomo.exe` 跑 `scripts\build.ps1`：

```
domain/cn-cdn-extra          365 B (mrs)   ← 文本 1089 B
domain/fakeip-filter-extra   257 B (mrs)   ← 文本 818 B
ipcidr/private-extra         133 B (mrs)   ← 文本 670 B
```

反解校验（`convert-ruleset domain mrs x.mrs dump.yaml`）确认内核实际收下的就是 `+.xxx` 形式，注释行和空行不进产物。

两个踩过的坑：

1. **UTF-8 BOM**：列表文件带不带 BOM 内核都能解析（首行是注释或域名都实测无 warning）。但 **Windows PowerShell 5.1 读无 BOM 的 `.ps1` 会按 GBK 解析**，中文注释会直接把脚本解析坏（我第一次跑就是这么挂的）。本仓库的 `build.ps1` 是 **UTF-8 with BOM**，用编辑器改它时别把 BOM 去掉。
2. **非法行只 warning、不报错**：`convert-ruleset` 碰到非法条目（如 `..bad.com`）打一条 warning 后跳过，**退出码仍是 0**。所以两个构建脚本都会 grep `invalid` 并主动 `exit 1`——否则坏行会静默进 dist。

## 上线步骤

仓库已建好：`Kauroth/mihomo_Rules`，public，**默认分支名是 `Kauroth`（不是 main）**——workflow 里已经按这个名字配好触发分支。

```bash
git clone https://github.com/Kauroth/mihomo_Rules
cd mihomo_Rules
# 把脚手架里的文件拷进来，README.md 直接覆盖掉建仓时自动生成的那个
git add -A
git commit -m "init: 自建 mihomo 规则集"
git push
```

推完之后：Actions 里 `build ruleset` 跑绿 → `dist` 分支里出现 `domain/*.mrs` + `domain/*.txt` → 就能在 mihomo 里引用了。

> 本机给 github.com 配过 git 代理（`http.https://github.com/.proxy = 127.0.0.1:7897`），**Clash 没开时 push 会失败**。另外这台机器没有缓存 GitHub 凭据（system 级 credential helper 是 `helper-selector`，会弹窗），push 那一下得在真实终端里手动授权一次。

## 排序坑（和别人的规则集混用时）

如果同时用 DustinWin / MetaCubeX 的集合：它们 `proxy` / `geolocation-!cn` 里有 `+.apple.com`、`+.microsoft.com`、`+.live.com` 这类父域，会把你辛苦分类的「国内 CDN 直连」整个吃掉。**你自己的直连集合必须排在那些集合之前**，本仓库的 `cn-cdn-extra.txt` 就是干这个用的。
