# hermes-engram

把 [Engram](https://github.com/Gentleman-Programming/engram) 接成 Hermes Agent 原生的 **memory provider**：每轮对话前按项目自动召回 Engram 记忆，界面上显示召回提示。参考官方 [Pi adapter](https://github.com/Gentleman-Programming/engram/tree/main/plugin/pi) 的输入过滤、private 脱敏、工具结果被动捕获及压缩摘要归档，不安装 Pi、不迁移到 HTTP。

```text
🧠 Engram·dsh-codex-ui — recalled 5 memories
```

不用单独配置 `mcp_servers.engram`：插件自己常驻一个 `engram mcp` 子进程，读写都走它，也提供 `engram_*` 工具给模型主动调用。

## 钩子对照（参考 Engram 官方 Codex / Pi 插件）

| Codex 钩子 | Hermes 钩子 | 插件行为 |
|---|---|---|
| `SessionStart` | 本会话第一次确定项目时（在 `prefetch()` 里） | `mem_session_start` 注册 Engram 会话；注入近期上下文（`mem_context`）和记忆协议 |
| `UserPromptSubmit` | `prefetch()` + `sync_turn()` | 每轮关键词检索召回（`mem_search`）；回合结束后后台 `mem_save_prompt` 记录用户提问 |
| （模型主动保存） | `engram_*` 工具 | `engram_save` / `engram_session_summary` / `engram_judge` / `engram_search` / `engram_get`，自动带会话 id 和项目 |
| Pi `tool_execution_end` | 插件 observer `post_tool_call` | 非记忆工具结果先解析 JSON 字符串并递归提取文本字段；每段脱敏后超过 50 字符的真实多行正文交给 `mem_capture_passive` |
| `SubagentStop` | `on_delegation()` | 即使工具 hook 已注册也捕获异步完成结果；与同步 `delegate_task` 捕获按本会话、项目、脱敏正文哈希去重 |
| Pi `session_compact` | `on_session_switch(reason="compression")` + 后续 `sync_turn(messages=...)` | 按压缩时项目归档 Hermes 正式摘要；`on_pre_compress()` 只重置召回，不再把对话摘录冒充正式摘要 |
| `SessionEnd` | `on_session_end()` | `mem_session_end` 关闭本会话注册过的 Engram 会话 |

Hermes 压缩上下文会换 session_id（`on_session_switch(reset=False)`），插件沿用同一个 Engram 会话；`/new` 会先关闭旧会话，再在新会话里重新注册。

同 id 原地压缩也支持。归档前必须观察到 `reason="compression"`，并在后续完成回合的 `messages` 中找到宿主的正式摘要前缀/结束标记；只取摘要正文，去掉 handoff 提示，不生成替代摘要。

### 输入与隐私

- 提问先去首尾空白和 `<private>...</private>` 块，再判断长度；默认 **超过 10 字符**且非 trivial 输入才记录，可配置 `prompt_min_chars`。长度是 Python 字符数，不是 UTF-8 字节数。
- 跳过 Hermes 已知的系统、压缩、异步委派、cron 等内部标记，以及宿主的压缩续写/迭代上限总结提示；`turn_author.is_bot=true` 的提问也跳过。真人 OUT-OF-BAND 消息不因包装标记被丢弃。
- 显式 private 块（不区分大小写、支持跨行）替换为 `[REDACTED]`。提问、工具结果、子代理结果在截断前脱敏；召回在关键词切分前脱敏；MCP 出站参数递归脱敏，覆盖主动保存及总结等路径。
- 这是与 Pi 同义的**配对标签约定**，不是通用密钥/个人信息扫描器；不应把未加标签的秘密当作可安全存档的数据。

### 已核实的宿主加载路径

本机 Hermes 的 `plugins.memory._load_provider_from_dir()` 使用 `_ProviderCollector.collect()`，其 `register_hook()` 会将注册转发给真实 `PluginContext` 的 fallback hook registry。插件的 `register()` 将 **同一个 provider 实例**交给 `register_memory_provider()` 和 `post_tool_call`；通过 `model_tools._emit_post_tool_call_hook()` → `hermes_cli.lifecycle` 调用链验证捕获。

旧宿主若不支持 `register_hook` 或注册抛错，明确记录「工具捕获不可用」，provider 其余功能继续工作；不会伪造已注册。全局 hook 只有携带本 provider 当前 `session_id` 的结果才能写入，缺失 id、其他会话和子代理 id 一律跳过。

工具正文提取支持嵌套 dict/list 中的 `text`、`content`、`summary`、`output`、`stdout`、`stderr`、`result` 字段，以及这些字段里的 JSON 字符串（递归深度上限 20）。不重新 `json.dumps` 正文，保留真实换行，避免 Engram 的行首 Markdown 标题/编号提取失效；状态、计数、目标、note 等元数据不作为正文捕获。

本机宿主同步委派先通过 `tools.delegate_tool_results._notify_memory_manager()` 通知 `memory.on_delegation`，随后工具 hook 收到 `{"results":[{"summary":"..."}],"total_duration_seconds":...}` 的 JSON 字符串。默认后台委派的即时工具返回只有 `status="dispatched"`，完成正文仍由 `memory.on_delegation` 传递，不能因为 hook 已注册就禁用它。同步批次按每个 summary 正文独立去重，回调先后顺序不影响结果；不同正文继续捕获。去重只保留当前 provider 内本会话/项目的已入队哈希，不是跨重启或失败重试保证。

工具和子代理正文先脱敏，再在入队前截断到 **30000 UTF-8 字节**，后台闭包不持有完整巨大结果或原始 JSON 包装。仅有 `dispatched` 元数据不会触发捕获。捕获仍须满足平台、primary、会话 id、已知项目和确认归属的 Engram 会话边界。

### 压缩归档状态与去重

`provider.compaction_status(session_id=...)` 返回项目 + 脱敏正文哈希对应的状态，不返回正文：`pending`（排队）、`saved`（MCP 成功返回）、`failed`（明确 MCP 拒绝）、`unknown`（超时/异常，可能已写入）。同一会话 lineage 的同项目同摘要只派发一次，压缩轮换/原地压缩沿用去重表；`/new` 重置。

失败或未知状态**不自动重放**，避免未知提交被重复写入；需要重试时可显式调用 `engram_session_summary`。状态和去重表只在当前 provider 进程内存在，不是跨重启 exactly-once 保证。

## 安全边界

- **项目判定**：点名唯一项目 > 会话工作目录绑定 > 本会话沿用。点名多个、写了不存在的项目、判断不了时一律跳过，**绝不跨项目读写**。
- **本轮归属与追问历史分离**：每次 `prefetch` 先撤销本轮确认；多项目、未知项目、项目解析异常或空输入不会继承上轮写权限。提问记录、工具/委派被动捕获和省略 `project` 的主动工具只使用本轮已确认项目；拒绝轮的压缩也不能借历史项目归档。历史项目仍保留，下一次合法追问重新解析后可恢复（即使没有新召回正文）。显式工具 `project` 仍须属于已知项目，不自动建项目；`engram_get` / `engram_judge` 按显式记录/判断 id 工作，不依赖默认项目。
- **来源同步绑定**：`prefetch` 保存脱敏正文哈希与当时项目判定（包括拒绝）。宿主延迟执行 `sync_turn` 时只匹配来源记录，不读取最新项目；来源缺失、正文转换不一致或同文判定冲突时跳过。`on_turn_start` 撤销上一轮权限并阻止陈旧召回发布；没有回合 ID 的同文重复保守拒绝。最多保留 2048 个来源键，达到容量后停止本会话提问自动保存，不淘汰拒绝记录重新授权。
- **后台归属快照**：provider 收到任务后固定项目；后台委派从工具参数 `tasks[].goal` 记录派发时会话/项目，完成通知按该来源匹配。冲突、容量耗尽或 `/new` 后的旧任务不写；缺少派发证据的旧宿主路径仅保留现有完成回调能力，不承诺恢复准确回合。正式摘要保留压缩时已确认的归属。
- **首次项目登记**：`auto_create_projects=true` 时，未绑定目录只在真实会话 cwd 位于 Git 仓库、允许 primary/本机写入且没有未知/多项目点名时初始化。通过 `git rev-parse` 确认根目录，调用 `mem_session_start(id, directory)`，采用 Engram canonical 项目名并刷新目录绑定表核对。兼容本机 Engram 3.0.0，不依赖其不支持的 `mem_current_project(cwd)` 参数。普通目录、父目录子仓库扫描、用户主目录与后端进程 cwd 不触发首次登记；这是比官方普通目录名回退更严格的边界。Engram 可能在 `.git` 的共享元数据内创建私有项目身份文件，Git worktree 复用该身份。
- **已有项目会话注册**：点名项目必须已有，且会话目录绑定与 Engram 注册应答一致才挂会话；不会根据任意未知文本创建项目。
- **没有确认归属的会话**：只读召回照常；`engram_save` 等工具改用显式 `project`、不挂会话；提问记录和被动捕获直接跳过（Engram 在缺会话时会按进程目录猜项目）。
- **谁能写**：只在 primary 上下文和允许的平台写入。子代理、cron、IM 渠道不写；写工具在这些场景下直接返回错误。
- **不阻塞对话**：写入都放进一个后台线程串行执行；召回有单轮总预算，超时就跳过。
- **不泄露内容**：失败日志只记异常类别，不记用户问题和记忆正文；子进程强制关闭云同步（`ENGRAM_CLOUD_AUTOSYNC=0`），并忽略外部的 `ENGRAM_PROJECT`。
- **用户主目录、盘符根**这类宽泛的目录绑定只允许精确匹配，不会兜住它下面的所有子目录。

## 安装

需要先装好 Engram（`engram mcp` 可用）。插件不依赖第三方 Python 包。

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 1. 复制插件到 $HERMES_HOME\plugins\engram（已有旧版本会先备份为 .engram.bak）
python D:\Repository\hermes-plugins\hermes-engram\scripts\install.py

# 2. 指定 engram 路径并切换 memory provider（同一时间只能有一个外部 provider，会替换 holographic）
hermes config set plugins.engram.engram_path D:/Tools/engram/engram.exe
hermes config set memory.provider engram

# 3. 确认
hermes memory status
```

切换后重启 Hermes（桌面端 / 网关）才会生效。不需要再配 `mcp_servers.engram`；如果还留着，会和 `engram_*` 工具重复。

### 只读模式

不想自动写入时：

```powershell
hermes config set plugins.engram.auto_capture false
```

此时不注册会话、不记录提问、不做被动捕获和压缩存档，也不暴露写工具，只保留召回和 `engram_search` / `engram_get`。

### 回滚

```powershell
hermes config set memory.provider holographic
```

## 配置

都在 `config.yaml` 的 `plugins.engram` 下，全部可选：

| 键 | 默认值 | 说明 |
|---|---|---|
| `engram_path` | PATH 中的 `engram` | engram 可执行文件路径 |
| `platforms` | `[desktop, cli, tui, gui, local, acp]` | 允许召回和写入的平台；IM 渠道默认不在内 |
| `auto_capture` | `true` | 自动写入总开关（会话注册、提问记录、被动捕获、压缩存档、会话关闭、写工具） |
| `auto_create_projects` | `true` | 从真实会话 cwd 的未绑定 Git 仓库首次登记项目；项目名由 Engram 解析 |
| `capture_prompts` | `true` | 按来源证据记录用户提问（`mem_save_prompt`） |
| `prompt_min_chars` | `10` | 脱敏后的提问必须超过此字符数才记录 |
| `capture_tools` | `true` | 通过真实 `post_tool_call` 被动捕获非记忆工具结果；依赖宿主加载器提供 hook 注册 |
| `capture_delegation` | `true` | 捕获子代理完成正文（包括默认后台委派）；同步工具重复正文按会话/项目去重 |
| `compaction_summary` | `true` | 完成回合同步时归档正式压缩摘要（`mem_session_summary`），不是压缩前摘录 |
| `tools` | `true` | 注册 `engram_*` 工具并注入记忆协议 |
| `max_bytes` | `6000` | 单轮注入总字节上限（UTF-8），含记忆协议 |
| `context_bytes` | `3000` | 近期上下文字节上限 |
| `search_bytes` | `2200` | 检索结果字节上限 |
| `search_limit` | `5` | 每轮检索条数 |
| `call_timeout` | `4.0` | 召回时单次 MCP 调用超时（秒） |
| `total_budget` | `7.0` | 单轮召回总预算（秒），要小于 Hermes 外部 provider 的 8 秒上限 |
| `write_timeout` | `8.0` | 后台写入和工具调用的单次超时（秒） |
| `exact_only_dirs` | `[]` | 额外的「只允许精确匹配」绑定目录；用户主目录和盘符根始终如此 |

## 目录结构

```text
engram/              # 插件本体，安装时整个复制到 $HERMES_HOME/plugins/engram/
  __init__.py        # EngramMemoryProvider + register()：钩子、会话注册、后台写入、工具分发
  capture.py         # 输入识别、private 脱敏、正式摘要提取、会话 id、记忆协议、工具 schema
  recall.py          # 纯函数：项目判定、中文二元组检索词、字节截断、结果格式化
  mcp_client.py      # 常驻 engram mcp 子进程的极简 MCP stdio 客户端（懒启动、超时即重启）
  plugin.yaml
scripts/
  install.py         # 安装到 $HERMES_HOME/plugins/engram/
  e2e_smoke.py       # 隔离 HERMES_HOME + 真实 engram 的端到端冒烟（--write 用临时 Engram 库验证写入）
tests/               # pytest，后端用假 MCP 服务，接口直接引用本机 Hermes 源码
```

## 开发与测试

测试直接 import 本机 Hermes 源码（默认 `%LOCALAPPDATA%\hermes\hermes-agent`，可用 `HERMES_AGENT_DIR` 覆盖），用 Hermes 自己的 venv 跑：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$py = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe"

# 单元 + 集成测试（不改 Hermes venv，pytest 由 uv 临时提供）
uv run --no-project --python $py --with pytest python -m pytest -q

# 端到端·只读：Hermes 加载器 + 真实 Engram 库，只输出字节数、耗时和召回提示
& $py scripts\e2e_smoke.py --engram D:\Tools\engram\engram.exe --cwd D:\Repository\deepseek-harness-plugin\dsh-codex-ui

# 端到端·写入：在 Hermes scratch 下创建临时 HERMES_HOME / ENGRAM_DATA_DIR，结束后删除
& $py scripts\e2e_smoke.py --engram D:\Tools\engram\engram.exe --cwd D:\Repository\hermes-plugins\hermes-engram --write
```

`--write` 使用真实 Hermes provider 加载器、真实工具 observer 发射函数、真实委派 memory 通知函数及真实 `engram mcp`。夹具采用宿主真实 `delegate_task` 同步 JSON 包装和后台 dispatched/完成路径，最后只读回临时 SQLite，分别断言同步重复正文、仅工具 JSON 正文、后台完成正文各落库一次，并核实项目/会话归属、private 脱敏、提问过滤、正式摘要单次归档及会话关闭。摘要测试夹具采用宿主正式标记格式，**没有请求在线模型，不宣称验证了 LLM 生成质量或实际子代理调度**。pytest 覆盖真实加载器/MemoryManager 集成及明确拒绝、超时状态；假 MCP 只确认传输，不伪报 extracted/saved 数量，真实提取落库由隔离库 e2e 验证。

## 已知限制

- Hermes 的 `sync_turn` 不带回合 ID，因此正文哈希配对是保守来源证据，不是完整的回合身份；无法可靠配对时会少记，不猜目标项目。
- 检索是关键词规则（中文按二元组切分），不是语义召回。
- 外部 memory provider 同一时间只能启用一个，启用 Engram 会停用 holographic。
- 召回内容在聊天界面不显示正文，只显示召回提示；完整内容在发给模型的 `api_content` 里。
- 被动捕获依赖 Engram 的 `## Key Learnings:` 提取规则：实测中文条目要用空格分词才会被提取，整句不带空格的中文会被忽略。记忆协议里已提示模型这样写。
- Hermes memory provider 没有 `on_post_compress(summary=...)` 生命周期参数；`on_session_switch` 只通知 reason/id。归档因此延后到携带正式摘要的完成回合同步，若回合被中断、宿主不传 messages、没有正式标记或进程提前退出，本次不会自动归档；不承诺压缩前 durable checkpoint。
- Hermes 未提供 Pi 的输入 `source="extension"` provenance；只能过滤已核实的内部标记/常量与机器人作者，未标记的扩展输入无法可靠识别，不能声称全部合成输入已跳过。
- 当前摘要提取依赖宿主摘要常量和 carrier 标记格式；不支持未确认的旧格式或 provider-native opaque compaction。归档状态没有额外 UI 提示，供代码检查，不修改系统提示缓存。
- Hermes 先读取工具 schema 再初始化 provider，因此 `engram_*` 工具在 IM 渠道也会出现在工具列表里，但写工具调用会被拒绝。
