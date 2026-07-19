# MindAgent 强制规范

> 状态：Accepted
>
> 本文是首版实现不可违背的契约。业务范围见 [proposal.md](proposal.md)，技术方案见 [design.md](design.md)。

## 1. 基本约定

- 本文中的 **MUST** 表示必须，**MUST NOT** 表示禁止；
- MindAgent 生成的内部 ID MUST 使用 UUID4；
- 持久时间 MUST 使用 UTC RFC 3339；
- API 字段 MUST 使用 `snake_case`；
- OneBot 提供的用户、群、消息和事件 ID MUST 作为不透明字符串处理。

## 2. 用户、Session 与 Workspace

- 渠道身份唯一键 MUST 为 `(channel, account_id, platform_user_id)`；
- 首次私聊或首次群聊 `@Agent` MUST 原子创建用户、Session 和 Workspace；
- 普通未触发群消息 MUST NOT 创建用户；
- 每名用户 MUST 只有一个 Session，且该 Session MUST 与用户稳定绑定；
- 每名用户 MUST 只有一个 Workspace；
- 同一用户从任意群聊或私聊触发的 Agent run MUST 进入该用户的同一个 Session；
- 群聊和私聊的账号、类型与窗口 ID MUST 只作为触发来源、渠道消息查询范围和回复投递目标，不得用于创建或选择 Session、Workspace；
- 同一用户 Session 的 run MUST 串行执行，不同用户 Session MAY 并发执行；
- 被禁用用户 MUST NOT 启动新 Agent run 或任务；
- 普通用户 MUST NOT 登录 Web，管理员可以查看和管理全部 Workspace 文件。

所有文件路径 MUST 在解析后仍位于目标 Workspace、人设目录或知识库目录内。路径穿越和符号链接逃逸 MUST 被拒绝。

## 3. 统一渠道消息模型

OneBot 实时事件和历史查询结果 MUST 使用同一个转换器生成 `ChannelMessage`，Agent 层 MUST NOT 读取 OneBot 原始字段。

`ChannelAddress` 描述消息在渠道中的来源与回复路由，不代表 Agent Session。命名采用 `Message` + `Content` 分层，但使用 `Channel` 前缀明确它属于外部渠道消息，避免与 Session 内部历史混淆。

```json
{
  "message_id": "onebot-message-123",
  "address": {
    "channel": "qq",
    "account_id": "bot-main",
    "conversation_type": "group",
    "conversation_id": "123456"
  },
  "sender": {
    "user_id": "10001",
    "display_name": "Alice"
  },
  "created_at": "2026-07-19T08:00:00Z",
  "content": [
    {
      "type": "text",
      "text": "请整理最近的讨论"
    }
  ]
}
```

`ChannelMessage` MUST 包含：

- `message_id`；
- `address.channel`，首版固定为 `qq`；
- `address.account_id`；
- `address.conversation_type`：`private` 或 `group`；
- `address.conversation_id`；
- `sender.user_id` 与 `sender.display_name`；
- `created_at`；
- 非空 `content`。

`MessageContent` 使用 `type` 作为判别字段：

| 类型 | 输入 | 输出 |
| --- | --- | --- |
| `text` | MUST | MUST |
| `mention` | MUST | MUST |
| `quote` | MUST | MUST |
| `image` | MUST | MUST |
| `audio` | MUST | 不要求 |
| `video` | MUST | 不要求 |
| `file` | MUST | MUST |
| `unsupported` | MUST | MUST NOT |

- `MentionContent` MUST 使用 `target_user_id`，`QuoteContent` MUST 使用被引用消息的 `message_id`；
- OneBot 不支持或无法转换的消息段 MUST 转换为 `UnsupportedContent`，不得静默丢弃；
- 文件类 content MUST 使用稳定 `file_ref`，不得向 Agent 暴露绝对路径或临时下载地址；
- QQ 发送只需覆盖表格中的输出类型，不实现表情、戳一戳等扩展动作；
- 首版 MUST NOT 实现渠道注册表、能力插件或第二种 `channel`。

## 4. 触发与渠道消息历史

- 私聊消息 MUST 触发 Agent；
- 群聊消息只有明确提及当前 Bot 时才能触发 Agent；
- 普通群消息 MUST NOT 触发、创建用户或写入 Workspace；
- 群聊或私聊消息记录 MUST 通过 `query_channel_history` 按需查询，不得全量注入 Session 上下文；
- `query_channel_history` MUST 绑定当前触发消息的 `ChannelAddress`，Agent 不得指定其他群或私聊窗口；
- 首次查询的 `anchor_message_id` 默认使用触发消息 ID；Agent MAY 指定当前窗口中的另一条消息作为锚点，查询该消息及其之前的记录；
- 后续分页 MUST 使用 OneBotGateway 返回的不透明 `cursor`，Agent 不得构造或解析 cursor；
- 查询结果 MUST 返回 `ChannelMessagePage`，至少包含 `messages`、`next_cursor` 和 `has_more`；其中每条 `messages` MUST 与实时事件使用相同的 `ChannelMessage` 格式，并按时间正序排列；
- OneBotGateway MUST 验证锚点消息属于当前 `ChannelAddress`；无法验证或发生跨窗口定位时 MUST 拒绝；
- 查询结果只用于当前 run，MUST NOT 写入 Session 历史、Workspace 或知识库；
- OneBot 不支持历史查询时 MUST 返回明确错误，不得伪造内容。

## 5. 简化 Scroll

每个 Workspace 使用 `history.db` 保存当前用户唯一 Session 的情景历史。

MUST 持久化：

- 触发 Agent 的用户消息；
- Agent 回复；
- 工具调用与工具结果；
- Sub-agent 和验收 run 的关联事件。

Scroll MUST 遵循：

1. 每条历史记录拥有当前 Workspace 内递增的 `seq`；
2. 模型上下文只加载当前用户 Session 的最近完整轮次，不按群聊或私聊窗口过滤；
3. 达到 token 预算时，从最旧的已完成轮次开始驱逐；
4. 当前活动轮次和配对的 tool call/result MUST 保持完整；
5. 被驱逐区间使用 `seq lo-hi` 占位，不生成摘要；
6. Agent 只获得 `recall_session_history(expand)` 和 `recall_session_history(search)`；
7. recall MUST 强制绑定当前用户 Session，不得跨用户 Session 读取；
8. 历史逐字保存，不生成 headline、分层索引、用户画像或长期记忆。

`recall_session_history` 读取 MindAgent 自己保存的用户 Session 历史；`query_channel_history` 读取 QQ 当前窗口的外部消息记录。二者 MUST 使用不同的工具名、参数类型和返回类型，不得复用或隐式回退。

`expand` 按 `seq` 区间返回 `SessionHistoryEntry`，`search` 只在当前用户 Session 的逐字文本中检索。两者 MUST 为只读结构化操作，返回值不得伪装成 `ChannelMessage`。

## 6. 全局人设

全局人设目录固定包含：

```text
persona/
├── AGENTS.md
├── SOUL.md
└── PROFILE.md
```

- 三个文件 MUST 按 `AGENTS.md`、`SOUL.md`、`PROFILE.md` 顺序加载；
- `PROFILE.md` MUST 只描述 MindAgent，不得保存具体用户资料；
- 管理员可以通过 Web 读取和编辑三个文件；
- 写入 MUST 使用临时文件和原子替换；
- 每次 Agent run MUST 读取最新内容，无需重启；
- 首版 MUST NOT 增删、启停或重排人设文件；
- 首版 MUST NOT 实现 ZIP 导入、语言模板或 `BOOTSTRAP.md`。

## 7. 知识库

知识库是管理员维护、所有 Agent 只读的全局 RAG 知识库。

- 只有管理员 Web API 可以配置 Embedding、上传、显式替换、重建和删除文档；普通用户 MUST NOT 直接访问知识库 Web API；
- 首版 MUST 只接受不超过 10 MiB 的 UTF-8 `.txt` 和 `.md` 文件，无法解码或扩展名不匹配时 MUST 拒绝；
- 同名上传 MUST 默认拒绝；显式替换 MUST 保留原 `document_id`，且旧 active generation MUST 持续可检索直到新 generation 构建成功；
- 文档状态只允许 `queued`、`indexing`、`ready` 和 `failed`；失败 MUST 保存结构化错误并允许重试；
- 默认切块参数 MUST 为 `chunk_size=1000`、`chunk_overlap=150`；`chunk_size` MUST 位于 200–8000，`chunk_overlap` MUST 位于 0–1000 且小于 `chunk_size`；
- 文档 MAY 覆盖默认切块参数，实际参数 MUST 随文档版本持久化；
- `.txt` MUST 使用递归字符切块；`.md` MUST 先按标题层级切分，再对过长内容递归切块；每个 chunk MUST 保留文档、标题路径和原文起止行定位；
- Embedding MUST 支持 OpenAI-compatible 和外部 Ollama；Ollama 由用户独立部署，MindAgent 只连接其 HTTP 地址；
- Embedding 配置应用前 MUST 提供连通性测试；新配置 MUST 先作为 pending generation 后台重建全部文档，全部成功后才能切换为 active，失败时 MUST 保留旧配置和旧索引；
- 查询 MUST 只读取 active generation；文档重建、替换或配置迁移期间 MUST 继续使用旧 active generation；
- 知识索引任务 MUST 单并发执行；服务启动时 `queued` 任务继续执行，遗留 `indexing` 任务 MUST 重新排队；
- 首次 Embedding 配置在连通性测试成功后 MUST 直接创建空 active collection；没有 active 配置时 MUST NOT 接受文档上传；
- 原始文件 MUST 是知识正文的真相来源，chunk、向量和 generation MUST 可由原始文件与配置重建；
- Agent 只能使用 `search_knowledge`、`list_knowledge_documents` 和 `read_knowledge_document`，MUST NOT 写入或修改知识库；
- `search_knowledge` MUST 为显式工具调用，不得在每轮 Agent run 中自动执行；`top_k` 默认 5 且 MUST 位于 1–10，可选 `score_threshold` MUST 位于 0–1；
- `search_knowledge` MUST 返回 `document_id`、`chunk_id`、文件名、标题路径、起止行、片段和相关度分数；`read_knowledge_document` MUST 按文档 ID 与行号读取原文；
- `read_knowledge_document` 单次 MUST NOT 返回超过 500 行；知识库是全局共享数据，检索 MUST NOT 按用户或 Session 改变可见范围；
- 知识库工具 MUST 与 `recall_session_history`、`query_channel_history` 使用不同的数据源、参数和返回类型，不得隐式回退；
- 未配置 Embedding、索引不可用或 Provider 故障时 MUST 返回结构化错误，不得伪造检索结果；
- 删除文档 MUST 移除其原始文件、chunk 元数据和所有向量 generation，删除完成后 MUST NOT 再被检索。

## 8. Sub-agent 与验收

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
- 任务创建时 MUST 保存原始要求和验收条件；
- 默认并发为每用户 1 个、全局 4 个；
- Sub-agent MUST 使用独立 run 和上下文；
- Sub-agent 完成后只保存候选结果，并将任务转为 `reviewing`；
- 候选结果 MUST NOT 直接发送给用户；
- 主 Agent MUST 使用独立 review run 验收候选结果；
- 验收通过后才能转为 `succeeded` 并发送最终回复；
- 首次验收不通过时 MUST 携带返工意见自动执行第二次；
- 第二次仍不通过时 MUST 转为 `failed`，不得继续循环；
- 服务重启时，`queued` 任务继续等待，`running` 和 `reviewing` 任务转为 `interrupted`；
- 任务结果和状态只能发送到创建任务时记录的原始渠道投递目标。

review run MUST 返回：

```json
{
  "accepted": true,
  "issues": [],
  "rework_instructions": null,
  "final_response": {
    "content": [
      {
        "type": "text",
        "text": "任务已完成。"
      }
    ],
    "artifact_refs": []
  }
}
```

`accepted=false` 时，`issues` 和 `rework_instructions` MUST 非空，`final_response` MUST 为 null。

## 9. Web 与 API

Web 只允许管理员登录，页面范围以 `proposal.md` 为准。QQ 页面 MUST 为只读状态页，不得提供渠道参数修改。

REST 成功响应统一为：

```json
{
  "data": {},
  "request_id": "2a56f39e-7d84-4d2a-87b3-5d8b52a38646"
}
```

REST 错误响应统一为：

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Resource not found."
  },
  "request_id": "2a56f39e-7d84-4d2a-87b3-5d8b52a38646"
}
```

测试聊天使用 SSE；每个事件 MUST 包含 `run_id`、递增 `sequence`、`timestamp` 和 `payload`。机器状态 MUST 使用结构化字段，不得藏在自然语言标记中。

## 10. 首版验收

1. 两名用户在同一群中并发 `@Agent`，上下文、文件和任务互不串用；
2. 普通群消息不触发、不创建用户、不落盘；
3. OneBot 实时事件与历史查询结果可以转换为同一 `ChannelMessage` 模型，并完成规定的输入输出闭环；
4. Scroll 驱逐后可以在当前用户 Session 内展开和搜索原文；
5. 同一用户从私聊和不同群聊触发时复用同一 Session 和 Workspace，不同用户之间不能 recall；`recall_session_history` 与 `query_channel_history` 的数据源和返回类型可明确区分；
6. 修改任一人设文件后，下一次 Agent run 使用新内容；
7. TXT 与 Markdown 可以按配置切块并异步建立向量索引；同名替换、文档重建或 Embedding 配置迁移失败时旧 active 索引仍可用，Agent 只能搜索、列举和按行读取；
8. Sub-agent 候选结果经过主 Agent 验收，最多自动返工一次；
9. QQ 状态页只读，系统不存在 Skills、备份、迁移或多渠道管理入口；
10. 路径穿越、符号链接逃逸和跨 Workspace 文件访问均被拒绝。
