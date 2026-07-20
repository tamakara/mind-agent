# MindAgent 实现任务清单

> 本文用于持续记录实现进度。完成子任务并通过对应验证后，将 `[ ]` 改为 `[x]`；只有阶段内全部任务与验收门槛完成后，才勾选阶段完成项。
>
> 当前基线：仓库已完成业务提案、强制规范和技术设计，尚未创建应用源码。

## 进度规则

- [x] 业务范围、强制规范和技术设计已对齐并标记 Accepted
- [x] Workspace 用户隔离边界与全局 Agent 能力边界已明确
- [x] Skills scripts 首版只读不执行，MCP 详细主体规则不在首版范围
- [ ] 每次实现提交同步更新本清单、相关测试和必要文档
- [ ] 不以跳过测试、放宽安全约束或伪造成功结果的方式勾选任务

## P0. 项目骨架与开发基线

- [ ] 创建 Python 3.12 `uv` 项目、`pyproject.toml`、`uv.lock` 和 `src/mindagent/` 包
- [ ] 配置 FastAPI、Uvicorn、Pydantic v2、aiosqlite、LangGraph、模型 Provider、MCP 和测试依赖
- [ ] 创建 React 18、TypeScript、Vite、Ant Design 管理台骨架
- [ ] 建立 backend unit/contract/integration 与 frontend unit/e2e 测试目录
- [ ] 配置 Ruff、类型检查、前端 lint/format、pytest、Vitest 和 Playwright 命令
- [ ] 建立 `.env.example`，只包含数据目录、监听地址和首次引导配置
- [ ] 实现应用配置加载和 `<MINDAGENT_DATA_DIR>` 初始化
- [ ] 建立结构化错误基类、request ID 中间件和统一 REST 响应格式
- [ ] 建立 `/healthz`、`/readyz` 与应用启动/关闭生命周期
- [ ] 验收：全新环境可安装依赖、启动空应用并通过基础检查
- [ ] **P0 完成**

## P1. Domain 契约与持久化基础

- [ ] 定义 UUID4、UTC RFC 3339、分页和 revision 公共类型
- [ ] 定义 `ChannelAddress`、`ChannelMessage` 和全部 `MessageContent` 判别联合
- [ ] 定义 `AgentRequest`、`AgentResponse`、工具调用、任务和审批领域类型
- [ ] 定义稳定结构化错误码及 HTTP/工具错误映射
- [ ] 实现 app.db schema 初始化与轻量版本迁移机制
- [ ] 所有 SQLite 连接启用 WAL、foreign keys 和 busy timeout
- [ ] 实现 repository transaction、唯一约束和并发写入封装
- [ ] 实现同目录临时文件、flush、fsync 和原子替换工具
- [ ] 实现安全路径解析，拒绝绝对路径、`..`、符号链接逃逸和跨根目录访问
- [ ] 限制数据目录和 app.db 文件权限，并建立日志脱敏工具
- [ ] 为领域 DTO、数据库约束、原子文件和安全路径编写测试
- [ ] 验收：schema 可重复初始化，失败写入不留下部分状态或半文件
- [ ] **P1 完成**

## P2. 管理员认证与管理台框架

- [ ] 实现管理员首次初始化、密码哈希、登录、登出和 Session 过期
- [ ] 实现管理员认证中间件、CSRF/同源策略和登录限速
- [ ] 创建管理台登录页、应用布局、错误边界和 API client
- [ ] 按设计建立静态非折叠菜单：概览、管理、智能体、设置
- [ ] 不提供 Web 测试聊天或其他 Agent 交互入口，不引入动态插件菜单
- [ ] 建立路由占位页：用户、知识库、任务、人设文件、Skills、内置工具、MCP、模型、QQ 状态
- [ ] 实现概览所需健康状态与基础统计 API
- [ ] 为登录、权限拒绝、Session 过期、菜单顺序和路由刷新编写测试
- [ ] 验收：未登录只能访问登录/健康接口，管理员可以访问全部占位页
- [ ] **P2 完成**

## P3. 用户、Session 与 Workspace

- [ ] 创建 users、channel_identities、agent_sessions 和 workspaces 表及 repository
- [ ] 以 `(channel, account_id, platform_user_id)` 原子解析或创建内部用户
- [ ] 首次有效私聊原子创建唯一 Session、Workspace 和目录结构
- [ ] Workspace 目录只使用内部用户 UUID，不使用 QQ ID
- [ ] 实现每用户 Session 异步执行锁和不同用户并发
- [ ] 实现用户启用/禁用；禁用用户不能创建新 run 或任务
- [ ] 实现 Workspace files/artifacts/tasks 注册与安全读写
- [ ] 创建用户列表、详情和启停 API
- [ ] 用户详情承载 PROFILE、MEMORY、历史、文件、产物和任务入口，不创建独立 Workspace 菜单
- [ ] 编写并发首次创建、唯一约束、跨用户访问和路径逃逸测试
- [ ] 验收：两名用户的 Session、目录、文件和锁完全隔离
- [ ] **P3 完成**

## P4. Persona / Profile / Memory

- [ ] 首次启动初始化 `persona/AGENTS.md` 和 `persona/SOUL.md`
- [ ] 创建用户 Workspace 时初始化 PROFILE.md 和 MEMORY.md
- [ ] 实现 frontmatter 剥离和固定顺序 Prompt Builder
- [ ] 每次 run 重新读取四文件正文，不缓存文件内容
- [ ] 实现全局人设 GET/PUT API、32 KiB 限制、SHA-256 revision 和冲突检查
- [ ] 实现 `read_user_context_file` 与 `replace_user_context_file`
- [ ] 通用文件工具禁止修改、删除或重命名 PROFILE/MEMORY
- [ ] 创建人设文件页面和用户详情中的 PROFILE/MEMORY 编辑器
- [ ] 显示 revision 冲突并允许管理员重新加载，不静默覆盖
- [ ] 编写注入顺序、原子替换、大小限制、revision 和跨用户测试
- [ ] 验收：全局文件影响所有用户下一次 run，用户文件只影响所属用户
- [ ] **P4 完成**

## P5. Model Providers 与基础 Runtime

- [ ] 定义统一 Chat Model 与 Embedding Provider 接口
- [ ] 实现 OpenAI-compatible Chat Provider
- [ ] 实现 OpenAI-compatible 与外部 Ollama Embedding Provider
- [ ] 创建模型配置表、脱敏 CRUD 和连通性测试 API
- [ ] 实现模型配置页面；敏感字段省略保留、`null` 清除、掩码不可写回
- [ ] 建立 LangGraph 主 Agent loop 和最大迭代/超时限制
- [ ] Runtime 从 `AgentRequest` 解析用户、Session、Workspace 和 ChannelAddress
- [ ] 定义每次 run 的不可变 Runtime/Capability Snapshot
- [ ] 实现 `AgentResponse` 到统一内容与产物引用的输出
- [ ] 编写 Provider mock、配置脱敏和 run 隔离测试
- [ ] 验收：管理员可配置并测试模型连通性，Agent run 仍只由 QQ 私聊触发
- [ ] **P5 完成**

## P6. Context / Scroll

- [ ] 为每个 Workspace 初始化独立 history.db
- [ ] 创建 session_history schema，支持 seq、turn_id、角色、正文、工具关联、headline 和地址
- [ ] 完整持久化用户消息、Agent 回复、tool call/result、Sub-agent 和 review 关联事件
- [ ] 实现 headline 解析、200 字符限制和确定性回退
- [ ] 按完整 turn 构建 live context，禁止拆开 tool call/result
- [ ] 实现 token 预算和从最旧完成 turn 开始的驱逐
- [ ] 实现 `[context compressed]` 索引和最近 20 个 headline 导航
- [ ] 实现 `recall_session_history(expand)` 与 `search`
- [ ] search 优先使用 FTS5，不可用时安全降级为参数化 LIKE
- [ ] 保证 recall 只绑定当前 Session，且不与 QQ 历史或 Knowledge 隐式回退
- [ ] 编写 turn 完整性、预算边界、headline、并发写入和跨用户测试
- [ ] 验收：被驱逐历史可以导航、展开和搜索原文，不能跨用户召回
- [ ] **P6 完成**

## P7. QQ Channel Adapter

- [ ] 实现 NapCat OneBot v11 反向 WebSocket 接入和生命周期管理
- [ ] 实现事件去重、心跳、连接状态和 `echo` Future 关联
- [ ] 建立实时事件与历史结果共用的 OneBot Message Converter
- [ ] 完整转换 text、mention、quote、image、audio、video、file 和 unsupported
- [ ] 实现 QQ 文件注册与稳定 `file_ref`，不向 Agent 暴露临时 URL/绝对路径
- [ ] 将 `AgentResponse` 转换为规定的 OneBot 输出动作
- [ ] 实现 `query_channel_history` 的锚点验证、不透明 cursor 和当前私聊约束
- [ ] 查询结果仅用于当前 run，不写入 history.db、Workspace 或 Knowledge
- [ ] 实现只读 `/api/v1/qq/status` 和 QQ 状态页
- [ ] 在创建普通 AgentRequest 前预留审批命令截获入口
- [ ] 编写 OneBot fixtures、实时/历史转换一致性、跨窗口拒绝和断线测试
- [ ] 验收：QQ 私聊完成收发闭环，历史补偿不能越出当前私聊
- [ ] **P7 完成**

## P8. 内置工具与 Tool Registry

- [ ] 实现 `ToolDescriptor`、代码级 Tool Registry 和重复名称检查
- [ ] 明确首版所有内置工具的名称、分类、默认启用状态和参数 Schema
- [ ] 创建 builtin_tool_settings 表与全局覆盖 repository
- [ ] 合并 Registry 默认值和数据库覆盖，生成不可变工具快照
- [ ] 将 Persona、Context、Knowledge、Task、Workspace 等工具接入 Registry
- [ ] 标记 Runtime 必需能力为 `configurable=false`，不显示管理开关
- [ ] 实现工具列表、单个启停和批量启停 API
- [ ] 创建内置工具页面，支持搜索、分类、单个和全部启停
- [ ] 工具执行从当前 AgentRequest 推导用户边界，不接受任意 user_id 或绝对路径
- [ ] 编写默认值、覆盖、热更新、重复名称、系统保留工具和安全边界测试
- [ ] 验收：配置只影响后续 run，任何开关都不能关闭 Workspace/Persona 安全约束
- [ ] **P8 完成**

## P9. Skills

- [ ] 创建 skills 表、全局 skills 根目录和 SkillService
- [ ] 实现 `skill_key`、UTF-8 `SKILL.md`、frontmatter 和 128 KiB 限制校验
- [ ] 实现按相对路径排序的整目录 revision 哈希
- [ ] 创建/编辑/覆盖使用 `expected_revision` 乐观并发控制
- [ ] 实现 staged directory 和原子安装/替换/删除
- [ ] 实现 ZIP 导入的 5 MiB、20 MiB 解压、256 文件限制
- [ ] 拒绝 ZIP 绝对路径、`..`、符号链接、硬链接和压缩炸弹
- [ ] 实现 Skill 列表、搜索、创建、保存、导入、资源树、启停和删除 API
- [ ] 实现系统保留 `read_skill_resource`，限制根目录、UTF-8 和单次 64 KiB
- [ ] Prompt 只注入启用 Skill 的 key/name/description 目录
- [ ] 确保 scripts 可列出、查看和下载，但没有执行入口且通用 Workspace 工具不可访问
- [ ] 创建 Skills 页面、编辑抽屉、资源树、导入和冲突处理 UI
- [ ] 编写 revision、原子性、ZIP 攻击、二进制、路径逃逸和 scripts 不可执行测试
- [ ] 验收：Skill 可完整管理并按需读取，恶意包不能写出 skills 根目录
- [ ] **P9 完成**

## P10. MCP 客户端与工具策略

- [ ] 创建 mcp_clients 和 mcp_tool_settings 表及 repository
- [ ] 实现安全唯一 `client_key` 和敏感配置脱敏/保留/清除语义
- [ ] 实现 stdio 配置判别校验和状态化客户端
- [ ] 实现 Streamable HTTP 配置判别校验和状态化客户端
- [ ] 实现标准 `mcpServers` JSON 单个/批量导入
- [ ] MCPManager 启动时并发连接启用客户端，单客户端失败隔离
- [ ] 实现客户端级关闭、重建、重连和应用退出清理
- [ ] 实现工具发现、原始名称映射、OpenAI 安全名称规范化和冲突拒绝
- [ ] 实现白名单：`null` 暴露全部、`[]` 全部关闭、显式数组只暴露列出工具
- [ ] 实现策略：`tool_effect ?? default_effect`，新客户端默认 `ask`
- [ ] 实现逐工具继承/覆盖、修改默认不删除覆盖和清除全部覆盖
- [ ] `deny` 在连接执行端前返回 `MCP_TOOL_DENIED`
- [ ] 实现 MCP 客户端 CRUD、启停、测试、工具和策略 API
- [ ] 创建 MCP 卡片、配置编辑、工具列表、Schema 和策略 UI
- [ ] 编写两种 transport、故障隔离、白名单、策略优先级、热更新和名称冲突测试
- [ ] 验收：多个 MCP 独立运行，客户端变更不改写正在执行的 run 快照
- [ ] **P10 完成**

## P11. MCP Approval Service

- [ ] 创建 tool_approvals 表和状态机：pending → approved/denied/expired/cancelled
- [ ] 创建不可预测的一次性审批码并设置 120 秒过期
- [ ] 审批绑定用户、Session、run、tool call、工具名和原始 ChannelAddress
- [ ] QQ Adapter 截获 `/approve <code>` 与 `/deny <code>`，不创建新 Agent run
- [ ] 审批命令绕过被等待 run 持有的 Session 锁，但只能完成审批决策
- [ ] 拒绝跨用户、跨 Session、跨地址、重复、过期和未知审批码
- [ ] `ask` 策略等待有效决定后才调用 MCP 执行端
- [ ] 后台无审批表面时立即返回 `MCP_APPROVAL_UNAVAILABLE`
- [ ] 服务启动时将遗留 pending 审批标记 expired
- [ ] 审批记录和日志不保存未脱敏凭据或完整敏感参数
- [ ] 编写竞态、超时、重放、身份绑定、Session 锁和重启恢复测试
- [ ] 验收：`allow/ask/deny` 分别完成直接执行、确认后执行和零执行拒绝
- [ ] **P11 完成**

## P12. Knowledge / RAG

- [ ] 创建 knowledge.db、originals、staging 和 chroma 目录
- [ ] 创建文档、版本、chunk、generation 和索引任务 schema
- [ ] 实现 UTF-8 `.txt`/`.md`、10 MiB 限制和同名默认拒绝
- [ ] 实现 Markdown 标题感知切分和 TXT 递归字符切分
- [ ] 保存标题路径、原文起止行、实际切块参数和 generation
- [ ] 实现 Embedding 连通性测试和首次空 active collection
- [ ] 实现单并发索引队列及 queued/indexing 重启恢复
- [ ] 实现 active/pending generation 构建与全成功原子切换
- [ ] 替换、重建或 Provider 迁移失败时继续使用旧 active generation
- [ ] 实现文档列表、上传、替换、重试、重建、删除和原文读取 API/UI
- [ ] 实现 `search_knowledge`、`list_knowledge_documents`、`read_knowledge_document`
- [ ] Knowledge 工具保持 Agent 只读且不按用户改变可见范围
- [ ] 编写切块定位、generation 切换、失败回退、删除和 Provider 故障测试
- [ ] 验收：索引迁移失败不影响旧索引查询，Agent 可回到带行号原文
- [ ] **P12 完成**

## P13. Tasks / Sub-agent / Review

- [ ] 创建 tasks 表、状态约束和 Task repository
- [ ] 实现进程内队列、每用户并发 1、全局并发 4
- [ ] 保存原始请求、验收条件、用户、Session、Workspace 和投递地址
- [ ] Sub-agent 使用独立 run、能力快照和任务目录
- [ ] Sub-agent 只提交候选结果，不直接投递用户
- [ ] 主 Agent 使用独立 review run 返回强类型验收结果
- [ ] 首次失败携带返工意见执行唯一一次返工
- [ ] 第二次失败后终止，不继续循环
- [ ] 启动时 queued 继续等待，running/reviewing 转 interrupted
- [ ] 实现任务列表、详情、取消 API 和管理台页面
- [ ] 最终结果只投递到任务创建时记录的原始 ChannelAddress
- [ ] 编写状态机、并发限制、返工上限、重启和跨用户测试
- [ ] 验收：候选结果必须验收后才能发送，服务重启不伪造任务成功
- [ ] **P13 完成**

## P14. 可观测性、安全与故障退化

- [ ] 为主 Agent、Sub-agent 和 review run 建立独立 trace
- [ ] 为 LLM、内置工具、MCP、策略决定、审批、知识索引建立子 span
- [ ] 记录必要 ID、模型、耗时和结构化错误，不上传密钥或完整路径
- [ ] 接入可选 Langfuse；不可用时退化为本地结构化日志
- [ ] 实现有限超时 flush 和不阻断应用关闭
- [ ] 健康状态覆盖 DB、QQ、模型、Embedding、MCP 和后台队列
- [ ] 对管理员登录、上传、MCP 测试和审批接口增加限速
- [ ] 审核所有 API 响应、异常和日志的凭据脱敏
- [ ] 验证被禁用用户、跨 Workspace、Skill 路径和 MCP 策略均 fail closed
- [ ] 编写降级、超时、敏感数据快照和故障注入测试
- [ ] 验收：可观测性组件失败不影响核心 run，安全组件失败不放行受控操作
- [ ] **P14 完成**

## P15. 全链路测试、部署与发布

- [ ] 编写管理员 Web 端到端流程：登录、菜单、人设、用户、Skills、工具、MCP、Knowledge、任务和设置
- [ ] 编写两名 QQ 用户并发私聊端到端测试
- [ ] 编写四文件生效、Scroll recall、知识检索和长任务验收端到端测试
- [ ] 编写 MCP allow/ask/deny 和 QQ 审批端到端测试
- [ ] 编写 Skill ZIP 攻击与 scripts 不可执行端到端安全测试
- [ ] 运行并通过 backend unit/contract/integration 全套测试
- [ ] 运行并通过 frontend unit 与 Playwright 全套测试
- [ ] 运行静态检查、类型检查、依赖审计和 secret scan
- [ ] 创建单 Worker MindAgent + NapCat Docker Compose 和持久卷
- [ ] 容器启动时验证数据目录权限、schema 初始化和健康检查
- [ ] 文档说明 stdio MCP 依赖必须预装，系统不自动下载 MCP/Skill 依赖
- [ ] 编写安装、升级、备份凭据风险、故障排查和运维文档
- [ ] 使用干净数据目录完成一次发布候选安装与验收
- [ ] 对照 `docs/spec.md` 第 14 节逐项签收首版验收条件
- [ ] **P15 完成：首版可发布**

## 暂不实施（非任务）

- QQ 群聊和第二渠道
- 多 Agent 与 per-Agent 能力配置
- 插件系统和动态菜单注册
- Skill 脚本执行、沙箱和依赖自动安装
- 技能市场、在线安装和自动更新
- MCP SSE、OAuth、Resources、Prompts 和详细主体规则
- 自动记忆整理、后台 dream 和 memory_search
- 备份恢复中心、迁移中心和多服务拆分
