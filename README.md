# MindAgent

MindAgent 是一个多用户智能体工作台。系统为每名用户提供一个独立 Session 和 Workspace，支持多用户并发、简化 Scroll 上下文、全局 RAG 知识库，以及由 Sub-agent 执行并由主 Agent 验收的异步长任务。首版通过 QQ 私聊和群聊触发 Agent，并允许 Agent 按需查询触发窗口的消息记录；聊天窗口不是 Session 的划分依据。

首个版本采用单机自托管方式，只实现基于 NapCat / OneBot v11 的 QQ 接入，并通过统一的 `ChannelMessage` 模型隔离 OneBot 与 Agent。

## 项目文档

- [业务提案](docs/proposal.md)：产品目标与首版范围；
- [强制规范](docs/spec.md)：消息、上下文、人设、知识库、任务与验收红线；
- [技术设计](docs/design.md)：首版架构、存储、目录和实现方向。

实现不得扩大业务提案的范围，也不得违反强制规范；技术设计用于指导当前首版实现。
