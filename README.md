# MindAgent

MindAgent 是面向单个企业部署的统一办公助手。员工通过飞书机器人私聊，用自然语言查询公司制度、查询年假余额、提交年假申请并跟踪业务审批进度。系统通过目录化 RAG 提供可信制度依据，通过 MCP 连接企业内部系统，并在产生真实业务写入前要求员工使用飞书交互卡片确认。

首版只覆盖飞书文本私聊和年假申请闭环。每个已绑定员工拥有一个稳定 Session；对话、工具调用、确认动作和结果逐字写入 Scroll 历史，并以 headline 导航被压缩的旧回合。员工身份来自管理员维护的结构化员工目录，不由模型或自由文本记忆决定。

## 最小链路

1. 员工在飞书私聊：“我想休下周一和周二的年假，帮我走一下流程。”
2. Agent 检索《员工请假制度.md》，并调用 `query_leave_balance`。
3. Agent 准备调用 `submit_leave_request` 时创建持久化待确认动作，结束当前 run，并发送确认卡片。
4. 员工点击“确认提交”；系统在新的确认回合中校验身份、原子抢占动作并使用幂等键调用 MCP。
5. 模拟 OA 返回申请单号和 `pending_approval`；员工之后可调用 `query_leave_request_status` 查询 `approved` 或 `rejected`。

飞书卡片只代表员工确认 Agent 代为提交，不代表主管或财务审批。业务审批由 OA 系统负责，MindAgent 只查询其状态。

## 首版组成

- MindAgent：飞书适配、Agent Runtime、Scroll、目录化知识库、MCP 客户端、确认服务、审计和管理员 Web；
- Mock OA：独立部署的模拟企业内部系统，通过 Streamable HTTP MCP 提供年假余额、申请提交和进度查询；
- `app.db`：MindAgent 的配置和业务编排状态；
- `mock_oa.db`：模拟 OA 的余额、申请、审批状态和幂等记录；
- `knowledge/originals/`：知识正文真相来源；
- `knowledge/index/`：可重建的向量索引。

首版采用单企业、单飞书自建应用、单 MindAgent Worker 和 SQLite WAL。Chat 与 Embedding 均使用 OpenAI-compatible Provider。

## 项目文档

- [业务提案](docs/proposal.md)：产品定位、用户链路和首版范围；
- [强制规范](docs/spec.md)：身份、消息、Scroll、知识、MCP、确认和安全契约；
- [技术设计](docs/design.md)：模块、存储、数据流和部署方案；
- [实现任务清单](docs/task.md)：按依赖顺序拆分的交付阶段。

实现不得扩大业务提案的首版范围，也不得违反强制规范；技术设计用于指导当前首版实现。
