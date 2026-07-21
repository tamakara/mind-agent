# WorkHub

WorkHub 是面向单个企业部署的对话式企业服务入口。员工通过飞书机器人私聊，用自然语言查询公司制度、查询年假余额、提交年假申请并跟踪业务审批进度。系统通过目录化 RAG 提供可信制度依据，通过 MCP 连接企业内部系统，并在产生真实业务写入前要求员工使用飞书交互卡片确认。

首版只覆盖飞书文本私聊和年假申请闭环。每个已绑定员工拥有一个稳定 Session；对话、工具调用、确认动作和结果逐字写入 Scroll 历史，并以 headline 导航被压缩的旧回合。员工身份来自管理员维护的结构化员工目录，不由模型或自由文本记忆决定。

## 最小链路

1. 员工在飞书私聊：“我想休下周一和周二的年假，帮我走一下流程。”
2. Agent 检索《员工请假制度.md》，并调用 `query_leave_balance`。
3. Agent 准备调用 `submit_leave_request` 时创建持久化待确认动作，结束当前 run，并发送确认卡片。
4. 员工点击“确认提交”；系统在新的确认回合中校验身份、原子抢占动作并使用幂等键调用 MCP。
5. 模拟 OA 返回申请单号和 `pending_approval`；员工之后可调用 `query_leave_request_status` 查询 `approved` 或 `rejected`。

飞书卡片只代表员工确认 Agent 代为提交，不代表主管或财务审批。业务审批由 OA 系统负责，WorkHub 只查询其状态。

## 首版组成

- WorkHub：飞书适配、Agent Runtime、Scroll、目录化知识库、MCP 客户端、确认服务、审计和管理员 Web；
- Mock OA：独立部署的模拟企业内部系统，通过 Streamable HTTP MCP 提供年假余额、申请提交和进度查询；
- `app.db`：WorkHub 的配置和业务编排状态；
- `mock_oa.db`：模拟 OA 的余额、申请、审批状态和幂等记录；
- `knowledge/originals/`：知识正文真相来源；
- `knowledge/index/`：可重建的向量索引。

首版采用单企业、单飞书自建应用、单 WorkHub Worker 和 SQLite WAL。Chat 与 Embedding 均使用 OpenAI-compatible Provider。

## 本地一体化部署

WorkHub 镜像使用多阶段构建生成 React 管理台，并由 FastAPI 在同一域名下提供静态页面和 API。Mock OA 仍作为独立服务运行：

```powershell
Copy-Item .env.example .env
# 启动前修改 .env 中的管理员密码和其他 change-me 值
docker compose build
docker compose up -d
docker compose ps
```

启动后可访问：

- WorkHub 管理台：<http://127.0.0.1:8000/>
- WorkHub 就绪检查：<http://127.0.0.1:8000/readyz>
- Mock OA 就绪检查：<http://127.0.0.1:8001/readyz>

前端本地开发使用 `npm run dev`，Vite 会将 `/api`、`/healthz` 和 `/readyz` 代理到本机 `8000` 端口。生产环境不需要单独部署前端容器。

首次启动时，WorkHub 使用 `WORKHUB_BOOTSTRAP_ADMIN_USERNAME` 和 `WORKHUB_BOOTSTRAP_ADMIN_PASSWORD` 原子创建唯一初始管理员；数据库已有管理员后不会再次引导。管理 API 默认要求持久 Session、同源 Origin 和 CSRF 请求头。通过 HTTPS 部署时必须设置 `WORKHUB_ADMIN_COOKIE_SECURE=true`。

## Mock OA 演示

Mock OA 首次启动会创建 `E10001` 演示员工和当年 10 天年假余额。WorkHub 在 MCP URL 与两端共享密钥均已配置后，引导 `mock_oa` 客户端并发现以下工具：

- `query_leave_balance`：查询当前员工余额；
- `submit_leave_request`：使用 WorkHub 生成的幂等键提交申请；
- `query_leave_request_status`：只查询当前员工自己的申请。

新发现工具遵守默认 deny。管理员应在 MCP 页面将余额和状态查询设为 `allow`，将申请提交设为 `confirm`，并加入模型白名单。

申请提交后固定进入 `pending_approval`。本地演示人员可使用独立管理令牌更新状态：

```powershell
$headers = @{ Authorization = "Bearer $env:MOCK_OA_ADMIN_TOKEN" }
$body = @{ status = "approved" } | ConvertTo-Json
Invoke-RestMethod -Method Patch -Headers $headers -ContentType "application/json" `
  -Body $body "http://127.0.0.1:8001/api/v1/leave-requests/<request_id>/status"
```

该管理接口不是 MCP 工具，WorkHub 和 Agent 均不能通过工具改变业务审批状态。示例制度文档位于 `context/demo/员工请假制度.md`，可在管理台知识库中导入。

## 项目文档

- [业务提案](docs/proposal.md)：产品定位、用户链路和首版范围；
- [强制规范](docs/spec.md)：身份、消息、Scroll、知识、MCP、确认和安全契约；
- [技术设计](docs/design.md)：模块、存储、数据流和部署方案；
- [实现任务清单](docs/task.md)：按依赖顺序拆分的交付阶段。

实现不得扩大业务提案的首版范围，也不得违反强制规范；技术设计用于指导当前首版实现。
