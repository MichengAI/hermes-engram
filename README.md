<p align="center">
  <img src="assets/branding/hermes-engram-banner.png" alt="Hermes Engram — Project memory / 项目记忆" width="100%">
</p>

<div align="center">

# Hermes Engram

**Project memory that stays with you.**

[![MIT](https://img.shields.io/badge/License-MIT-06b6d4.svg)](LICENSE)
[![Hermes Agent Plugin](https://img.shields.io/badge/Hermes%20Agent-Memory%20Plugin-0f766e.svg)](https://github.com/NousResearch/hermes-agent)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab.svg?logo=python&logoColor=white)](pyproject.toml)
[![Engram](https://img.shields.io/badge/Engram-Project%20Memory-8b5cf6.svg)](https://github.com/Gentleman-Programming/engram)

**English** · [简体中文](README.zh-CN.md)

</div>

> A community-maintained Hermes Agent plugin. Not an official Hermes or Engram product.

Help Hermes remember project decisions, fixes, and progress. Pick up the same project in a new session without explaining everything again.

[Quick start](#quick-start) · [Everyday use](#everyday-use) · [FAQ](#faq) · [Developer docs](docs/00-交接入口/00-项目交接.md)

## What it does

- **Recall by project**: retrieve relevant background from your session's working directory or the project you explicitly name.
- **Save useful conclusions**: give Hermes tools to save decisions, lessons, and session summaries; automatically record prompts and capture work results when they meet the capture rules.
- **Continue across sessions**: keep memories in your local Engram store so new sessions can search and use them.
- **Keep context after compaction**: archive Hermes' formal session summaries when the archive requirements are met.

When memories are recalled, you will see a notice such as:

```text
🧠 Engram·my-project — recalled 5 memories
```

The number is the count recalled for this turn, not the project's total. Ask Hermes to show the memories if you want to read them.

## Quick start

### 1. Have Hermes and Engram ready

You need `hermes`, `git`, and `engram` available in your terminal. If they are not installed yet, follow the official guides:

- [Install Hermes Agent](https://hermes-agent.nousresearch.com/docs/getting-started/installation)
- [Install Engram](https://github.com/Gentleman-Programming/engram/blob/main/docs/INSTALLATION.md)

The plugin needs no additional Python packages. You do not need to configure a separate MCP server or start an Engram service yourself.

### 2. Install with the official plugin command

Run in PowerShell:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

hermes plugins install MichengAI/hermes-engram/engram --enable
hermes memory status
```

Recent Hermes versions that support automatic memory-provider selection will select Engram for you. The status should show `Provider: engram`, with the plugin `installed` and `available`.

**`--enable` means you agree to replace your current external memory provider with Engram. Hermes can use one external provider at a time; its built-in MEMORY.md / USER.md memory remains enabled.**

This installs directly from a community GitHub repository, not a reviewed entry in the official catalog. The installer's `custom (unreviewed) source` warning is expected.

If Engram is not selected after installation, run:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
hermes config set memory.provider engram
hermes memory status
```

### 3. Restart Hermes and open a project session

Restart the Hermes desktop app or terminal session you use, then start a conversation in your project directory. An unregistered Git project can be registered automatically in an eligible local session. Existing Engram project memories can be reused.

A new project with no memories may not show a recall notice. Ask Hermes to save a project decision first, then query it from a new session.

## Everyday use

Chat as usual, or explicitly ask Hermes to save something important:

> Remember this project's decision: use cursor pagination, not page-number pagination.

> What memories do we have for the current project?

> Look up why we designed the login flow this way.

> Save the root cause and solution for this fix to the current project.

> Summarize this work so we can continue next time.

**Automatic capture is not a full chat backup, and not every result becomes a memory.** For important decisions, explicitly request a save and confirm the result.

## Privacy and boundaries

- Intended for local desktop, CLI/TUI, ACP, and similar sessions. Messaging channels, scheduled tasks, and subagent contexts do not automatically write memories by default.
- If project ownership is unclear or ambiguous, the corresponding reads or writes are skipped rather than guessing.
- Memories stay in your local Engram data directory. Engram subprocesses started by the plugin have automatic cloud sync disabled.
- Content marked with `<private>...</private>` is redacted before saving. **This is not an automatic secret scanner. Do not save unmarked passwords, keys, or sensitive personal information.**
- Recalled history is reference material, not a replacement for your current instructions.

## FAQ

### Installed, but not active?

Installation, provider selection, and restarting are separate steps. Run `hermes memory status`, confirm `engram` is the current provider and is `available`, then restart Hermes.

### What if Engram is not on PATH?

Set the absolute path to your actual installation. Replace the example below with your own path:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
hermes config set plugins.engram.engram_path "C:\Tools\engram\engram.exe"
hermes memory status
```

Restart Hermes afterward. Your desktop app may not have inherited a newly updated PATH even when your terminal can find Engram; an explicit path avoids that issue.

### Can I use read-only mode?

Yes. This disables automatic saving and write tools while keeping recall, search, and viewing:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
hermes config set plugins.engram.auto_capture false
```

Restart Hermes after changing it. Set the value back to `true` to restore saving.

### How do I update?

After installing with the official command above, run this and restart Hermes:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
hermes plugins update engram
```

Older manually copied installations may not have an official install record. See the [local installation notes](docs/05-工程交付/00-开发与测试.md#本地安装) (Chinese).

### How do I switch back to my previous provider?

Restore the previous value of `memory.provider` and restart Hermes. For example, if you used Holographic:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
hermes config set memory.provider holographic
```

Switching providers does not delete your saved Engram memories.

## Further reading

You do not need advanced settings for everyday use. The detailed reference documents below are currently in Chinese:

- [Project handover and documentation index](docs/00-交接入口/00-项目交接.md)
- [Memory and safety mechanisms](docs/03-技术架构/01-记忆与安全机制.md)
- [Full configuration reference](docs/03-技术架构/02-配置参考.md)
- [Development and testing](docs/05-工程交付/00-开发与测试.md)

## License

[MIT](LICENSE) · [Report an issue](https://github.com/MichengAI/hermes-engram/issues)
