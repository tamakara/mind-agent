# WorkHub 实现任务清单

> 本文用于持续记录实现进度。完成子任务并通过对应验证后，将 `[ ]` 改为 `[x]`；阶段内全部任务与验收门槛完成后，才勾选阶段完成项。
>
> 当前基线：对话式企业服务入口的业务提案、强制规范和技术设计已完成，尚未创建应用源码。

## 进度规则

- [x] 首版已收敛为单企业飞书文本私聊和年假闭环
- [x] 员工确认与 OA 业务审批边界已明确
- [x] Scroll、知识原文、WorkHub 状态和 Mock OA 业务数据的真相来源已明确
- [ ] 每次实现提交同步更新任务、测试和必要文档
- [ ] 不以跳过测试、放宽隔离或伪造成功结果的方式勾选任务

## P0. 工程基线

- [x] 创建 Python 3.12 `uv` workspace、`pyproject.toml`、`uv.lock` 和 `src/workhub/`
- [x] 创建独立 `mock-oa-service` Python 包和 `python -m mock_oa_service` 启动入口
- [x] 配置 FastAPI、Uvicorn、Pydantic v2、aiosqlite、LangGraph、OpenAI-compatible Provider、飞书 SDK、MCP 和 RAG 依赖
- [x] 创建 React 18、TypeScript、Vite、Ant Design 管理台骨架
- [x] 配置 Ruff、类型检查、pytest、Vitest、Playwright 和前端 lint/format
- [x] 建立 backend unit/contract/integration、Mock OA 和 frontend/e2e 测试目录
- [x] 创建 `.env.example`，覆盖数据目录、监听地址、管理员引导值和必要凭据占位
- [ ] 初始化 `<WORKHUB_DATA_DIR>` 与 `<MOCK_OA_DATA_DIR>`，限制目录和数据库权限
- [ ] 建立结构化错误、request ID、日志脱敏和统一 REST 错误响应
- [ ] 实现 `/healthz`、`/readyz` 和有限超时的启动/关闭生命周期
- [ ] 创建双服务 Docker Compose 和独立持久卷
- [ ] 验收：全新环境可安装、启动 WorkHub/Mock OA，并通过基础检查
- [ ] **P0 完成**

## P1. 存储、认证与审计基础

- [ ] 定义 UUID4、UTC RFC 3339、分页、revision 和结构化错误公共类型
- [ ] 实现 `app.db` schema 迁移和 repository transaction
- [ ] 所有 SQLite 连接启用 WAL、foreign keys 和 busy timeout
- [ ] 建立员工、身份、Session、Scroll、事件去重、待确认、配置、知识和审计基础表
- [ ] 为隔离读取建立 `(employee_id, session_id, seq)` 等复合索引和唯一约束
- [ ] 实现同目录临时文件、flush、fsync 和原子替换工具
- [ ] 实现安全相对路径解析，拒绝绝对路径、`..` 和符号链接逃逸
- [ ] 实现管理员首次初始化、密码哈希、登录、登出、Session 过期和登录限速
- [ ] 实现管理员认证中间件、HttpOnly/SameSite cookie、CSRF 与同源保护
- [ ] 实现审计写入器和按字段策略脱敏，不保存凭据、action token 或完整敏感参数
- [ ] 编写 schema 重复初始化、约束、并发写、原子文件、认证和脱敏测试
- [ ] 验收：失败事务不留下部分状态，未认证请求不能访问管理 API
- [ ] **P1 完成**

## P2. 员工目录与飞书身份

- [ ] 定义 `Employee`、`ChannelIdentity`、`ActorContext` 和 `FeishuMessage` 契约
- [ ] 实现员工号唯一、主管引用、active/disabled 状态和 repository
- [ ] 实现 identity `(channel, app_id, open_id)` 唯一与 unbound/bound 状态
- [ ] 未知 `open_id` 首次消息只 upsert 待绑定 identity，不创建 Session 或 run
- [ ] 实现管理员创建/编辑/启停员工和绑定/解绑 identity API
- [ ] 绑定后原子创建或复用员工唯一 Session
- [ ] 实现从当前绑定与员工快照构造只读 `ActorContext`
- [ ] 禁止 Agent 参数覆盖 employee/open_id 等可信主体字段
- [ ] 实现飞书官方 SDK 长连接、有限重连、健康状态和优雅关闭
- [ ] 实现私聊文本事件转换、`event_id` 去重和非文本/群聊拒绝
- [ ] 实现文本投递、交互卡片投递/更新和回复地址封装
- [ ] 卡片事件在普通消息路由之前分流，不进入自由 Agent 推理
- [ ] 编写并发首次消息、重复事件、禁用、解绑和跨身份测试
- [ ] 验收：未知用户只得到绑定提示；绑定用户稳定复用 Session；两名员工不串用身份
- [ ] **P2 完成**

## P3. Model Runtime 与 Scroll

- [ ] 定义 OpenAI-compatible Chat/Embedding Provider 接口
- [ ] 实现模型配置脱敏 CRUD、revision、连通性测试和超时
- [ ] 创建代码维护的办公助手系统 Prompt，不加载用户或 Persona 文件
- [ ] 建立 LangGraph 主 Agent loop、最大迭代、总超时和工具超时
- [ ] Runtime 只从 Feishu Adapter 接收已验证 `ActorContext` 和 `FeishuMessage`
- [ ] 实现每员工 Session 异步执行锁和不同员工并发
- [ ] 创建 `session_turns`、`session_events` 及递增 seq repository
- [ ] 逐字保存用户、Agent、知识/MCP 工具、动作创建和脱敏结果事件
- [ ] 实现 headline 结构化解析、200 字符限制和确定性回退
- [ ] 按完整 turn 和 token 预算构建 live context，不拆 tool call/result
- [ ] 实现 `[context compressed]` 与最近 20 个 headline 导航
- [ ] 实现 `recall_session_history(expand/search)`，强制绑定 ActorContext
- [ ] 搜索优先 FTS5，不可用时降级为参数化 LIKE
- [ ] 普通申请回合在确认卡片投递后完成并释放 Session 锁
- [ ] 编写 Prompt、turn 完整性、预算、headline、并发和跨员工 recall 测试
- [ ] 验收：旧回合被驱逐后仍能按 headline 展开/搜索逐字原文，不能跨员工读取
- [ ] **P3 完成**

## P4. 目录化 Knowledge / RAG

- [ ] 创建 `knowledge/originals`、`staging`、`index` 目录和知识 schema
- [ ] 实现目录树节点、稳定文档 ID、父子唯一名称和相对路径
- [ ] 实现目录/文档创建、移动、重命名、保存、删除 API
- [ ] 只接受 UTF-8 `.md`/`.txt` 和 10 MiB 限制，所有路径经过安全解析
- [ ] 保存使用 `expected_revision`、原子替换和 SHA-256 内容去重
- [ ] 内容变化时创建新版本与 queued 索引任务；哈希未变不计算 Embedding
- [ ] Markdown 按标题层级切分，TXT 递归字符切分，并保存原文行号
- [ ] 实现单并发索引消费者和 queued/indexing 重启恢复
- [ ] 实现 pending/active generation，全成功后原子切换
- [ ] 失败保存结构化错误和重试入口，旧 active generation 继续服务
- [ ] 移动/重命名只更新路径元数据，内容不变时不重新 Embedding
- [ ] 删除清理原文、chunk 元数据和全部向量 generation
- [ ] 启动 Reconciler 对账原文、元数据和索引，不一致项排队修复
- [ ] 实现 `search_knowledge`、`list_knowledge_directory` 和按行 `read_knowledge_document`
- [ ] 检索结果包含文档 ID、版本、路径、标题路径、行号、片段和分数
- [ ] 创建知识目录树、编辑器、索引状态、错误和重试 UI
- [ ] 编写路径、revision、哈希、切片定位、generation、重命名和故障测试
- [ ] 验收：保存自动索引，失败不影响旧索引，纯重命名不产生 Embedding 调用
- [ ] **P4 完成**

## P5. MCP 与持久化员工确认

- [ ] 定义 `ToolDescriptor`、`McpToolEffect` 和执行快照契约
- [ ] 创建 mcp_clients、mcp_tools、mcp_tool_settings repository
- [ ] 只实现 Streamable HTTP 状态化客户端和敏感 headers 脱敏语义
- [ ] 实现客户端连接、重连、关闭、工具发现和单客户端故障隔离
- [ ] 实现安全模型工具名、原始名称映射和冲突拒绝
- [ ] 实现工具白名单与 `allow/confirm/deny` 策略独立求值
- [ ] 新客户端和新发现工具默认 `deny`；deny 在执行端之前拒绝
- [ ] 从模型 Schema 移除主体字段和幂等键，并在执行包装器注入 ActorContext
- [ ] 创建 `pending_actions` repository、状态约束和唯一幂等键
- [ ] 规范化工具参数并计算哈希，生成确定性确认摘要和安全随机 token
- [ ] 数据库只存 token 哈希；飞书卡片只携带原 token 和决定
- [ ] `confirm` 在当前 run 只创建动作和卡片，确保 MCP 零调用
- [ ] 卡片投递失败时原子取消动作，禁止不可见动作被后续执行
- [ ] 回调校验 identity、员工状态、地址、token、有效期、参数哈希和工具策略
- [ ] 通过条件更新原子抢占 pending；取消和重复回调保持幂等
- [ ] 确认创建独立 confirmation turn，不启动 LLM，并调用 MCP
- [ ] 执行结果更新动作、Scroll、卡片和原飞书私聊回复
- [ ] 启动时过期旧 pending，并用幂等键恢复遗留 executing
- [ ] 创建 MCP 客户端、工具发现、白名单和策略 UI
- [ ] 编写策略、Schema 主体剥离、跨用户、过期、重放、竞态、重启和脱敏测试
- [ ] 验收：allow 直接执行；confirm 未确认零执行；deny 零连接；重复确认只产生一个业务请求
- [ ] **P5 完成**

## P6. 独立 Mock OA 服务

- [ ] 初始化 `mock_oa.db` 迁移、WAL、员工余额、请假申请和幂等表
- [ ] 创建与 WorkHub 分离的 Mock OA 配置、健康检查和结构化错误
- [ ] 实现可信员工主体传输约定并拒绝缺失或非法主体
- [ ] 实现 `query_leave_balance` MCP 工具
- [ ] 实现 `submit_leave_request` 的日期、工作日、余额和重复申请校验
- [ ] 在同一事务中按 `(employee_id, idempotency_key)` 创建或返回原申请
- [ ] 新申请固定进入 `pending_approval` 并返回业务单号
- [ ] 实现 `query_leave_request_status`，只返回当前员工自己的申请
- [ ] 实现受管理 token 保护的 Demo Admin REST 状态更新，仅允许 approved/rejected
- [ ] 确保状态更新接口不注册为 MCP 工具、不进入 Agent Prompt
- [ ] 提供本地演示种子员工、余额和示例制度文档
- [ ] 编写余额隔离、重复日期、余额不足、幂等、越权和状态更新测试
- [ ] 验收：超时重试只创建一张申请；WorkHub 只能查询而不能执行业务审批
- [ ] **P6 完成**

## P7. 最小管理端与端到端验收

- [ ] 创建登录页、应用布局、API client、错误边界和紧凑菜单
- [ ] 实现概览健康状态与员工、知识、MCP、飞书连接基础统计
- [ ] 创建员工列表/详情、启停和待绑定 identity 绑定界面
- [ ] 集成 P4 知识树、编辑器、保存冲突和索引状态界面
- [ ] 集成 P5 MCP 客户端、工具白名单和策略界面
- [ ] 创建 OpenAI-compatible Chat/Embedding 配置与测试页面
- [ ] 创建只读飞书状态页并展示待绑定 identity 入口
- [ ] 创建按员工、事件、工具、动作、业务单号和结果筛选的脱敏审计页
- [ ] 确认 Web 不提供测试聊天、业务审批或任意 MCP 调用入口
- [ ] 编写管理员登录、员工绑定、知识编辑、配置脱敏和菜单 Playwright 测试
- [ ] 编写未知用户、绑定、余额查询、制度检索、确认提交和状态查询端到端测试
- [ ] 编写两名员工并发、跨用户卡片、过期/取消/重复确认和重启恢复端到端测试
- [ ] 故障注入验证飞书断线、MCP 故障、Embedding 故障和旧索引退化
- [ ] 快照检查 API、日志和审计不泄露密钥、token 或完整敏感参数
- [ ] 编写本地启动、飞书应用配置、Mock OA 演示和数据卷说明
- [ ] 验收：README 的最小链路可独立复现，全部首版验收红线通过
- [ ] **P7 完成**

## 首版之后

只有在年假 MVP 验收后再评估：飞书通讯录同步、部门知识权限、请假撤销、报销附件、真实 OA、PostgreSQL、多副本、多企业、多渠道和长期个性化偏好。以上能力不得提前进入首版实现。
