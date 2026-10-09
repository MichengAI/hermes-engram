# hermes-engram

把 [Engram](https://github.com/Gentleman-Programming/engram) 接成 Hermes Agent 原生的 **memory provider**：每轮对话前按项目自动召回 Engram 记忆，界面上显示召回提示；对话过程中按 Engram 官方 Codex 插件的钩子时机自动存取。

```text
🧠 Engram·dsh-codex-ui — recalled 5 memories
```

不用单独配置 `mcp_servers.engram`：插件自己常驻一个 `engram mcp` 子进程，读写都走它，也提供 `engram_*` 工具给模型主动调用。

## 钩子对照（参考 Engram 官方 Codex 插件）

| Codex 钩子 | Hermes 钩子 | 插件行为 |
|---|---|---|
| `SessionStart` | 本会话第一次确定项目时（在 `prefetch()` 里） | `mem_session_start` 注册 Engram 会话；注入近期上下文（`mem_context`）和记忆协议 |
| `UserPromptSubmit` | `prefetch()` + `sync_turn()` | 每轮关键词检索召回（`mem_search`）；回合结束后后台 `mem_save_prompt` 记录用户提问 |
| （模型主动保存） | `engram_*` 工具 | `engram_save` / `engram_session_summary` / `engram_judge` / `engram_search` / `engram_get`，自动带会话 id 和项目 |
| `SubagentStop` | `on_delegation()` | `mem_capture_passive` 被动捕获子代理结论里的 `## Key Learnings:` |
| `SessionStart:compact` | `on_pre_compress()` | 压缩前把对话摘录存成 `mem_session_summary`；下一轮重新注入上下文 |
| `SessionEnd` | `on_session_end()` | `mem_session_end` 关闭本会话注册过的 Engram 会话 |

Hermes 压缩上下文会换 session_id（`on_session_switch(reset=False)`），插件沿用同一个 Engram 会话；`/new` 会先关闭旧会话，再在新会话里重新注册。

## 安全边界

- **项目判定**：点名唯一项目 > 会话工作目录绑定 > 本会话沿用。点名多个、写了不存在的项目、判断不了时一律跳过，**绝不跨项目读写**。
- **会话注册要能确认归属**：只有会话工作目录在 Engram 的项目目录绑定表里、且 Engram 解析出的项目与目标一致时才注册。未绑定目录（如 `D:\AI\HermesData`）不注册，避免凭空多出一个按目录名起的项目。
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
| `capture_prompts` | `true` | 每轮记录用户提问（`mem_save_prompt`） |
| `capture_delegation` | `true` | 子代理结果被动捕获（`mem_capture_passive`） |
| `compaction_summary` | `true` | 压缩前存会话总结（`mem_session_summary`） |
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
  capture.py         # 纯函数：Engram 会话 id、压缩前总结、记忆协议、工具 schema
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

# 端到端·写入：用临时 ENGRAM_DATA_DIR 走完整的自动存取，不碰真实库，结束后删除
& $py scripts\e2e_smoke.py --engram D:\Tools\engram\engram.exe --cwd D:\Repository\hermes-plugins\hermes-engram --write
```

## 已知限制

- 检索是关键词规则（中文按二元组切分），不是语义召回。
- 外部 memory provider 同一时间只能启用一个，启用 Engram 会停用 holographic。
- 召回内容在聊天界面不显示正文，只显示召回提示；完整内容在发给模型的 `api_content` 里。
- 被动捕获依赖 Engram 的 `## Key Learnings:` 提取规则：实测中文条目要用空格分词才会被提取，整句不带空格的中文会被忽略。记忆协议里已提示模型这样写。
- 压缩前存档是对话摘录，不是模型生成的摘要；模型主动调用 `engram_session_summary` 写的总结质量更高。
- Hermes 先读取工具 schema 再初始化 provider，因此 `engram_*` 工具在 IM 渠道也会出现在工具列表里，但写工具调用会被拒绝。
