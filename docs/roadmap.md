# 路线图：从「补丁型 rule-provider 仓库」到「自建 ruleset + geodata 发布仓库」

> 本文是设计文档，不是教程。所有「实测/已验证」的结论都标了来源；没验过的标 **待验**。
> 对标项目：`DustinWin/ruleset_geodata`（其完整 CI 链路已逆向，见文末附录）。

## 决策记录

**2026-11-12 —— 主走 rule-provider，geodata 降级为可选兼容产物。**

- rule-provider 在表达力上没有天花板：`behavior: classical` 可以混装进程名 / 端口 / IP-CIDR，
  而 geosite 的语法只有 domain / full / keyword / regexp 四种，装不了这些。
- 更新粒度差一个数量级：geosite 是全量 dat（那家 5.2 MB），任何一个分类变动都要重下整份；
  rule-provider 是每分类一个 KB 级文件，各自有 `interval`。
- 分类命名自主：文件名即分类名，不用被上游的命名习惯绑架。
- geodata 只在「要给只吃 `GEOSITE` 写法的客户端用」时才需要，属于兼容产物，不是主线。

**geodata 能不能自动补全？能。** 机制就是 dlc 自带的：

| 需求 | dlc 的机制 | 证据 |
|---|---|---|
| 补上缺失的单平台分类 | 直接新增 data 文件 / 引 v2fly 的现成分类 | v2fly data/ 有 1556 个分类，`telegram`/`twitter`/`instagram`/`facebook`/`tiktok` 全在 |
| 按属性切子集 | `include:apple @cn`（选择性包含，过滤语义） | `apple` 文件里 209 行带 `@cn` |
| 分类聚合 | `include:` 递归 | 你上层的 `apple` 就是一堆 include 拼的 |
| 一处维护多处出现 | `&affiliation` | 官方文档 |

所以「不能补全」不成立。但它有代价：要装 Go、要同步 v2fly 的 `data/`、产物是全量 dat。
既然走 rule-provider，同样的上游数据用 `sources/` 引就行，不需要 Go —— **同一份数据，更低的代价**。

**已落地**：`sources/`（27 个分类 / 42154 条）+ `scripts/fetch-sources.py`，见 README。

## 0. 现状盘点（已完成）

| 能力 | 状态 | 证据 |
|---|---|---|
| 纯文本源 → `.mrs` 编译 | 已跑通 | `scripts/build.sh` / `build.ps1`，`mihomo convert-ruleset domain\|ipcidr text` |
| 文本版 + mrs 版同时发布 | 已跑通 | `dist/<behavior>/<name>.{txt,mrs}` |
| 非法行守卫 | 已跑通 | 脚本 grep `invalid` 并 `exit 1`；`+cn.bing.com` 这类 typo 已被抓到 |
| CI 自动构建 → `dist` 分支 | 已跑通 | `.github/workflows/build-ruleset.yml`，强推 `dist:dist` |
| 跨内核版本一致性 | 已实测 | v1.19.12 / v1.19.33 同列表零 warning，mrs 双向可读 |
| 内容体量 | **弱** | 只有 3 个「例外补丁」：`cn-cdn-extra`、`fakeip-filter-extra`、`private-extra` |
| sing-box 输出 | **缺** | 无 `.json` / `.srs` |
| geodata 输出 | **缺** | 无 `geosite.dat` / `geoip.dat` / `*.mmdb` / `*.metadb` |
| 定时构建 / 上游自动同步 | **缺** | 只有 `push` 触发，源数据不随上游更新 |

## 1. 目标形态

三条腿，各自独立可发布：

```
rules/  (人工维护的纯文本，人可读可 diff)
  ├─ domain/*.txt   ─┬─→ mihomo  .list + .mrs        → dist 分支 / mihomo-ruleset
  │                  └─→ sing-box .json + .srs        → dist 分支 / sing-box-ruleset
  └─ ipcidr/*.txt   ─┴─→ 同上

geodata/ (数据源声明 + 上游拉取)
  ├─ geosite.dat  ← v2fly/domain-list-community 的 data/ (带 @cn/@ads 属性) + 自有 include
  └─ geoip.dat / Country.mmdb / geoip.metadb  ← Loyalsoldier/geoip (config.json 驱动)
```

关键：**源文件永远只有一份（纯文本）**，其余格式都是编译产物。这决定了第 4 阶段能加回归测试。

## 2. 阶段 1：sing-box 输出

### 2.1 转换规则（这是真正的坑）

本仓库的 domain 文本用的是 **mihomo 语义**，sing-box 的 source JSON 没有对应概念，必须显式分类：

| 本仓库写法 | mihomo 语义 | sing-box source JSON |
|---|---|---|
| `example.com` | 精确匹配 | `"domain": ["example.com"]` |
| `+.example.com` | 自身 + 任意层级子域 | `"domain_suffix": ["example.com"]` ✅ 语义等价 |
| `.example.com` | 仅子域（不含自身） | **无等价物** → 只能 `domain_regex`：`^.+\.example\.com$` |
| `*.example.com` | 仅一级子域 | **无等价物** → `domain_regex`：`^[^.]+\.example\.com$` |
| `keyword:x` | 子串 | `"domain_keyword": ["x"]` |

> 建议：**仓库自己的 domain 列表只允许写 `example.com` 和 `+.example.com` 两种形式**，其余禁用。
> 理由：这两种能无损映射到 mihomo / sing-box / geosite 三方，一旦允许 `.` 和 `*.`，跨内核必产生行为差异，而且是静默的。
> 这条约束应该写进 README 的「文件内容规范」，并用构建脚本强制（正则校验）。

### 2.2 编译

```bash
# sing-box 需要 json → srs 两步
sing-box rule-set compile --output out.srs out.json
```

- `.json` 是 source format，`"version"` 字段随内核版本走（DustinWin 的做法：从内核文档里抓出 latest / compatible 两个 version 值，分别出两套）。
- **待验**：本地 `sing-box.exe`（v2rayN 自带）版本号 → 决定 version 取 4 还是 5。

### 2.3 验收

- [ ] `dist/` 下每个 domain 列表都有 `.txt` / `.mrs` / `.json` / `.srs` 四份
- [ ] 同一份列表，mihomo 与 sing-box 命中结果逐条一致（构造测试域名跑一遍）
- [ ] mrs/srs 体积报告进 CI 日志

## 3. 阶段 2：自建 geodata

### 3.1 一个已验证的关键结论

**DustinWin 的 `geosite.dat` 里没有任何 attribute（`@cn` / `@ads` 一个都没有）。**

实测方法：下载其 release 产物，用零依赖 protobuf 解析器 dump（工具见 `scripts/dump-geosite.py`）：

```
分类数: 15  条目总数: 255011
分类            条数   类型分布                          属性
ADS           107467  Domain(suffix)=107467            -
AI               158  Plain=3 Domain(suffix)=134 Full=21 -
APPLE-CN         243  Domain(suffix)=225 Full=18       -
CN            114768  Domain(suffix)=114765 Full=3     -
...
PROXY          25481  Domain(suffix)=25387 Full=94     -
```

成因是构造性的：它的数据源是 Clash 格式的 `.list`（只有 `DOMAIN,` / `DOMAIN-SUFFIX,` / `DOMAIN-KEYWORD,`），
再 `sed` 成 dlc 语法的 `full:` / 裸域名喂给生成器 —— **Clash 格式里根本表达不了 protobuf 的 attribute 字段**。

所以它的分类是**扁平命名**：`apple-cn`、`microsoft-cn`、`google-cn`，而不是 v2fly 的 `apple@cn`。
推论：`GEOSITE,apple@cn` 在这份 dat 上**匹配不到任何东西**（静默失效，不报错）。

### 3.2 因此有两条路，必须选一条

| | 路线 A：对齐 v2fly（保留属性） | 路线 B：对齐 DustinWin（扁平命名） |
|---|---|---|
| 数据源 | `v2fly/domain-list-community` 的 `data/` 原始文件（含 `include:` / `@cn` / `@ads`） | 自有的 Clash `.list` |
| 生成 | `go run ./ --datapath=... --outputname geosite.dat`（dlc） | sed 转换后同样走 dlc |
| 优势 | 客户端可继续用 `GEOSITE,apple@cn`，**是真正的 geosite 替代品**；分类名与主流配置兼容 | 与现有 `.list` 单一数据源，维护成本低；与 DustinWin 生态一致 |
| 劣势 | 需要维护/同步 v2fly 的 `data/`；纯文本源不止一份 | 丢掉属性能力；用 `apple@cn` 的配置会静默失效 |
| 适合 | 目标是「别人能拿我的 dat 直接替掉官方」 | 目标是「服务自己那一套固定配置」 |

**推荐 A**，理由：B 路线已经是 DustinWin 在做的事，重复没有意义；而 A 是目前**没有被人做好**的位置，
且你现有配置里确实在用 `GEOSITE,apple@cn` / `GEOSITE,microsoft@cn` 这类写法，走 B 等于把自己的配置改坏。

### 3.3 geoip 侧

用 `Loyalsoldier/geoip`（Go，`config.json` 驱动），输入源与输出：

- 输入：`private` 内置；`text` URI（如 `ispip.clang.cn/all_cn.txt`、`china-operator-ip`、Telegram `cidr.txt`）；`maxmindGeoLite2ASNCSV`（按 ASN 抽 Telegram / Netflix）
- 输出：`v2rayGeoIPDat`（`geoip.dat`，`wantedList` 控制分类）、`mmdb`、`metadb`
- 注意：**这套工具只支持你 `wantedList` 里列出的分类**。DustinWin 的 `geoip.dat` 只有 5 个分类（`private`/`cn`/`telegram`/`netflix`/`media`），**没有国家代码**。
  如果客户端要写 `GEOIP,TW`，必须另外出 `Country-all.mmdb`，或者在 wantedList 里显式加国家。

### 3.4 验收

- [ ] 产出的 `geosite.dat` 用 `scripts/dump-geosite.py` dump 后，`apple` 分类能看到 `@cn` 属性
- [ ] 客户端配置 `GEOSITE,apple@cn` 命中（用 `convert-ruleset` 反解 + 实跑日志双验证）
- [ ] `geoip.dat` / `Country.mmdb` / `geoip.metadb` 三份产物的分类清单一致
- [ ] 体积对比表（vs MetaCubeX / Loyalsoldier / DustinWin）写进 README

## 4. 阶段 3：自动化与发布

- `schedule: cron "0 3 * * *"` + `timezone: Asia/Shanghai`（对齐上游构建时间）
- `workflow_dispatch` 手动触发
- 数据源同步：geodata 侧的 `data/` 用 submodule 或 CI 内 `git clone` 上游（不要 fork 一份进仓库，否则自己变成了维护上游的人）
- 发布形态二选一：
  - **分支强推**（现有做法，`dist`）——客户端 URL 稳定，jsDelivr 可直接引
  - **Release 资产**（DustinWin 做法）——有版本号、可 sha256 校验、GitHub 直链不带缓存问题
  - 建议：**两者都出**，主推 Release（可校验），`dist` 分支保留给 jsDelivr
- 每条产物生成 `sha256sum.txt`

## 5. 阶段 4：质量守门

- 非法行 fail（已有）
- **属性回归**：dump 产物，断言 `@cn` 属性条目数 > 0 —— 这条能防止哪天数据源悄悄退化成 Clash 格式
- **命中回归**：固定的测试域名 → 期望命中规则名，mihomo / sing-box 各跑一遍
- 体积 / 条目数变化报告（突增突减要能看出来）
- 三方体积对比自动写进 README，防止「体积涨了 10 倍没人发现」

## 附录：DustinWin 全链路逆向（2026-11-12 核对）

CI 文件：`.github/workflows/run.yml`（每天 03:00 Asia/Shanghai）

1. **geosite.dat**：checkout `DustinWin/domain-list-custom` + `v2fly/domain-list-community`；
   拉上游 `*.list` → `sed 's/DOMAIN,/full:/; s/DOMAIN-SUFFIX,//; s/DOMAIN-KEYWORD,/keyword:/'` → `community/mydata/`；
   `cd community && go run ./ --datapath=./mydata/ --outputname geosite-all.dat`（dlc）。
   出 5 个变体：`geosite-all` / `-all-lite` / `geosite` / `-lite` / `-mini`，靠增删 `ads`、替换 `fakeip-filter(-lite)`、换 `cn-lite` 控制。
2. **geoip**：不自己算，直接下载 `DustinWin/geoip` 的 release 资产（`.mmdb` 进 `mihomo-geodata/`，其余 `.dat`/`.metadb` 进 `community/` 后一起挪走）。
   `DustinWin/geoip` 本身是 `Loyalsoldier/geoip` 的用法（见其 `config.json`）。
3. **mihomo ruleset**：`.list` → `./mihomo convert-ruleset domain|ipcidr text in out.mrs`，文本版同时保留。
4. **sing-box ruleset**：`tools/convert.sh` 手工把 clash `.list` 按
   `PROCESS-NAME`（拆 package / process / exe）、`DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-KEYWORD`、`DOMAIN-REGEX`、`IP-CIDR` 分类，
   用 `sed` 拼成 source format JSON，再 `sing-box rule-set compile` 出 `.srs`。
   出两套：`sing-box-ruleset`（latest，`version: 5`）与 `sing-box-ruleset-compatible`（`version: 4`）。
5. **发布分支**：`mihomo-geodata`、`mihomo-ruleset`、`sing-box-ruleset`、`sing-box-ruleset-compatible`、`icons`，全部强推。
6. **mihomo 内核**：用 `DustinWin/proxy-tools` 的 upx 压缩版（v3）—— 只为了 CI 里跑得快，不影响产物。
