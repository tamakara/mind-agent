# MindAgent 强制规范

> 状态：Accepted
>
> 本文是 MindAgent 实现与验收的强制契约。业务范围见 [proposal.md](proposal.md)，技术方案见 [design.md](design.md)。`design.md` 不得覆盖本文约束；任何偏离都必须先修改并评审本文。

## 1. 规范用语

本文中的关键词含义如下：

- **MUST / 必须**：实现不可违反；
- **MUST NOT / 禁止**：实现不可出现；
- **SHOULD / 应当**：除非有书面记录的充分理由，否则必须遵循；
- **MAY / 可以**：允许但不强制。

所有由 MindAgent 生成的内部实体 ID MUST 使用小写标准格式的 UUID4 字符串；渠道提供的 `event_id`、`account_id`、`platform_user_id`、群号和消息号 MUST 视为不透明字符串，不得强制转换为 UUID。所有持久时间 MUST 使用 UTC，并以 RFC 3339 格式输出。API 字段 MUST 使用 `snake_case`。

## 2. 身份与访问控制

### 2.1 数据结构

#### User

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | UUID4 | 主键 |
| `display_name` | string | 非空，来自首次渠道身份，可由管理员修改 |
| `status` | enum | `active`、`disabled` |
| `created_at` | datetime | UTC RFC 3339 |
| `updated_at` | datetime | UTC RFC 3339 |

#### ChannelIdentity

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | UUID4 | 主键 |
| `user_id` | UUID4 | 指向 User |
| `channel` | string | 首版固定为 `qq` |
| `account_id` | string | MindAgent 接入的渠道账号标识 |
| `platform_user_id` | string | 渠道侧用户标识 |
| `display_name` | string | 渠道侧显示名 |
| `created_at` | datetime | UTC RFC 3339 |

`(channel, account_id, platform_user_id)` MUST 全局唯一。

#### Workspace

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | UUID4 | 主键 |
| `user_id` | UUID4 | 唯一；一名用户只能拥有一个 Workspace |
| `relative_path` | string | 相对 Workspace 根目录的安全路径 |
| `state` | enum | `ready`、`disabled`、`deleting` |
| `created_at` | datetime | UTC RFC 3339 |
| `updated_at` | datetime | UTC RFC 3339 |

#### AdminAccount

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | UUID4 | 主键 |
| `username` | string | 唯一、大小写不敏感 |
| `password_hash` | string | Argon2id 哈希，禁止保存明文 |
| `status` | enum | `active`、`disabled` |
| `created_at` | datetime | UTC RFC 3339 |
| `last_login_at` | datetime/null | UTC RFC 3339 |

### 2.2 自动创建

- 首次私聊消息 MUST 原子创建 User、ChannelIdentity、Workspace 及初始 Workspace 目录；
- 首次群聊 `@Agent` MUST 执行相同创建流程；
- 普通未触发群消息 MUST NOT 创建 User 或 Workspace；
- 对同一渠道身份并发到达的首次消息 MUST 只产生一个 User 和 Workspace；
- 初始化任一步骤失败时 MUST 回滚数据库记录，并清理未完成目录；
- 已存在身份 MUST 路由到原 User 和 Workspace，不得隐式新建第二个 Workspace。

### 2.3 权限

- 普通用户 MUST NOT 登录 Web 工作台；
- 管理员 MUST 能通过 Web 查看、修改和删除全部 Workspace 私有内容；
- 管理员测试聊天 MUST 显式选择目标 Workspace，并在该 Workspace 中以 `web_admin` 会话记录，不得使用隐式全局上下文；
- 管理员会话 MUST 使用服务端会话和 `HttpOnly` Cookie；
- 被禁用用户 MUST NOT 启动新 Agent run、工具调用或任务；
- 禁用操作 MUST 保留既有数据；只有管理员显式删除才可清除 Workspace；
- 所有文件访问 MUST 在解析符号链接后的 Workspace 或公告目录边界内；路径穿越 MUST 返回 `FORBIDDEN`。

## 3. 会话、消息与群历史

### 3.1 会话键

#### Conversation

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | UUID4 | 主键 |
| `workspace_id` | UUID4 | 所属 Workspace |
| `channel` | string | 渠道名 |
| `account_id` | string | 渠道账号 |
| `kind` | enum | `private`、`group`、`web_admin` |
| `platform_conversation_id` | string | 私聊对象或群标识 |
| `user_id` | UUID4 | 当前上下文所属用户 |
| `created_at` | datetime | UTC RFC 3339 |
| `updated_at` | datetime | UTC RFC 3339 |

- 私聊上下文 MUST 按 `(channel, account_id, platform_user_id)` 隔离；
- 群聊上下文 MUST 按 `(channel, account_id, group_id, user_id)` 隔离；
- 同一上下文 MUST 串行执行；不同上下文 MAY 并发执行；
- 群中不同用户 MUST NOT 共享近期上下文、checkpoint、工具结果或用户记忆。

### 3.2 统一消息模型

渠道输入 MUST 转换为以下 `NormalizedMessage` 后才能进入应用层：

```json
{
  "version": "1.0",
  "event_id": "evt-123",
  "channel": "qq",
  "account_id": "bot-main",
  "conversation": {
    "kind": "group",
    "id": "123456"
  },
  "sender": {
    "platform_user_id": "10001",
    "display_name": "Alice"
  },
  "occurred_at": "2026-07-18T12:00:00Z",
  "reply_to": null,
  "mentions": [
    {
      "platform_user_id": "bot-main",
      "display_name": "MindAgent"
    }
  ],
  "parts": [
    {
      "type": "text",
      "text": "请整理最近的讨论"
    }
  ]
}
```

`NormalizedMessage` MUST 包含：

- `version`：首版固定为 `1.0`；
- `event_id`：渠道事件唯一标识；
- `channel`、`account_id`；
- `conversation.kind` 与 `conversation.id`；
- `sender.platform_user_id` 与 `sender.display_name`；
- `occurred_at`；
- 可空的 `reply_to`；
- `mentions` 数组；
- 非空 `parts` 数组。

`ContentPart` MUST 使用 `type` 作为判别字段，并只允许：

| `type` | 必需字段 | 说明 |
| --- | --- | --- |
| `text` | `text` | 文本 |
| `image` | `file_ref`、`name` | 图片引用 |
| `audio` | `file_ref`、`name` | 音频引用 |
| `video` | `file_ref`、`name` | 视频引用 |
| `file` | `file_ref`、`name` | 普通附件 |
| `mention` | `platform_user_id` | 提及用户 |
| `quote` | `message_id` | 引用消息 |
| `extension` | `name`、`data` | 渠道专属扩展；`data` 必须为 JSON object |

未知消息段 MUST 转换为带原始类型说明的 `extension` 或显式降级结果，MUST NOT 静默丢弃。

### 3.3 触发规则

| 事件 | 行为 |
| --- | --- |
| 私聊消息 | 触发当前用户私聊上下文 |
| 群聊且明确 `@Agent` | 触发当前群中当前用户上下文 |
| 群聊普通消息 | 不触发、不创建用户、不长期保存 |
| 用户查询或取消任务 | 由主 Agent 或固定命令处理 |
| Sub-agent 产生候选结果 | 不投递渠道；触发主 Agent review run |
| 主 Agent 验收通过 | 允许向原始会话投递最终回复 |
| 任务失败、取消或中断 | 允许向原始会话投递状态说明 |
| 定时器、空闲状态、巡检 | 禁止触发 Agent 回复 |

### 3.4 群历史

- 群历史的权威来源 MUST 是当前渠道；
- MindAgent MUST NOT 将未触发群消息长期持久化；
- 渠道查询结果 MAY 进入最多五分钟的有界内存缓存，服务重启后 MUST 丢弃；
- 查询 MUST 绑定当前 `channel`、`account_id` 和 `group_id`；
- 查询结果 MUST 只用于当前 run，不得写入其他用户 Workspace、记忆或团队公告；
- 渠道不支持历史查询时 MUST 返回结构化 `CHANNEL_CAPABILITY_UNAVAILABLE`，不得伪造历史。

## 4. Workspace、记忆与文件

### 4.1 Workspace 内容

每个 Workspace MUST 包含或允许创建：

- `workspace.json`：Workspace 可读配置；
- `MEMORY.md` 与 `memory/`：长期记忆；
- `history.db`：会话索引与 LangGraph checkpoint；
- `files/`：用户文件；
- `artifacts/`：Agent 产物；
- `tasks/`：任务日志和任务产物；
- `skills/`：用户 Workspace Skills。

- 用户正文文件、记忆、Skills 与任务产物 MUST 保存在 Workspace 目录，不得把正文写入 `control.db`；
- 记忆 MUST 以用户为边界，MUST NOT 跨用户合并；
- 群查询结果 MUST NOT 自动写入长期记忆；
- 文件引用 MUST 使用不暴露绝对路径的稳定 ID；
- 文件读取失败 MUST 返回明确错误，Agent MUST NOT 猜测内容。

### 4.2 团队公告资料

#### PublicMaterial

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | UUID4 | 主键 |
| `relative_path` | string | 公告根目录内唯一安全路径 |
| `kind` | enum | `directory`、`file` |
| `size` | integer/null | 文件字节数；目录为 null |
| `media_type` | string/null | 文件 MIME；目录为 null |
| `sha256` | string/null | 文件摘要；目录为 null |
| `created_at` | datetime | UTC RFC 3339 |
| `updated_at` | datetime | UTC RFC 3339 |

- 公告目录 MUST 是文件正文的真相来源；索引 MUST 可以从目录重建；
- 只有管理员 Web API 可以创建、覆盖、移动或删除公告资料；
- Agent MUST NOT 注册任何公告写入、移动或删除工具；
- 普通用户可用工具只允许 `list_public_materials` 和 `read_public_material`；
- 列举 MAY 按文件名过滤；MUST NOT 使用 embedding、向量检索或自动主题聚类；
- 符号链接 MUST NOT 被公告资料 API 接受或跟随。

工具返回 MUST 为结构化 JSON。例如：

```json
{
  "path": "policies",
  "entries": [
    {
      "name": "remote-work.md",
      "path": "policies/remote-work.md",
      "kind": "file",
      "size": 2048,
      "media_type": "text/markdown",
      "updated_at": "2026-07-18T12:00:00Z"
    }
  ]
}
```

## 5. Agent 与异步任务

### 5.1 运行隔离

- 主 Agent 和 Sub-agent MUST 使用 LangGraph；
- 每次运行 MUST 拥有唯一 `run_id`、独立 checkpoint namespace 和明确的 `workspace_id`；
- Sub-agent MUST 使用独立上下文，不得直接继承主 Agent 全量历史；
- 主 Agent 只能向 Sub-agent 提供任务描述、必要文件引用、选定公共资料和最小工具权限；
- Sub-agent MUST NOT 获得其他 Workspace 或其他群的上下文；
- Sub-agent 的候选结果 MUST 交回主 Agent 验收，MUST NOT 由 TaskManager 或 Channel 直接发送给用户；
- 验收 MUST 使用独立 review run，不得污染或占用普通会话的长期执行上下文；
- 阻塞或 CPU 密集工具 MUST 在受限执行池运行，不得阻塞 Web、QQ 或主 Agent 事件循环。

### 5.2 AgentTask

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | UUID4 | 主键与外部任务 ID |
| `workspace_id` | UUID4 | 任务所属 Workspace |
| `conversation_id` | UUID4 | 原始会话 |
| `run_id` | UUID4/null | 当前 Sub-agent 执行 run |
| `review_run_id` | UUID4/null | 当前主 Agent 验收 run |
| `status` | enum | 见下文 |
| `prompt` | string | 明确任务描述 |
| `acceptance_criteria` | array[string] | 创建任务时固定的验收条件 |
| `attempt` | integer | 当前执行次数，从 1 开始 |
| `max_attempts` | integer | 首版固定为 2，即最多一次返工 |
| `candidate_result_ref` | string/null | Sub-agent 候选结果引用 |
| `review_result_ref` | string/null | 主 Agent 结构化验收结论引用 |
| `rework_instructions_ref` | string/null | 验收不通过时的返工意见引用 |
| `result_ref` | string/null | 验收通过后的最终结果引用 |
| `error_code` | string/null | 失败错误码 |
| `created_at` | datetime | UTC RFC 3339 |
| `started_at` | datetime/null | UTC RFC 3339 |
| `finished_at` | datetime/null | UTC RFC 3339 |

任务状态只允许：

- `queued`；
- `running`；
- `reviewing`；
- `succeeded`；
- `failed`；
- `cancelled`；
- `interrupted`。

约束：

- Sub-agent 只能由主 Agent 针对明确用户任务创建；
- 主 Agent 创建任务时 MUST 同时保存不可变的原始 `prompt` 与 `acceptance_criteria`，后续普通对话不得改变本次任务验收标准；
- 默认并发 MUST 为每用户 1 个、全局 4 个，管理员 MAY 调整；
- 超出并发限制的任务 MUST 保持 `queued`，不得挤占主 Agent；
- Sub-agent 完成执行后 MUST 保存候选结果与产物，将任务从 `running` 转为 `reviewing`，不得直接转为 `succeeded`；
- `reviewing` MUST 触发一次主 Agent review run；review run MUST 使用原始需求、固定验收条件、候选结果、产物引用和执行摘要作为输入；
- 主 Agent 验收通过后，MUST 生成结构化最终回复；只有此时任务才能转为 `succeeded` 并允许投递；
- 首次验收不通过时，MUST 保存明确问题与返工意见，将 `attempt` 增加到 2，并重新进入 `queued`；
- 第二次验收仍不通过时，任务 MUST 转为 `failed`，错误码为 `TASK_REVIEW_REJECTED`，不得继续自动返工；
- 取消 `queued` 任务 MUST 直接转为 `cancelled`；取消 `running` 或 `reviewing` 任务 MUST 请求对应 LangGraph run 停止并最终落为 `cancelled` 或 `failed`；
- 服务启动时，持久化的 `queued` 任务 MUST 重新进入调度；原 `running` 或 `reviewing` 任务 MUST 转为 `interrupted`；
- `interrupted` 任务 MUST NOT 自动重放，避免重复执行有副作用的工具；
- Sub-agent 执行失败、取消或中断时不进行内容验收；系统 MUST 生成明确的用户可读状态说明；
- 候选结果、验收意见和返工内容 MUST 只保存在任务所属 Workspace，不得发送到渠道；
- 最终成功回复以及失败、取消和中断状态 MUST 记录，并只投递给原始会话；
- 投递失败 MUST 保留可重试状态，不得丢失任务结果；
- 除任务结果投递外，Agent MUST NOT 主动发送消息。

### 5.3 TaskReviewDecision

主 Agent review run MUST 返回以下结构，且必须通过 schema 校验：

```json
{
  "accepted": true,
  "issues": [],
  "rework_instructions": null,
  "final_response": {
    "parts": [
      {
        "type": "text",
        "text": "任务已完成，以下是整理后的结果。"
      }
    ],
    "artifact_refs": []
  }
}
```

约束：

- `accepted` MUST 为 boolean；
- `issues` MUST 为数组；每项包含稳定 `code`、用户可理解的 `message` 和可选 `evidence_refs`；
- `accepted=true` 时，`issues` MUST 为空，`rework_instructions` MUST 为 null，`final_response` MUST 存在；
- `accepted=false` 时，`issues` MUST 非空，`rework_instructions` MUST 提供可执行返工要求，`final_response` MUST 为 null；
- review run 无法验证关键要求时 MUST 拒绝，不得假定候选结果合格；
- `final_response.parts` MUST 使用统一 `ContentPart`，不得直接转发 Sub-agent 原始自然语言输出；
- review run 只能使用验收所需的只读文件、资料和查询工具，不得执行具有外部副作用的工具；
- review 过程不得写入普通会话历史；验收通过后的 `final_response` 才可在取得原会话锁后追加并投递。

## 6. API 与流式事件

### 6.1 REST 响应

成功响应 MUST 使用：

```json
{
  "data": {
    "id": "6f34a49a-8855-4c4a-9f03-3f50d60cc889"
  },
  "meta": {
    "next_cursor": null
  },
  "request_id": "05f7c3bb-d06c-4406-b846-e60c424ca6c3"
}
```

`meta` MAY 省略。错误响应 MUST 使用：

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "The requested task was not found.",
    "details": {
      "resource": "task"
    }
  },
  "request_id": "05f7c3bb-d06c-4406-b846-e60c424ca6c3"
}
```

`details` MAY 省略。实现至少 MUST 支持：

| 错误码 | HTTP 状态 |
| --- | ---: |
| `VALIDATION_ERROR` | 422 |
| `UNAUTHENTICATED` | 401 |
| `FORBIDDEN` | 403 |
| `NOT_FOUND` | 404 |
| `CONFLICT` | 409 |
| `RATE_LIMITED` | 429 |
| `CHANNEL_UNAVAILABLE` | 503 |
| `CHANNEL_CAPABILITY_UNAVAILABLE` | 422 |
| `MODEL_ERROR` | 502 |
| `TOOL_ERROR` | 502 |
| `TASK_INTERRUPTED` | 409 |
| `TASK_REVIEW_REJECTED` | 409 |
| `INTERNAL_ERROR` | 500 |

内部异常、绝对路径、密钥和模型原始敏感响应 MUST NOT 出现在外部 `message` 或 `details` 中。

### 6.2 SSE

Web 流式输出 MUST 使用 SSE。每个事件的 `data` MUST 是：

```json
{
  "run_id": "793e4340-7322-4b38-a126-9a067b1e42bb",
  "sequence": 3,
  "timestamp": "2026-07-18T12:00:00Z",
  "payload": {
    "text": "正在处理"
  }
}
```

- `sequence` MUST 在同一 run 内从 1 严格递增；
- `timestamp` MUST 是 UTC RFC 3339；
- SSE `event` 只允许：`run.started`、`message.delta`、`tool.started`、`tool.completed`、`task.queued`、`task.reviewing`、`task.rework`、`task.completed`、`run.completed`、`error`；
- `task.reviewing` 表示候选结果已提交主 Agent；`task.rework` 表示首次验收拒绝并已安排唯一一次返工；
- `task.completed` 只能用于 `succeeded`、`failed`、`cancelled` 或 `interrupted` 终态，并 MUST 在 payload 中携带最终 `status`；
- `run.completed` 或 `error` MUST 是一次流的终止事件；
- 重连时实现 SHOULD 使用 `Last-Event-ID` 或等价游标恢复已持久事件。

### 6.3 Agent 工具结果

- 工具结果 MUST 是可 JSON 序列化 object；
- 工具失败 MUST 返回稳定错误码和可读消息；
- 任务 ID、文件引用和分页游标 MUST 位于独立字段；
- MUST NOT 通过 `[TASK_ID: ...]`、Markdown 标记或其他不可解析自然语言约定传递机器状态。

## 7. 验收标准

实现必须通过以下场景：

1. 两名新用户同时在同一群 `@Agent`，分别只创建一个 Workspace，并可并发得到回复；
2. 同一用户连续发送两条触发消息时顺序稳定，其他用户不被阻塞；
3. 普通群消息不创建用户、不触发、不落盘；群历史只通过 QQ 能力查询；
4. 私聊和不同群中的上下文互不混入，其他用户的记忆、文件和任务不可访问；
5. 禁用用户后，其新消息、工具和任务被拒绝，既有数据仍在；
6. 主 Agent 创建 Sub-agent 后立即恢复响应，且每用户 1、全局 4 的限制生效；
7. Sub-agent 完成后只产生候选结果，任务进入 `reviewing`，候选内容不会直接发送到渠道；
8. 主 Agent 使用原始需求和固定验收条件执行独立 review run，通过后才生成最终回复并转为 `succeeded`；
9. 首次拒绝会产生明确返工意见并自动返工一次；第二次拒绝转为 `failed`，不形成无限循环；
10. `queued` 任务在重启后继续，原 `running` 或 `reviewing` 任务变为 `interrupted` 且不自动重放；
11. 任务成功、验收失败、执行失败、取消和中断均可查询，并只向原始会话投递最终状态；
12. 普通用户只能列举和读取公告资料；不存在任何 Agent 公告写入工具；
13. 管理员可通过 Web 完整管理 Workspace，未认证请求和普通用户均无法访问；
14. 路径穿越、符号链接逃逸和跨 Workspace 文件引用均被拒绝；
15. REST、SSE、统一消息、验收结论与工具结果全部符合本文结构；
16. QQ 不支持的内容或动作返回明确降级信息，不静默丢失；
17. 系统不因定时器、空闲、巡检或模型自主判断主动发言。
