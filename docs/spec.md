# WorkHub 强制规范

> 状态：Accepted
>
> 本文是首版实现不可违背的契约。业务范围见 [proposal.md](proposal.md)，技术方案见 [design.md](design.md)。

## 1. 基础约定

- **MUST** 表示必须，**MUST NOT** 表示禁止；
- 内部 ID MUST 使用 UUID4；持久时间 MUST 使用 UTC RFC 3339；
- API 字段 MUST 使用 `snake_case`；
- 飞书 ID、事件 ID、消息 ID 和审批业务单号 MUST 作为不透明字符串处理；
- 首版 MUST 以单企业、单飞书应用、单 WorkHub Worker 运行；
- `app.db` MUST 使用 SQLite WAL、foreign keys 和 busy timeout；
- Chat 与 Embedding MUST 只支持 OpenAI-compatible Provider。

## 2. Employee、ChannelIdentity 与 ActorContext

`Employee` 至少包含：

```json
{
  "employee_id": "uuid",
  "employee_no": "E10001",
  "display_name": "张三",
  "department": "研发部",
  "manager_employee_id": "uuid-or-null",
  "timezone": "Asia/Shanghai",
  "status": "active"
}
```

- `employee_no` MUST 在当前实例内唯一；
- `status` 只允许 `active` 或 `disabled`；
- `manager_employee_id` 必须引用存在的员工且不得引用自身；
- 员工资料只能由管理员 API 管理，Agent 和飞书用户 MUST NOT 修改；
- 被禁用员工 MUST NOT 启动新 Agent run、创建确认动作或执行待确认动作。

`ChannelIdentity` 至少保存 `identity_id`、`channel`、`app_id`、`platform_user_id`、可选显示名、绑定状态和 `employee_id`。约束：

- 首版 `channel` 固定为 `feishu`；
- 唯一键 MUST 为 `(channel, app_id, platform_user_id)`；
- `platform_user_id` 使用飞书事件提供的 `open_id`；
- 绑定状态只允许 `unbound` 或 `bound`；
- 未知 `open_id` 首次私聊 MUST 原子创建或更新一条 `unbound` identity，返回联系管理员提示，并 MUST NOT 创建 Session 或 Agent run；
- 管理员 MUST 显式把 identity 绑定到一个员工；一个飞书 identity 只能绑定一个员工；
- 解绑后该 identity MUST 立即失去运行和确认权限，但历史仍归原员工所有。

Runtime 在身份解析后构造可信 `ActorContext`：

```json
{
  "employee_id": "uuid",
  "employee_no": "E10001",
  "display_name": "张三",
  "department": "研发部",
  "manager_employee_id": "uuid-or-null",
  "timezone": "Asia/Shanghai",
  "channel_identity_id": "uuid"
}
```

- `ActorContext` MUST 来自数据库中的当前绑定和员工快照；
- 模型输入或 MCP 参数 MUST NOT 覆盖 `ActorContext`；
- Agent 工具的模型可见 Schema MUST NOT 暴露 `employee_id`、`employee_no`、`open_id` 等主体选择参数，也不得暴露由 Runtime 生成的 `idempotency_key`；
- 工具执行层 MUST 从 `ActorContext` 注入实际主体，并拒绝任意跨员工操作。

## 3. FeishuMessage 与渠道行为

飞书官方 SDK 长连接事件 MUST 先转换为与 SDK 解耦的领域消息。Agent Runtime MUST NOT 读取飞书原始事件结构。

```json
{
  "message_id": "om_xxx",
  "event_id": "evt_xxx",
  "address": {
    "channel": "feishu",
    "app_id": "cli_xxx",
    "conversation_type": "private",
    "conversation_id": "oc_xxx"
  },
  "sender": {
    "platform_user_id": "ou_xxx",
    "display_name": "张三"
  },
  "created_at": "2026-07-21T08:00:00Z",
  "text": "我还有多少天年假？"
}
```

- 首版只接受 `conversation_type=private` 的非空文本；
- 群聊和非文本消息 MUST 返回明确的不支持提示或被安全忽略，不得进入 Agent；
- 事件 MUST 按飞书 `event_id` 去重；同一事件重投不得产生第二个 run；
- 回复地址 MUST 来自当前消息或持久化待确认动作，模型不得指定；
- 每名已绑定员工 MUST 只有一个稳定 Session；同一员工 run MUST 串行，不同员工 MAY 并发；
- 飞书连接状态只读展示；管理端不得伪造消息或发起 Agent run；
- 系统 MUST NOT 查询或导入飞书聊天历史，本地 Session 历史是唯一上下文来源。

飞书卡片回调 MUST 在普通消息路由之前识别。回调只允许确认或取消指定动作，不触发新的自由形式 Agent 推理。

## 4. Agent Runtime 与 Prompt

- 系统 Prompt MUST 由代码版本维护，描述办公助手职责、安全边界、日期澄清、知识引用和工具约束；
- 首版 MUST NOT 从数据目录加载 Persona、Profile、Memory、Skill 或用户指令文件；
- 每次 run MUST 注入当前 `ActorContext`、最近完整 Scroll 回合、headline 导航、知识工具及 MCP 工具快照；
- Runtime MUST 设置模型最大迭代次数、总超时和工具超时；
- 模型不确定具体日期、假期类型或申请范围时 MUST 先追问，不得猜测后提交；
- 日期解析 MUST 使用员工时区，Mock OA MUST 再次校验日期、工作日、余额和重复申请；
- 工具、模型或 Provider 失败 MUST 返回结构化错误，不得伪造制度内容、余额、申请单号或审批结果。

## 5. Context / Scroll

`app.db` 是 Session 历史真相来源。每名员工只有一个 Session；所有历史查询 MUST 同时绑定 `employee_id` 和 `session_id`。

必须逐字持久化：

- 触发 Agent 的员工文本；
- Agent 文本回复和卡片摘要；
- 知识与 MCP 工具调用、脱敏参数摘要和结果；
- 待确认动作创建；
- 飞书确认/取消事件；
- 确认后的 MCP 执行结果和最终回复。

每个回合 MUST 使用唯一 `turn_id` 并保存递增 `seq`。普通 Agent 回合和卡片确认回合是两个独立完整回合：

- 普通回合在确认卡片成功发送后结束，headline 表示“申请已准备，等待确认”；
- 确认回合包含用户决定、动作状态变化、MCP 结果和回复，headline 表示“确认并提交申请”或“取消申请”；
- 等待确认期间 MUST NOT 持有模型调用、Session 锁或 MCP 连接。

Scroll MUST 遵循：

1. 模型上下文只加载当前 Session 最近的完整回合，不得拆开 tool call/result；
2. 达到 token 预算时从最旧已完成回合开始驱逐；
3. 当前活动回合始终完整；
4. 被驱逐区间使用 `[context compressed]` 标识，并展示最近 20 个 headline；
5. headline 单行且不超过 200 字符，无有效模型输出时使用员工首条文本的确定性截断；
6. headline 只用于导航，回答事实前必须读取逐字原文；
7. `recall_session_history(expand)` 按 seq 范围返回分组原文；
8. `recall_session_history(search)` 搜索 headline 和逐字文本；
9. recall MUST 强制使用当前 `ActorContext`，不得接受员工 ID 参数或跨员工回退。

## 6. Knowledge / RAG

知识库由管理员维护，首版所有文档对全员只读共享。

### 6.1 原文与目录

- `<WORKHUB_DATA_DIR>/knowledge/originals/` MUST 是知识正文真相来源；
- 管理 API MUST 支持目录和 UTF-8 `.md`/`.txt` 文档的创建、保存、移动、重命名和删除；
- 首版 MUST NOT 监听管理员在磁盘上的直接修改；所有受支持变更必须经过管理 API；
- 路径 MUST 使用安全相对路径，并拒绝绝对路径、`..`、符号链接逃逸和越出 originals 根目录；
- 单文档 MUST 不超过 10 MiB；目录和文档同级名称 MUST 唯一；
- 文件保存 MUST 使用同目录临时文件、flush、fsync 和原子替换；失败时保留旧原文；
- `app.db` 保存稳定节点 ID、父目录、名称、版本、内容哈希、状态和 active generation；移动或重命名 MUST 保留节点 ID。

### 6.2 自动索引

- 新建或保存文档 MUST 计算 SHA-256；哈希未变化时 MUST NOT 创建新的 Embedding 任务；
- 内容变化 MUST 增加文档版本并创建内部索引任务；该队列不是 Agent Tasks 能力；
- `.md` MUST 先按标题层级切分，再对超长内容递归切分；`.txt` 使用递归字符切分；
- 每个 chunk MUST 保存文档 ID、版本、generation、目录路径、标题路径和原文起止行；
- 新 generation 只有在文档全部切片和 Embedding 成功后才能原子切为 active；
- 构建失败 MUST 保留旧 active generation，并保存可重试的结构化错误；
- 纯移动或重命名只更新路径元数据，内容哈希不变时 MUST NOT 重新 Embedding；
- 删除文档后 MUST 从可见目录、chunk 元数据和所有 generation 中移除，完成后不得再被检索；
- 应用启动 MUST 对账原文、元数据和索引；不一致项 MUST 标记并排队修复，不得静默标记 ready；
- 索引队列 MUST 单并发；遗留 `indexing` 项在启动时重新排队。

### 6.3 Agent 工具

Agent 只拥有：

- `search_knowledge(query, top_k, score_threshold?)`；
- `list_knowledge_directory(path?)`；
- `read_knowledge_document(document_id, line_start, line_end)`。

检索 MUST 返回文档 ID、版本、相对路径、标题路径、行号、片段和相关度。原文读取单次不得超过 500 行。未配置 Embedding、索引不可用或 Provider 失败时必须返回结构化错误。

## 7. MCP 客户端与策略

- 首版 MUST 只支持 Streamable HTTP，不支持 stdio、SSE、OAuth、Resources、Prompts 或市场安装；
- MCP 客户端配置 MUST 包含稳定唯一 `client_key` 和 HTTPS/HTTP URL；
- env、headers、令牌和模型密钥在 API 中只返回 `configured`，日志与错误 MUST 脱敏；
- 工具白名单与执行策略 MUST 分开求值；未进入白名单的工具不得注册给模型；
- 策略只允许 `allow`、`confirm`、`deny`；新客户端和新发现工具默认 `deny`；
- `deny` MUST 在连接执行端之前失败；
- `allow` MAY 在当前 run 直接执行；
- `confirm` MUST 创建待确认动作，当前 run MUST NOT 调用 MCP 执行端；
- 配置修改只影响后续 run 和后续动作执行校验，不得改写已经执行的结果；
- 单个 MCP 客户端连接、发现或调用失败 MUST 不影响知识、Scroll 和其他客户端。

模型可见工具 Schema MUST 移除主体选择字段和 `idempotency_key`。执行包装器 MUST 使用当前 `ActorContext` 注入可信主体，并只在实际 MCP 调用时注入动作的幂等键；Mock OA 必须把注入值作为唯一操作主体。

## 8. PendingAction 与员工确认

`PendingAction` 至少保存：

- `action_id`、不可预测 `action_token_hash`；
- `employee_id`、`session_id`、`channel_identity_id` 和原飞书回复地址；
- MCP client/tool、规范化参数、参数 SHA-256；
- 面向员工的不可变确认摘要；
- 唯一幂等键；
- `status`、创建/过期/执行时间及脱敏结果摘要。

状态只允许：

```text
pending -> executing -> succeeded
                   -> failed
pending -> cancelled
pending -> expired
```

约束：

- 默认有效期 MUST 为 10 分钟；
- action token MUST 使用密码学安全随机数，数据库只保存其哈希；
- 飞书卡片 MUST 只携带 token 和决定，不携带工具参数、员工 ID 或凭据；
- 卡片 MUST 展示工具参数生成的确定性摘要，包括日期、假期类型、天数和余额；
- 确认卡片投递失败时，动作 MUST 原子转为 `cancelled` 并记录结构化错误，之后不得执行；
- 回调 MUST 重新校验飞书身份仍绑定同一启用员工、动作仍为 `pending`、地址匹配且参数哈希有效；
- 确认 MUST 以数据库条件更新原子抢占 `pending -> executing`；只有抢占成功者可以调用 MCP；
- 取消 MUST 原子执行 `pending -> cancelled`，且 MCP 零调用；
- 重复回调 MUST 返回已有终态，不得再次执行；
- 过期动作 MUST NOT 执行；
- MCP 调用 MUST 携带动作创建时生成的幂等键；
- 执行前 MUST 再次确认工具仍存在且策略不是 `deny`；若策略变为 `deny`，动作转为 `failed`；
- 执行前 MUST 使用当前实际工具 Schema 重新验证保存的规范化参数；不再兼容时动作转为 `failed`；
- 成功或失败结果 MUST 写入确认回合并投递至原飞书私聊地址；
- 服务重启后未过期 `pending` 动作继续有效，遗留 `executing` 动作必须使用幂等键向执行端恢复或查询结果，绝不能盲目重复提交。

## 9. Mock OA 服务

Mock OA MUST 独立进程部署并使用独立 `mock_oa.db`。它拥有余额、请假申请、审批状态和幂等记录，WorkHub 不得直接读写该数据库。

MCP 工具固定为：

```text
query_leave_balance() -> {remaining_days, as_of}
submit_leave_request(start_date, end_date, leave_type, reason?, idempotency_key)
  -> {request_id, status: "pending_approval", submitted_at}
query_leave_request_status(request_id) -> {request_id, status, updated_at}
```

- 工具的员工主体必须来自 WorkHub 注入的可信调用上下文，不得接受模型自由选择；
- `submit_leave_request` MUST 校验日期顺序、工作日、余额和重复申请；
- 同一员工与同一幂等键 MUST 永远返回同一申请，不得重复扣减余额；
- 业务状态只允许 `pending_approval`、`approved`、`rejected`；
- WorkHub 只查询状态，不提供改变状态的 MCP 工具；
- Mock OA MAY 提供独立管理 REST 接口修改状态，但 MUST 使用管理令牌认证并仅用于本地演示与测试；
- Mock OA 故障必须表现为真实结构化失败，WorkHub 不得伪造成功。

## 10. 管理 Web 与 REST

Web 只允许管理员登录。首版菜单固定为：

```text
概览
员工
知识库
MCP
审计
设置
├── 模型
└── 飞书状态
```

REST API 至少按领域分组：

- `/api/v1/auth/*`；
- `/api/v1/employees/*` 与 `/api/v1/channel-identities/*`；
- `/api/v1/knowledge/*`；
- `/api/v1/mcp/*`；
- `/api/v1/audit-events/*`；
- `/api/v1/settings/models/*`；
- `/api/v1/feishu/status`。

- 管理 API MUST 使用认证、CSRF/同源防护和适当限速；
- 列表 MUST 分页，更新 MUST 使用 revision 或等价的乐观并发控制；
- 密钥、headers、action token、完整敏感工具参数和飞书凭据 MUST NOT 出现在响应、日志或审计正文；
- Web MUST NOT 提供 Agent 测试聊天、业务审批或任意 MCP 调用入口。

## 11. 存储、审计与部署

数据布局固定为：

```text
<WORKHUB_DATA_DIR>/
├── app.db
├── knowledge/
│   ├── originals/
│   ├── staging/
│   └── index/
└── logs/

<MOCK_OA_DATA_DIR>/
└── mock_oa.db
```

`app.db` 至少保存管理员会话、员工、渠道身份、Agent Session、Scroll、待确认动作、模型/飞书/MCP 配置、知识节点/版本/索引任务和审计事件。原始知识文件是正文真相来源，向量索引是可重建派生数据。

审计 MUST 记录请求主体、飞书事件、知识版本、工具名、策略决定、动作状态、幂等键摘要、业务单号、耗时和结构化错误；MUST NOT 记录密钥、卡片 token、未脱敏完整参数或不必要的员工隐私。

部署 MUST 至少包含 WorkHub 和 Mock OA 两个独立服务及独立持久卷。WorkHub 首版只运行一个 Worker；未来迁移 PostgreSQL 或多副本不进入首版。

## 12. 首版验收红线

1. 未绑定 identity 不创建 Agent run，绑定后稳定复用唯一 Session；
2. 两名员工的历史、余额、待确认动作和 recall 不可串用；
3. Scroll 驱逐后可按 headline 展开和搜索逐字原文；
4. 文档内容保存自动索引，失败保留旧 generation，纯重命名不重复 Embedding；
5. `allow` 余额查询直接执行，`confirm` 提交在员工确认前 MCP 零调用；
6. 跨用户、过期、取消和重复回调不能执行或重复提交；
7. 超时重试依靠幂等键只创建一张请假申请；
8. Agent 可查询三种业务审批状态，但不能改变状态；
9. 服务重启不丢失未过期待确认动作，也不盲目重复执行遗留动作；
10. 响应、日志和审计不泄露凭据、action token 或完整敏感参数。
