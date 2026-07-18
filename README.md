# MindAgent

MindAgent 是支持群聊交互场景的多用户智能体工作台。系统为每名用户提供独立 Workspace，支持群聊并发、简化 Scroll 上下文、公共知识库，以及由 Sub-agent 执行并由主 Agent 验收的异步长任务。

首个版本采用单机自托管方式，只实现基于 NapCat / OneBot v11 的 QQ 接入，并通过统一消息模型隔离 OneBot 与 Agent。

## 项目文档

- [业务提案](docs/proposal.md)：产品目标、首版范围与明确非目标；
- [强制规范](docs/spec.md)：消息、上下文、人设、知识库、任务与验收红线；
- [技术设计](docs/design.md)：首版架构、存储、目录和实现方向。

实现不得扩大业务提案的范围，也不得违反强制规范；技术设计用于指导当前首版实现。
