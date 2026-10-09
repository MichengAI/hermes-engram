# Hermes Engram 已安装

接下来只需确认记忆引擎，再重启 Hermes：

1. 先确保本机 Engram 已安装。插件不需要额外配置 MCP 或启动服务。
2. 使用 `hermes memory status` 确认当前 provider 为 `engram`，状态为 `available`。若尚未选择，运行 `hermes config set memory.provider engram`。
3. Engram 不在 PATH 时，用 `hermes config set plugins.engram.engram_path <绝对路径>` 指定可执行文件。
4. 重启正在使用的 Hermes，打开项目会话，问“当前项目有哪些记忆？”。新项目尚无记忆时，先保存一条项目结论再查询。

**Engram 会替换当前外部记忆引擎，但不会停用内置 MEMORY.md / USER.md。**

完整说明：https://github.com/MichengAI/hermes-engram
