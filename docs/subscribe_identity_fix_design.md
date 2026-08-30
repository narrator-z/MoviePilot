# 订阅身份丢失修复方案设计（豆瓣/Bangumi 源订阅无法匹配下载与重复订阅）

> 作者：高见远（软件架构师） ｜ 基于主理人已查证的 NAS 只读数据根因
> 约束：遵循 `AGENTS.md`（公共方法中文 docstring、最小改动、不改数据库结构、测试不触真网/真库）；运行态数据不碰（不改 NAS 库），仅改项目代码。

---

## 1. 修复方案总览

**一句话**：让每一个订阅都携带一个可解析的 `tmdbid`；在**创建**（`add`/`async_add`）与**周期刷新**（`check`）两处，当豆瓣/Bangumi 主识别拿不到 `tmdb_id` 时，用"标题+年份"做一次尽力而为的 TMDB 回退识别并把 `tmdbid` 写回订阅。因为下载/整理历史、种子匹配、媒体库缺集查询**全部以 TMDB 身份为键**，订阅一旦具备 `tmdbid`，原本"幽灵化"的豆瓣订阅就能命中它已下载的记录与未来种子。同时，在**创建订阅**时增加**跨身份同剧去重**：同一部剧用 TMDB / 豆瓣等不同来源再次订阅时，合并身份字段到既有订阅并返回既有订阅，而不是新建一条幽灵订阅。

**为什么只回填 tmdbid、不往历史里写 doubanid**：下载/整理历史由种子的 TMDB 识别写入，`media_source` 实测 100% = `themoviedb` 且 `tmdbid` 无一为空，而 `doubanid` 大概率为空。把订阅的 `tmdbid` 补齐，就是连接"豆瓣订阅 ↔ TMDB 历史"的正确桥梁；反向往历史写 `doubanid` 既无数据来源（种子识别不出豆瓣号）也解决不了问题，属于冗余改动，故不采纳。

---

## 2. 缺集匹配兜底策略（核心）

### 2.1 当前匹配实际发生的位置（已逐行核查）

| 场景 | 代码位置 | 匹配键 | tmdbid=NULL 时的后果 |
|---|---|---|---|
| 订阅详情页"下载/整理文件" | `SubscribeChain.subscribe_files_info`（subscribe.py:3784）→ `DownloadHistoryOper.get_by_mediaid(tmdbid=sub.tmdbid, doubanid=sub.doubanid, …)`（subscribe.py:3824）→ 再经 `TransferHistoryOper.list_by_hash` 关联整理记录 | 订阅身份 → 下载历史身份 | `get_by_mediaid` 在 `tmdbid` 为空时回退到 `doubanid` 分支，而历史 `doubanid` 为空 → 0 条 → 详情页"关联丢失" |
| 订阅匹配下载（`match`） | 种子按 TMDB 识别，与订阅按媒体键（含 `tmdbid`）匹配 | 订阅 `tmdbid` ↔ 种子 `tmdbid` | NULL 永远不匹配 → 下载全落到同剧的 TMDB 订阅，豆瓣订阅成幽灵 |
| 缺集进度（媒体库口径） | `refresh_subscribe_progress` → `recognize_media(_subscribe_recognize_kwargs)` → `resolve_subscribe_missing` 查媒体库 | 订阅 `tmdbid` 查媒体库 | `tmdbid` 为空时媒体库缺集查询失败 → `lack_episode` 不更新 |

### 2.2 兜底回退顺序（tmdbid → doubanid → media_source+media_id → 标题+年份）

- **主机制（根治）**：在 `add`/`check` 把 `tmdbid` 回填进订阅，使上述三处的 `tmdbid` 分支直接命中（历史/种子/媒体库均为 TMDB 键）。
- **次机制（安全网，仅在主机制尚未生效时兜底）**：扩展 `DownloadHistory.get_by_mediaid`，新增 `title`/`year` 入参；当身份查询（tmdbid/doubanid/media_source+media_id）**无结果**且传入了 `title`/`year` 时，按 `title`+`year` 回退查询。
  - 历史表 `title`/`year` 字段始终有值，无需新增列、无需迁移。
  - 该回退只作用于"订阅详情历史关联"这一读路径；写库历史本身不变。

### 2.3 历史记录需要携带的身份字段（结论）

- **不需要**为下载/整理历史新增 `doubanid` 写入（见 §1 论证）。
- 已存在的 `tmdbid`（必填键）+ `title`+`year`（必填文本）足以覆盖全部兜底场景，**零数据库结构变更**。

---

## 3. 订阅创建去重 / 关联规则

### 3.1 触发时机
`SubscribeOper.add` / `async_add`（subscribe_oper.py:29 / :90），在 `Subscribe.exists`（精确身份）返回 `None` 之后。

### 3.2 同剧判定顺序（新增 `Subscribe.find_same_media`）

1. **tmdbid 优先**：若请求带 `tmdbid`，按 `tmdbid`（+`season`）查 → 命中说明"同剧已用 TMDB 身份存在"。
2. **标题+年份**：`name == title AND year == year AND season == season` 查（`Subscribe.get_by_title` 已存在，需补 `season`/类型约束）→ 跨任意来源的同剧兜底。
3. **doubanid / bangumiid**：若请求带这些 ID，按它们查 → 命中说明"同剧已用豆瓣/Bangumi 身份存在"。

> owner_scope 场景需保留 `username` 作用域（`exists_by_username` 同口径）。

### 3.3 命中后的处置（合并而非新建幽灵）
当 `find_same_media` 命中一条**身份不完全相同**的既有订阅时：
- **合并身份字段**：把新请求携带、而既有订阅缺失的身份字段写回既有订阅（`tmdbid`/`doubanid`/`bangumiid`/`media_source`/`media_id`/`imdbid`/`tvdbid`），并跑一次 §2.2 的 `tmdbid` 标题年份回填，确保存活订阅最终带 `tmdbid`。
- **返回既有订阅 id**（语义等同"订阅已存在"），**不再插入新行**。

效果对照 NAS 实测：
- 为已有 TMDB 订阅（id=1/3, `tmdbid=296286`）的"躲在超市后门抽烟的两人"再建豆瓣订阅 → 豆瓣号被合并进 id=1/3，无新幽灵。
- 为已有豆瓣幽灵（id=5, `tmdbid=NULL`）建 TMDB 订阅 → `tmdbid=296286` 回填进 id=5，id=5 由幽灵变可用。
- 既有幽灵（id=5/id=7）会在**下次 `check()` 运行时**被 §4 的回填逻辑自愈（经正常 `update` 写回 `tmdbid`，非直接改库），无需一次性迁移脚本。

---

## 4. 文件清单与改动点（精确到方法）

### 4.1 `app/db/models/subscribe.py`（新增查询方法，含中文 docstring）
- `find_same_media(cls, db, name, year, season=None, tmdbid=None, doubanid=None, bangumiid=None, media_source=None, media_id=None, username=None)`：按 §3.2 顺序返回"同剧不同身份"的既有订阅（同步版）。
- `async_find_same_media(...)`：异步版，供 `async_add` 使用。
- 注意：`get_by_title` 已存在，合并逻辑复用之；新增方法仅做"OR 聚合 + season/owner 约束"。

### 4.2 `app/db/models/downloadhistory.py`（历史查询兜底）
- 扩展 `get_by_mediaid`：新增 `title: Optional[str] = None, year: Optional[str] = None` 入参；现有身份优先级分支（media_source+media_id → tmdbid → doubanid → bangumiid → anilistid）保持不变，**当该查询返回空且 `title`/`year` 均有值时，追加 `title == title AND year == year` 查询**作为回退。
- 同步扩展 `DownloadHistoryOper.get_by_mediaid`（downloadhistory_oper.py:37）透传新参数。

### 4.3 `app/db/subscribe_oper.py`（创建去重+合并）
- `add` / `async_add`：在 `Subscribe.exists` 返回 `None` 后，调用 `find_same_media`；命中则：
  - 调用新增私有辅助 `__merge_subscribe_identity(self, existing, request_identities)` 合并身份字段并 `update` 既有订阅；
  - 返回 `(existing.id, "订阅已存在")`，跳过 `Subscribe(**kwargs).create(...)`。
- `async_add` 同构处理（用 `async_find_same_media` + `async_update`）。

### 4.4 `app/chain/subscribe.py`（tmdbid 兜底回填 + 详情关联兜底）
- `add` / `async_add`（约 :1000 处，于 `SubscribeOper().add(...)` 之前）：若 `mediainfo.tmdb_id is None` 且 `mediainfo.title`/`mediainfo.year` 存在，调用
  `self.recognize_media(meta=MetaInfo(f"{mediainfo.title} {mediainfo.year}"), mtype=mediainfo.type, cache=False)`
  若回退结果带 `tmdb_id`，则把 `tmdb_id`/`imdb_id`/`tvdb_id` 写回 `mediainfo`（沿用到 `SubscribeOper().add` 落库）。
- `check`（约 :2148 之后、:2218 `update_data` 组装前）：若 `mediainfo.tmdb_id is None` 且 `subscribe.name`/`subscribe.year` 存在，按同上"标题+年份"回退识别；命中则把 `tmdbid`/`imdbid`/`tvdbid` 追加进 `update_data`。
- `subscribe_files_info`（约 :3824）：把 `title=subscribe.name, year=subscribe.year` 透传给 `downloadhis.get_by_mediaid(...)`，启用 §2.2 标题年份回退。

### 4.5 不改动项（明确排除，避免范围蔓延）
- `app/db/models/transferhistory.py`：**不改**（订阅详情链路经 download→hash→transfer 关联，无需在整理历史上做同构回退）。
- `app/chain/download.py` / `app/chain/transfer.py`：**不改**（不在历史里写 `doubanid`/`media_source`/`media_id`，理由见 §1）。
- 数据库结构 / Alembic 迁移：**不涉及**（无新增列）。

---

## 5. 任务分解（按实现顺序，含依赖）

> 约束：任务数 ≤ 5；每个任务 ≥ 3 个相关文件；最小粒度按模块/层次分组。
> 注：本任务为既有仓库的缺陷修复（非 greenfield），故"基础设施任务"以**数据/模型层基座**替代。

### T01 数据/模型层：跨身份同剧查询 + 下载历史标题年份兜底 ｜ P0 ｜ 依赖：无
- `app/db/models/subscribe.py`（新增 `find_same_media` / `async_find_same_media`，中文 docstring）
- `app/db/models/downloadhistory.py`（扩展 `get_by_mediaid` 支持 `title`/`year` 回退）
- `app/db/downloadhistory_oper.py`（扩展 `get_by_mediaid` 透传 `title`/`year`）
- 交付：两个查询能力就绪，供 T02/T03/T04 消费。

### T02 订阅创建去重与身份合并（oper 层） ｜ P0 ｜ 依赖：T01
- `app/db/subscribe_oper.py`（`add`/`async_add` 接入 `find_same_media` + 新增 `__merge_subscribe_identity` 合并辅助）
- `app/db/models/subscribe.py`（`find_same_media` 联调确认）
- `tests/test_subscribe_oper.py`（新增去重/合并用例）
- 交付：同剧不同来源不再建幽灵订阅。

### T03 tmdbid 兜底回填 + 订阅详情历史关联兜底（chain 层） ｜ P0 ｜ 依赖：T01（消费 T02 合并路径）
- `app/chain/subscribe.py`（`add`/`async_add`/`check` 的 tmdbid 标题年份回填；`subscribe_files_info` 透传 `title`/`year`）
- `app/db/downloadhistory_oper.py`（消费 T01 扩展）
- `tests/test_subscribe_chain.py` + `tests/test_transfer_download_history_lookup.py`（回填与关联兜底用例）
- 交付：豆瓣/Bangumi 订阅自愈具备 tmdbid，详情页能按标题年份命中历史。

### T04 端到端回归与既有测试体检 ｜ P0 ｜ 依赖：T02、T03
- `tests/test_subscribe_oper.py`（去重/合并回归）
- `tests/test_subscribe_chain.py`（tmdbid 回填 + check 自愈回归）
- `tests/test_transfer_download_history_lookup.py`（tmdbid 为空时按标题年份命中历史回归）
- 交付：三大场景用例全绿，既有用例无回归（`python tests/run.py` 全量通过、零真网调用）。

---

## 6. 测试计划（建议新增/修改用例）

遵循既有 `test_subscribe_chain.py` 的 `stub_modules` + `recognize_media` mock 范式，零真网/真库。

**`tests/test_subscribe_oper.py`**
- `test_add_dedup_merges_douban_into_existing_tmdb`：已有 `tmdbid=296286` 订阅；以 `doubanid` 再 `add` → 返回既有 id、既有订阅 `doubanid` 被回填、未插入新行。
- `test_add_dedup_by_title_year`：仅以 `name`+`year`+`season` `add` → 命中既有同剧订阅。
- `test_add_creates_when_truly_different_show`：不同剧正常新建（确认不会误合并）。

**`tests/test_subscribe_chain.py`**
- `test_add_backfills_tmdbid_from_title_year_when_douban_null`：mock `recognize_media` 首次（按 doubanid）返回 `tmdb_id=None`，再次（按标题+年份）返回 `tmdb_id=296286`；断言落库订阅带 `tmdbid`。
- `test_check_backfills_tmdbid_for_existing_douban_sub`：既有豆瓣幽灵订阅（`tmdbid=None`）；`check()` 中回退识别命中 → `update_data` 含 `tmdbid`/`imdbid`/`tvdbid`。
- `test_subscribe_files_info_matches_history_by_title_year_when_tmdbid_null`：mock `DownloadHistory.get_by_mediaid` 仅在 `title`/`year` 分支返回记录；断言详情 `episodes` 含下载/整理文件。

**`tests/test_transfer_download_history_lookup.py`**
- `test_get_by_mediaid_falls_back_to_title_year`：身份查询为空、传 `title`/`year` 时命中历史；不传则空（验证回退不污染既有行为）。

**既有用例体检**：`T04` 全量跑 `python tests/run.py`，确认 `test_check_keeps_sparse_priority_*`、`test_resolve_subscribe_missing_*` 等不受影响。

---

## 7. 设计决策与风险

- **不改数据库结构**：全部复用既有 `tmdbid`/`title`/`year` 列，无 Alembic 迁移，符合 `AGENTS.md` 与"不碰运行态数据"约束。
- **自愈而非一次性迁移**：既有 NAS 幽灵（id=5/id=7）在部署后首次 `check()` 自动补齐 `tmdbid`；运营也可手动触发"刷新订阅元数据"即刻生效。不写一次性改库脚本，避免直接操作生产库。
- **标题年份回填为尽力而为**：若 TMDB 按标题年份仍搜不到（极罕见），订阅维持原状，不会写错 `tmdbid`（仅在有把握命中时才回填）。
- **新增公共方法均带中文 docstring**，满足 `AGENTS.md` 强制门禁。
