# MindAgent 业务提案

> 状态：Accepted
>
> 本文定义首版产品模型、用户可见能力和范围。强制行为见 [spec.md](spec.md)，实现方案见 [design.md](design.md)。

## 1. 产品定位与核心模型

MindAgent 是一个多用户智能体工作台。一个实例服务一个团队，每名用户拥有一个独立 Session 和 Workspace。用户之间的上下文、文件、任务和用户资料完全隔离。

首版聚焦 QQ 私聊一对一交互。QQ 私聊只是外部入口，不决定 Session 或 Workspace 的划分；同一用户的所有私聊都进入同一个 Session。

## 2. 用户、Session 与 Workspace

- 用户首次发送有效私聊时自动创建；
- 每名用户拥有一个唯一 Session 和一个唯一 Workspace；
- Session 保存该用户连续的 Agent 上下文；
- Workspace 保存该用户的逐字历史、PROFILE/MEMORY、文件、产物和异步任务；
- 同一用户的 run 按顺序执行，不同用户可以并发执行；
- 管理员可以查看和管理全部 Workspace，普通用户不登录 Web。

## 3. 入口

### QQ 私聊

- 使用 NapCat / OneBot v11 接入 QQ 私聊；
- 每条私聊消息直接触发 Agent；
- Agent 优先使用本地 Session 历史；本地历史缺失时才查询 QQ 原始私聊记录；
- 支持文本、提及、引用、图片、语音、视频和文件等常用消息。

### 管理员 Web

首版只提供管理员工作台。菜单按 MindAgent 自身的领域边界组织，而不是把全局 Agent 能力归入用户 Workspace：

- 概览；
- 管理：用户、知识库、任务；
- 智能体：人设文件、Skills、内置工具、MCP；
- 设置：模型、QQ 状态。

用户页面同时承载用户与其 Workspace 管理；全局人设、Skills、内置工具和 MCP 是独立的全局 Agent 能力。QQ 状态页保持只读，首版所有 Agent 交互只通过 QQ 私聊进行。

## 4. Agent 能力

### Runtime

主 Agent 负责实时会话和短任务。每次请求加载当前用户的运行时上下文、四个人设/记忆文件，以及当前启用的内置工具、MCP 工具和 Skill 目录快照，完成一次 Agent run 后返回文本或产物。

### Context / Scroll

触发消息、Agent 回复、工具调用和结果逐字保存。完整用户回合使用 `turn_id` 组织，最终回复带有用于被驱逐历史导航的 headline。上下文只保留最近完整回合，旧内容可以按 seq 展开或搜索。

### Persona / Profile / Memory

全局 `AGENTS.md` 与 `SOUL.md` 定义团队共用的规则和 Agent 身份；每个用户 Workspace 的 `PROFILE.md` 与 `MEMORY.md` 定义该用户的资料、偏好、决策和工作约定。四个文件按固定顺序全量注入当前用户的 Agent run。

Agent 只能通过专用工具更新当前用户的 PROFILE/MEMORY；不保存原始聊天日志，不跨用户召回。

### Tasks / Sub-agent

耗时任务由 Sub-agent 在后台执行。Sub-agent 只提交候选结果，主 Agent 负责验收；首轮失败自动返工一次，仍失败则结束任务并说明原因。

### Knowledge / RAG

知识库由管理员维护，对所有 Agent 只读共享。Agent 显式搜索、列举和读取知识文档，不在每轮对话中自动注入全部知识。

### 内置工具

内置工具由代码注册、管理员全局启停。每次 Agent run 在开始时取得工具配置快照，修改从下一次 run 生效；工具启停不能绕过 Workspace 隔离、Persona 特殊文件保护或其他安全边界。

### MCP

管理员可以配置全局 MCP 客户端，首版支持 stdio 和 Streamable HTTP。每个客户端可以控制哪些工具暴露给 Agent，并为全部工具设置 `ask`、`allow` 或 `deny` 默认策略，也可以按工具覆盖该策略。系统不提供按用户、渠道或 Session 的详细规则。

需要审批的调用由当前 QQ 用户在原私聊中确认。单个 MCP 客户端故障不影响其他客户端或 Agent 的基础能力。

### Skills

Skills 是全局、管理员维护的指令与参考资料包。每个 Skill 使用包含 `SKILL.md` 的独立目录，可包含 references、scripts 和其他资源；Agent 只加载启用 Skill 的目录信息，并按需读取正文和参考资料。

首版可以导入和查看 scripts，但不执行 Skill 脚本，不提供技能市场、在线安装、自动更新、用户 Workspace 副本或按用户启停。

## 5. 存储与配置

MindAgent 使用默认 `~/.mindagent` 数据根目录。`app.db` 统一保存全局状态、运行配置、模型配置、Embedding 配置、QQ 配置、内置工具设置、MCP 配置、Skill 状态和凭据；`persona/` 保存全局人设；`skills/` 保存全局 Skills；`workspaces/<internal-user-uuid>/` 保存用户隔离数据；`knowledge/` 保存全局知识库。

首次启动通过环境变量提供数据目录、监听地址和缺失配置的引导值；已有 `app.db` 配置优先。凭据与普通配置一起保存时，API 和日志必须脱敏，数据库文件权限必须受限。

## 6. 首版范围与非目标

首版实现 QQ 私聊、每用户唯一 Session/Workspace 隔离、Scroll 召回、四文件人设与用户记忆、Sub-agent 任务、全局 RAG、全局 Skills、内置工具管理、MCP 管理和管理员 Web。

首版不实现群聊、第二渠道、插件系统、Skill 脚本执行、技能市场、MCP OAuth、MCP 详细访问规则、自动记忆整理、后台 dream、备份恢复、迁移中心或多服务拆分。
