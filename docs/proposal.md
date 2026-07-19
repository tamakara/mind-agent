# MindAgent 业务提案

> 状态：Accepted
>
> 本文只定义首版业务目标与范围。强制行为见 [spec.md](spec.md)，实现方案见 [design.md](design.md)。

## 1. 产品定位

MindAgent 是一个多用户智能体工作台。一个实例服务一个团队，多名用户共用 Agent 服务，每名用户拥有一个独立 Session 和 Workspace，避免上下文、文件和任务相互串扰。首版聚焦 QQ 私聊一对一交互；本地历史缺失时，Agent 可按需查询当前私聊的 QQ 原始记录作为补偿。私聊只是触发渠道，不对应独立于用户之外的 Session。

首版以 QQ 为唯一入口，重点完成多用户隔离与并发、Session 历史与 Scroll 召回、长任务异步执行和公共知识检索，不追求完整平台能力。

## 2. 首版范围

### QQ 交互

- 使用 NapCat / OneBot v11 接入 QQ 私聊；
- 每条私聊消息直接触发 Agent；不同用户可以并发调用；
- Agent 优先使用本地 Session 历史；本地缺失时才按需查询当前私聊的 QQ 原始记录，不全量注入渠道消息；
- 常用消息支持文本、提及、引用、图片、语音、视频和文件。

### 用户、Session 与 Workspace

- 用户首次发送私聊时自动创建；
- 每名用户拥有一个独立 Session 和一个独立 Workspace；
- Session 承载该用户连续的 Agent 上下文；
- Workspace 保存 Session 的逐字历史、用户文件、Agent 产物和异步任务数据；
- 管理员可以查看 Workspace 文件并启用或禁用用户。

### Agent 与任务

- 主 Agent 负责实时会话和短任务；
- Sub-agent 在后台执行耗时任务，不阻塞主 Agent；
- Sub-agent 只提交候选结果，不直接回复用户；
- 主 Agent 根据原始要求验收候选结果，通过后生成最终回复；
- 首次验收不通过时自动返工一次，再次不通过则结束任务并说明原因。

### 简化 Scroll

- 触发对话、Agent 回复和工具结果逐字保存；
- 一个完整用户回合拥有一个短 headline，用于标识被驱逐历史；
- 模型上下文只保留当前用户 Session 最近的完整轮次，旧轮次按 headline 导航；
- 旧轮次被驱逐后仍可按 seq 展开或搜索，正文是事实来源；
- `PROFILE.md` 和 `MEMORY.md` 保存当前用户明确确认的资料、偏好、决策和工作约定；不保存原始聊天日志，不跨用户 Session 召回。

### 人设与用户记忆

全局 Agent 人设由管理员维护 `AGENTS.md` 和 `SOUL.md`；每名用户 Workspace 维护自己的 `PROFILE.md` 和 `MEMORY.md`。四个文件按固定顺序全量注入当前 Agent run，用户资料和记忆不会进入其他用户的上下文。

- `AGENTS.md`：全局工作规则、安全边界和工具约束；
- `SOUL.md`：全局身份、性格、语气和行为原则；
- `PROFILE.md`：当前用户的称呼、背景和稳定偏好；
- `MEMORY.md`：当前用户已确认的长期事实、决策、工作约定和工具设置。

Agent 只能通过专用工具更新当前用户的 `PROFILE.md` 或 `MEMORY.md`；全局文件由管理员编辑。

### 知识库

知识库是管理员维护、所有 Agent 只读的全局 RAG 知识库：

- 管理员通过 Web 上传 UTF-8 文本或 Markdown 文件，设置切块参数并管理索引；
- Web 展示文档索引状态、失败原因、切块预览和全量重建进度；
- 同名文件只有在管理员明确选择替换时才更新，旧索引在新版本就绪前继续服务；
- Agent 按需搜索知识片段、列举文档和读取带行号的原文，不在每轮对话中自动检索；
- 普通用户通过 Agent 使用知识库，不直接登录 Web。

### Web 工作台

首版只提供管理员 Web 工作台，包含：

- 登录与概览；
- 用户列表与启用/禁用；
- Workspace 文件管理；
- 全局 `AGENTS.md`/`SOUL.md` 与用户 `PROFILE.md`/`MEMORY.md` 管理；
- RAG 知识库与索引管理；
- 异步任务查看与取消；
- 对话模型与 Embedding 模型配置；
- QQ 连接状态；
- 管理员测试聊天。
