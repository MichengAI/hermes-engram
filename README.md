# hermes-engram

把 [Engram](https://github.com/Gentleman-Programming/engram) 接成 Hermes Agent 原生的 **memory provider**。每轮对话前按项目只读召回 Engram 记忆，注入到当前用户消息里，界面上会显示召回提示：

```text
🧠 Engram·dsh-codex-ui — recalled 5 memories
```

用来替代原来的「MCP + PowerShell `pre_llm_call` Hook」方案：不用每轮起 PowerShell（实测首轮约 0.4 秒，之后几十毫秒），注入了能在界面上看到，压缩后会自动补读。

## 当前范围（第一期：只读）

| 能力 | 说明 |
|---|---|
| 自动召回 | `prefetch()`：近期上下文（`mem_context`）+ 关键词检索（`mem_search`），只读 |
| 召回提示 | `recall_status()`：界面显示项目名和召回条数 |
| 去重 | 同一会话里近期上下文只注入一次，检索结果按 observation id 去重 |
| 压缩后补读 | `on_pre_compress()` 清空注入记录，下一轮重新读取 |
| 会话切换 | `/new` 清空状态；压缩、恢复、分支沿用项目并重新注入 |
| IM 渠道 | 默认不注入，只放行 desktop / cli / tui / gui / local / acp |
| 工具 | **不注册工具**。主动读写继续用 `mcp_servers.engram` 提供的 `mcp__engram__*` |

不做：自动保存、会话总结、压缩前存档（计划放在第二期，默认关闭）。

## 项目判定规则

按以下优先级选一个项目，**判断不了就跳过，绝不跨项目读取**：

1. 消息里点名且只点名一个已有项目（如「继续 renren-drama 项目……」）
2. 会话工作目录命中 Engram 项目目录绑定（取最长前缀；同长度多个项目算歧义）。会话工作目录从 `state.db` 的 `sessions.cwd` 读取，不用进程 cwd
3. 本会话上次读取过的项目（不点名的追问）

以下情况跳过：点名多个项目；写了「项目: xxx」但 xxx 不存在；用户主目录、盘符根这类宽泛绑定只允许精确匹配，不会兜住它下面的所有子目录。

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

切换后重启 Hermes（桌面端 / 网关）才会生效。如果原来配置了 `hooks.pre_llm_call` 里的 `engram-recall.ps1`，要一并移除，否则会重复注入。

### 回滚

```powershell
hermes config set memory.provider holographic
```

再把原来的 `hooks.pre_llm_call` 配置恢复即可。

## 配置

都在 `config.yaml` 的 `plugins.engram` 下，全部可选：

| 键 | 默认值 | 说明 |
|---|---|---|
| `engram_path` | PATH 中的 `engram` | engram 可执行文件路径 |
| `platforms` | `[desktop, cli, tui, gui, local, acp]` | 允许自动召回的平台 |
| `max_bytes` | `6000` | 单轮注入总字节上限（UTF-8） |
| `context_bytes` | `3000` | 近期上下文字节上限 |
| `search_bytes` | `2200` | 检索结果字节上限 |
| `search_limit` | `5` | 每轮检索条数 |
| `call_timeout` | `4.0` | 单次 MCP 调用超时（秒） |
| `total_budget` | `7.0` | 单轮召回总预算（秒），要小于 Hermes 外部 provider 的 8 秒上限 |
| `exact_only_dirs` | `[]` | 额外的「只允许精确匹配」绑定目录；用户主目录和盘符根始终如此 |

## 目录结构

```text
engram/              # 插件本体，安装时整个复制到 $HERMES_HOME/plugins/engram/
  __init__.py        # EngramMemoryProvider + register()
  mcp_client.py      # 常驻 engram mcp 子进程的极简 MCP stdio 客户端（懒启动、超时即重启）
  recall.py          # 纯函数：项目判定、中文二元组检索词、字节截断、结果格式化
  plugin.yaml
scripts/
  install.py         # 安装到 $HERMES_HOME/plugins/engram/
  e2e_smoke.py       # 隔离 HERMES_HOME + 真实 Engram 的端到端冒烟
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

# 端到端：Hermes 加载器 + 真实 Engram 库，只输出字节数、耗时和召回提示
& $py scripts\e2e_smoke.py --engram D:\Tools\engram\engram.exe --cwd D:\Repository\deepseek-harness-plugin\dsh-codex-ui
```

## 已知限制

- 检索是关键词规则（中文按二元组切分），不是语义召回。
- 外部 memory provider 同一时间只能启用一个，启用 Engram 会停用 holographic。
- 召回内容在聊天界面不显示正文，只显示召回提示；完整内容在发给模型的 `api_content` 里。
