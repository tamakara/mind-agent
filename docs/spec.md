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

## 2. 用户、Workspace 与会话

- 渠道身份唯一键 MUST 为 `(source, account_id, platform_user_id)`；
- 首次私聊或首次群聊 `@Agent` MUST 原子创建用户和 Workspace；
- 普通未触发群消息 MUST NOT 创建用户；
- 每名用户 MUST 只有一个 Workspace；
- 私聊上下文 MUST 按用户隔离；
- 群聊上下文 MUST 按 `(account_id, group_id, user_id)` 隔离；
- 同一上下文 MUST 串行执行，不同上下文 MAY 并发执行；
- 被禁用用户 MUST NOT 启动新 Agent run 或任务；
- 普通用户 MUST NOT 登录 Web，管理员可以查看和管理全部 Workspace 文件。

所有文件路径 MUST 在解析后仍位于目标 Workspace、人设目录或知识库目录内。路径穿越和符号链接逃逸 MUST 被拒绝。

## 3. 统一消息模型

OneBot 事件 MUST 先转换为 `UnifiedMessage`，Agent 层 MUST NOT 读取 OneBot 原始字段。

```json
{
  "id": "onebot-event-123",
  "source": "onebot",
  "account_id": "bot-main",
  "conversation": {
    "type": "group",
    "id": "123456"
  },
  "sender": {
    "id": "10001",
    "name": "Alice"
  },
  "timestamp": "2026-07-19T08:00:00Z",
  "reply_to": null,
  "parts": [
    {
      "type": "text",
      "text": "请整理最近的讨论"
    }
  ]
}
```

`UnifiedMessage` MUST 包含：

- `id`；
- `source`，首版固定为 `onebot`；
- `account_id`；
- `conversation.type`：`private` 或 `group`；
- `conversation.id`；
- `sender.id` 与 `sender.name`；
- `timestamp`；
- 可空 `reply_to`；
- 非空 `parts`。

`MessagePart` 使用 `type` 作为判别字段：

| 类型 | 输入 | 输出 |
| --- | --- | --- |
| `text` | MUST | MUST |
| `mention` | MUST | MUST |
| `quote` | MUST | MUST |
| `image` | MUST | MUST |
| `audio` | MUST | 不要求 |
| `video` | MUST | 不要求 |
| `file` | MUST | MUST |

- OneBot 不支持或无法转换的消息段 MUST 返回明确降级信息，不得静默丢弃；
- 文件类 part MUST 使用稳定 `file_ref`，不得向 Agent 暴露绝对路径或临时下载地址；
- QQ 发送只需覆盖表格中的输出类型，不实现表情、戳一戳等扩展动作；
- 首版 MUST NOT 实现渠道注册表、能力插件或第二种 `source`。

## 4. 触发与群历史

- 私聊消息 MUST 触发 Agent；
- 群聊消息只有明确提及当前 Bot 时才能触发 Agent；
- 普通群消息 MUST NOT 触发、创建用户或写入 Workspace；
- 群历史 MUST 由 OneBot 按需查询，不得全量注入上下文；
- 群历史查询 MUST 绑定当前账号和当前群；
- 查询结果只用于当前 run，MUST NOT 写入用户历史或知识库；
- OneBot 不支持历史查询时 MUST 返回明确错误，不得伪造内容。

## 5. 简化 Scroll

每个 Workspace 使用 `history.db` 保存当前用户的情景历史。

MUST 持久化：

- 触发 Agent 的用户消息；
- Agent 回复；
- 工具调用与工具结果；
- Sub-agent 和验收 run 的关联事件。

Scroll MUST 遵循：

1. 每条历史记录拥有当前 Workspace 内递增的 `seq`；
2. 模型上下文只加载当前 Conversation 的最近完整轮次；
3. 达到 token 预算时，从最旧的已完成轮次开始驱逐；
4. 当前活动轮次和配对的 tool call/result MUST 保持完整；
5. 被驱逐区间使用 `seq lo-hi` 占位，不生成摘要；
6. Agent 只获得 `recall_history(expand)` 和 `recall_history(search)`；
7. recall MUST 强制绑定当前 `conversation_id`，不得跨会话读取；
8. 历史逐字保存，不生成 headline、分层索引、用户画像或长期记忆。

`expand` 按 `seq` 区间返回原文，`search` 只在当前 Conversation 的逐字文本中检索。两者 MUST 为只读结构化工具。

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

知识库是普通文件目录，不是 RAG 系统。

- 只有管理员 Web API 可以创建目录、上传、覆盖、移动和删除文件；
- Agent 只能使用 `list_knowledge_files` 和 `read_knowledge_file`；
- 普通用户不能直接访问知识库 Web API；
- 文本文件直接读取，其他支持格式复用普通文件读取能力；
- 知识库正文 MUST 只保存在文件目录中；
- MUST NOT 建立 embedding、向量索引、自动分块或自动知识提炼。

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
- 任务结果和状态只能发送到原始会话。

review run MUST 返回：

```json
{
  "accepted": true,
  "issues": [],
  "rework_instructions": null,
  "final_response": {
    "parts": [
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
3. OneBot 常用消息可以转换为统一模型，并完成规定的输入输出闭环；
4. Scroll 驱逐后可以在当前 Conversation 内展开和搜索原文；
5. 私聊 history 不能在群聊 recall 中读取；
6. 修改任一人设文件后，下一次 Agent run 使用新内容；
7. 知识库只有管理员可写，Agent 只能列举和读取；
8. Sub-agent 候选结果经过主 Agent 验收，最多自动返工一次；
9. QQ 状态页只读，系统不存在 Skills、备份、迁移或多渠道管理入口；
10. 路径穿越、符号链接逃逸和跨 Workspace 文件访问均被拒绝。
