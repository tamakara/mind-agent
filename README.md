# MindAgent

MindAgent 是一个多用户智能体工作台。系统为每名用户提供一个独立 Session 和 Workspace，支持多用户并发、带 headline 导航的简化 Scroll 上下文、全局 RAG 知识库、全局 Skills、可管理的内置工具与 MCP，以及由 Sub-agent 执行并由主 Agent 验收的异步长任务。首版通过 QQ 私聊触发 Agent；本地历史缺失时，可按需查询当前私聊的 QQ 原始记录作为补偿，用户的上下文、文件和任务始终按用户隔离。

首个版本采用单机自托管方式，只实现基于 NapCat / OneBot v11 的 QQ 接入，并通过统一的 `ChannelMessage` 模型隔离 OneBot 与 Agent。

每次 Agent run 按顺序全量加载全局 `AGENTS.md`、`SOUL.md` 和当前用户 Workspace 的 `PROFILE.md`、`MEMORY.md`，再取得当前启用的内置工具、MCP 工具与 Skill 目录快照。全局规则和 Agent 能力由管理员维护，用户资料与长期记忆按用户隔离并通过专用工具更新。

## 项目文档

- [业务提案](docs/proposal.md)：产品目标与首版范围；
- [强制规范](docs/spec.md)：消息、上下文、人设、Skills、工具、MCP、知识库、任务与验收红线；
- [技术设计](docs/design.md)：首版架构、存储、目录和实现方向；
- [实现任务清单](task.md)：可持续勾选的分阶段实现进度。

实现不得扩大业务提案的范围，也不得违反强制规范；技术设计用于指导当前首版实现。
