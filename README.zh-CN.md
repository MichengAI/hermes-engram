<p align="center">
  <img src="assets/branding/hermes-engram-banner.png" alt="Hermes Engram — Project memory / 项目记忆" width="100%">
</p>

<div align="center">

# Hermes Engram

**让项目记忆，跟得上每一轮对话。**

[![MIT](https://img.shields.io/badge/License-MIT-06b6d4.svg)](LICENSE)
[![Hermes Agent Plugin](https://img.shields.io/badge/Hermes%20Agent-Memory%20Plugin-0f766e.svg)](https://github.com/NousResearch/hermes-agent)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab.svg?logo=python&logoColor=white)](pyproject.toml)
[![Engram](https://img.shields.io/badge/Engram-Project%20Memory-8b5cf6.svg)](https://github.com/Gentleman-Programming/engram)

[English](README.md) · **简体中文**

</div>

> 社区维护的 Hermes Agent 插件，不是 Hermes 或 Engram 官方产品。

让 Hermes 记住项目里的决定、修复经验和工作进展。换一个会话继续做同一项目时，不必每次从头交代背景。

[快速开始](#快速开始) · [日常使用](#日常使用) · [常见问题](#常见问题) · [开发文档](docs/00-交接入口/00-项目交接.md)

## 能做什么

- **按项目记忆**：根据会话工作目录或你明确指定的项目，召回相关背景。
- **保存重要结论**：提供记忆工具，让 Hermes 保存决定、经验和阶段总结；也会按规则自动记录提问和捕获工作结果。
- **跨会话继续工作**：已有记忆保存在本机 Engram 中，新会话可以继续查询和使用。
- **保留压缩后的背景**：符合归档条件时保存 Hermes 的正式会话摘要。

召回时，界面会显示类似提示：

```text
🧠 Engram·my-project — recalled 5 memories
```

这里的数字是本轮召回条数，不是项目的记忆总数。你可以直接问 Hermes 查看具体内容。

## 快速开始

### 1. 准备好 Hermes 和 Engram

需要能在终端运行 `hermes`、`git` 和 `engram`。尚未安装时，先按官方说明安装：

- [Hermes Agent 安装说明](https://hermes-agent.nousresearch.com/docs/getting-started/installation)
- [Engram 安装说明](https://github.com/Gentleman-Programming/engram/blob/main/docs/INSTALLATION.md)

插件不需要额外的 Python 包，也不需要单独配置 MCP 或启动 Engram 服务。

### 2. 用官方插件命令安装

在 PowerShell 中运行：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

hermes plugins install MichengAI/hermes-engram/engram --enable
hermes memory status
```

支持记忆插件自动选择的新版 Hermes 会把 Engram 设为当前记忆引擎。状态中应看到 `Provider: engram`，以及插件 `installed`、`available`。

**`--enable` 表示你同意使用 Engram 替换当前外部记忆引擎。Hermes 同时只能使用一个外部记忆引擎，但内置的 MEMORY.md / USER.md 不会因此停用。**

这是从社区 GitHub 仓库直接安装，不是官方目录中的已审查条目；安装器提示 `custom (unreviewed) source` 属于正常提醒。

如果安装后仍不是 Engram，补执行：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
hermes config set memory.provider engram
hermes memory status
```

### 3. 重启 Hermes，打开项目会话

重启正在使用的 Hermes 桌面端或终端，在你的项目目录开始对话。首次使用尚未登记的 Git 项目时，插件会在符合条件的本机会话中自动登记；已有 Engram 项目记忆可直接复用。

新项目还没有记忆时，不出现召回提示是正常的。可以先让 Hermes 记住一条项目结论，再开新会话查询。

## 日常使用

像平时一样对话即可；重要结论也可以明确要求保存：

> 记住这个项目的决定：接口统一使用游标分页，不使用页码分页。

> 当前项目有哪些记忆？

> 查一下之前为什么这样设计登录流程。

> 把这次修复的原因和解决方法保存到当前项目。

> 总结这次工作，留给下次继续。

**自动捕获不等于完整聊天备份，也不保证每个结果都会成为记忆。** 关键决定建议明确要求保存并确认结果。

## 隐私与使用边界

- 默认服务本机桌面、CLI/TUI 和 ACP 等场景；IM、定时任务与子代理上下文默认不自动写入记忆。
- 无法确认项目或出现多个候选项目时，跳过相应读写，不猜归属。
- 记忆保存在本机 Engram 数据目录；插件启动的 Engram 子进程关闭自动云同步。
- `<private>...</private>` 标记的内容会在保存前遮蔽。**这不是自动密钥扫描器，请勿把未标记的密码、密钥或个人敏感信息交给记忆保存。**
- 召回的历史内容只是参考，不能替代你当前的指令。

## 常见问题

### 插件装好了，为什么还没生效？

安装、选择记忆引擎和重启是三个环节。运行 `hermes memory status`，确认当前是 `engram` 且状态为 `available`，然后重启正在使用的 Hermes。

### Engram 不在 PATH 中怎么办？

指定实际安装位置的绝对路径即可。以下是示例，请换成你自己的路径：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
hermes config set plugins.engram.engram_path "C:\Tools\engram\engram.exe"
hermes memory status
```

修改后重启 Hermes。即使终端能找到 Engram，桌面端也可能还没继承新 PATH；显式设置路径可以解决这种情况。

### 可以只读取，不保存吗？

可以。以下设置会关闭自动保存和写入工具，保留召回、搜索与查看：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
hermes config set plugins.engram.auto_capture false
```

修改后重启 Hermes；恢复保存时把 `false` 改为 `true`。

### 如何更新？

通过上述官方命令安装后，运行以下命令，再重启 Hermes：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
hermes plugins update engram
```

旧版手动复制安装没有官方安装记录时，请参考[本地安装说明](docs/05-工程交付/00-开发与测试.md#本地安装)。

### 如何切回原来的记忆引擎？

把 `memory.provider` 改回原来的值，并重启 Hermes。例如，原来用 Holographic：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
hermes config set memory.provider holographic
```

切换不会删除已经保存的 Engram 记忆。

## 更多说明

普通使用不需要调整高级选项。配置、安全机制和开发验证资料分别放在：

- [项目交接与文档导航](docs/00-交接入口/00-项目交接.md)
- [记忆与安全机制](docs/03-技术架构/01-记忆与安全机制.md)
- [完整配置参考](docs/03-技术架构/02-配置参考.md)
- [开发与测试](docs/05-工程交付/00-开发与测试.md)

## 开源协议

[MIT](LICENSE) · [问题反馈](https://github.com/MichengAI/hermes-engram/issues)
