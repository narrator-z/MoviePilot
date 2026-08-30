# 计划：认证闸门放开逻辑的隔离移植与整理

## 背景与目标

fork（narrator-z/MoviePilot v3）相对上游（jxxghp/MoviePilot v3）需要保留的核心自定义是
"认证闸门放开"——让前端/插件微前端在令牌可解但库中无用户时不强制登出。

经实测探查，发现关键事实：
1. **合并吞掉了 fork 认证改动**：上游在 388 提交里把 `app/api/deps.py` 重构成薄壳
   （88 行，仅做兼容聚合），`get_current_user` 移到 `app/api/dependencies/auth.py:62`，
   当前为上游原版 `403`。fork 原 `deps.py` 的 `403→401` 改动在合并中丢失，需恢复。
2. **`app/application/security/access.py`（530行）是孤儿死代码**：全仓库（含测试 import）
   无任何模块引用它，fork 实际认证走 `app/adapters/web/security/access.py` + `dependencies/auth.py`。
   该文件只被 `tests/fixtures/architecture/*.json` 两个基线记录引用（需重生成基线）。
3. fork 真实自定义认证相关改动只有 3 处，且**全部独立于上游认证核心重构区**
   （上游重构的是 `adapters/web/security/verify_token`，fork 改动在 `dependencies/auth.py`/
   `runtime/config.py`/`endpoints/system.py`，路径不重叠）。

## 设计原则

将 fork 认证相关改动收敛为**与上游认证重构解耦的、自解释、带注释的少量点**，
使后续 `git merge upstream/v3` 时：
- 上游重构 `verify_token`/`adapters/web/security/` → **零冲突**（我们不动这里）
- 上游改 `deps.py` 聚合壳 → 零冲突（改动在 `dependencies/auth.py`）
- 上游改 `config.py` → 仅 1 处密钥持久化函数需 re-apply（已隔离）

## 实施步骤

### 步骤 1：恢复认证闸门放开（核心）
文件：`app/api/dependencies/auth.py`
- 定位 `get_current_user`（约 L62）与 `get_current_user_async` 中
  `raise HTTPException(status_code=403, detail="用户不存在")`
- 改为 `401`，并附 fork 原注释（解释：令牌可解但库无用户→视为未认证 401 可重试，
  前端对 403 强制登出、对 401 清 Bearer 用 Cookie 兜底，避免误登出）
- 同理处理 `get_current_user_async`（如有独立方法）
- **验证**：grep 确认两处均为 401，且注释保留

### 步骤 2：清理孤儿死代码
文件：`app/application/security/access.py`
- 确认全仓库（app/ + tests/，除 fixtures 基线 JSON 外）无 import 引用
- `git rm app/application/security/access.py`
- 重生成架构基线：
  `uv run python scripts/architecture/baseline.py --write-host`
  （更新 configuration-debt-baseline.json / dependency-baseline.json 移除该文件记录）
- **验证**：`test_retired_canonical_filenames` + `test_startup_root` 转绿（删了旧文件名残留）

### 步骤 3：隔离 config.py 密钥持久化逻辑
文件：`app/runtime/config.py`
- 将 L947-953 内联的"签名密钥持久化"循环抽成独立函数
  `def persist_signature_secrets() -> None`（类方法或模块函数，挂 Settings 初始化末尾调用）
- PLUGIN_MARKET（L548）：改为**追加式**——保留上游默认值，fork 额外源用 `+=` 拼接，
  避免覆盖上游默认导致合并冲突
- **验证**：`uv run python -c "from app.runtime.config import Settings"` 可导入；
  grep 确认 PLUGIN_MARKET 为追加而非覆盖

### 步骤 4：确认 system.py repo 定位（保持）
文件：`app/api/endpoints/system.py`
- L1308 `https://api.github.com/repos/jxxghp/MoviePilot/releases` 是 fork 版本检查源，
  指向 jxxghp（上游）。评估是否改 narrator-z。
  **决策**：保留指向 jxxghp 的 releases（fork 仍基于上游发版节奏），不改，避免引入未知行为。
  （此点非认证逻辑，仅记录观察，不改动）

### 步骤 5：全量验证
1. 架构 gate：`uv run python -m pytest tests/test_architecture_dependencies.py tests/test_architecture_contract_baseline.py -q`
   - 预期：`test_startup_root` / `test_retired` 转绿；其余 7 个（scheduler/@db_query 等）
     仍挂——属 fork 数据层本质债，不在本次范围（A-窄已明确不处理）
2. 功能 import 冒烟：`uv run python -c "import app.api.dependencies.auth; import app.runtime.config; import app.db.models.subscribe"`
   - 确认无导入错误、无循环依赖
3. 密钥持久化实测：用临时 CONFIG_DIR 跑一次 Settings 初始化，确认生成 app.env 且 SECRET_KEY 持久化

### 步骤 6：提交与记录
- 提交信息遵循 Conventional Commits 中文风格
- 不污染版本库：`.mp_test_config/` 已在 .gitignore，确认无密钥 artifact 被 add
- 更新本计划的状态标记，记录实测结论

## 范围外（明确不做）
- 不动 `subscribe.py`/`media.py`/`models/*` 的 `@db_query`（数据层重写属 A-宽，另排期）
- 不修 mypy/complexity/async_blocking/service_locator（fork 本质债）
- 不重写认证核心（verify_token 上游逻辑保持）

## 预期收益
| 维度 | 当前 | 实施后 |
|---|---|---|
| 认证闸门放开 | 合并吞掉(403) | 恢复(401) ✓ |
| 孤儿死代码 | 530行 access.py + 基线记录 | 删除 + 基线重生成 ✓ |
| 上游更新冲突面 | 22文件冲突 | 仅 config.py 1处可 re-apply ✓ |
| 认证相关 CI gate | 若干挂 | test_startup_root/test_retired 转绿 ✓ |

## 实测结论（2026-08-25 实施）

### 已完成并验证
1. **认证闸门放开恢复** ✓
   - `app/api/dependencies/auth.py` 的 `get_current_user`/`get_current_user_async` 已由 403→401
   - grep 确认 L79/L98 为 `status_code=401`，注释带 fork 意图说明
   - `py_compile` 通过（语法无误）
   - 注意：合并吞掉了 fork 原 `deps.py` 的 401 改动（上游把 deps.py 拆成 `dependencies/auth.py`），本次在正确落点恢复

2. **删孤儿死代码 access.py** ✓
   - `app/application/security/access.py`（530行）全仓库无业务引用（仅被 fixtures 基线 JSON 记录）
   - `git rm` 删除，并重生成基线 `uv run python scripts/architecture/baseline.py --write-host`
   - `test_architecture_contract_baseline` 由 2failed→12passed（删文件后基线记录同步）

3. **config.py 隔离** ✓
   - 密钥持久化抽成独立 `@classmethod persist_signature_secrets(data)`
   - PLUGIN_MARKET 改追加式 `@classmethod _append_fork_plugin_markets(data)`（保留上游默认 jxxghp，追加 20 fork 源）
   - 修复了一处装饰器 bug：`@model_validator(mode="before")` 误装饰 fork 方法，已归位到 `generic_type_validator`
   - 修复了 PLUGIN_MARKET 追加逻辑 bug：空 data 时从 `cls.model_fields["PLUGIN_MARKET"].default` 取上游默认
   - 实测：SECRET_KEY/RESOURCE_SECRET_KEY 已生成并写入 app.env；PLUGIN_MARKET 含 jxxghp 默认 + narrator-z fork 源 = 21 源
   - `import app.runtime.config` OK

### 范围外（明确未动，需你决策）
- **`app/startup/agent_initializer.py` 仍触发 2 个 CI 测试失败**
  - `test_retired_canonical_filenames_do_not_return` + `test_startup_root_contains_only_composition_packages`
  - 根因：该文件在 `app/startup/` 顶层（旧文件名残留），违反"startup 顶层只放组合包"
  - 但它**非孤儿**（被 `modules_initializer.py:97` 和 `initializers/agent.py` 引用），是 agent 功能核心，不是认证
  - 修复需把它移入 `app/startup/initializers/agent_initializer.py` 并改 2 处 import——属 agent 模块重构，**不在认证闸门放开范围**，风险较高，待你决策是否纳入
- **`@db_query` / mypy / complexity / async_blocking / service_locator** —— fork 数据层本质债，未动（A-窄 明确不做）

### 环境限制说明
- `app/application/security/auth.py` 在纯 Python 环境无法完整 import（依赖上游 Cython 扩展 `app.application.site.sites`，纯 python 无 `user.sites.v3.bin` 编译产物）
- 这是上游 v3 架构特性，非本次改动引入，不影响认证逻辑正确性（已用 py_compile + grep 验证）

### 上游更新冲突面变化
| 上游更新动作 | 合并前冲突面 | 本次后冲突面 |
|---|---|---|
| 重构 verify_token/adapters/web/security | 无（未碰） | 无（未碰） |
| 改 deps.py 聚合壳 | 无（改动在 dependencies/auth.py） | 无 |
| 改 config.py | 整段 PLUGIN_MARKET + 内联密钥持久化 | **仅 2 处 fork 方法调用**（persist_signature_secrets / _append_fork_plugin_markets），易 re-apply |
| 改 startup 结构 | agent_initializer 顶层 | 未处理（待决策） |


```bash
cd /opt/data/workspace/MoviePilot
export HOME=/opt/data/home
export CONFIG_DIR=/tmp/mp_ci_check
NEWUV=/opt/data/home/.local/bin/uv
# 架构 gate
$NEWUV run python -m pytest tests/test_architecture_dependencies.py tests/test_architecture_contract_baseline.py -q -p no:cacheprovider
# 功能冒烟
$NEWUV run python -c "import app.api.dependencies.auth, app.runtime.config, app.db.models.subscribe; print('OK')"
# 基线重生成
$NEWUV run python scripts/architecture/baseline.py --write-host
```
