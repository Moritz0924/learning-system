# 项目完整性审计报告（2026-06-25，更新于 2026-07-18）

> 本报告保留按日期记录的历史审计段落；较早段落中的测试数量、认证缺口和依赖漏洞状态可能已被后续补充取代。当前结论以本页顶部的 2026-07-18 状态和独立稳定基线验收记录为准。

## 结论摘要

项目已经具备 FastAPI、SQLAlchemy/Alembic、JWT Principal、Phase2 LangGraph、结构化讲师上下文、RAG/测验/计划调整、Next.js 16、Docker Compose 和五 Job CI。稳定基线已通过，但从“真实用户输入、真实文件、真实外部 provider、生产监控”的标准看仍不是完整生产闭环。

当前最重要的缺口有三类：

1. 入学诊断仍由前端提交硬编码自评、知识题答案、截止日期和学习偏好；没有版本化模板、纯函数评分、请求幂等或真实四步用户表单。
2. 文档链路已有对象存储、Outbox、Celery、解析器和状态查询，但尚无 `POST /api/documents` multipart 文件接口与真实文件选择 UI；现有入口是 JSON/Markdown 兼容链路。
3. remote embedding、LLM 与 Brave 的真实 key smoke、告警和人工重放仍未闭合。长期记忆、混合 RAG、RRF/rerank 和新 Agent 明确不在当前阶段范围内。

## 本次验证结果

已通过：

- `.\scripts\test.ps1`（compileall + pytest）：`218 passed, 1 warning`
- `npm run test:ui-routes`：通过
- `npm run lint`：通过
- `npm run build`：通过
- `npm run test:e2e -- --project=chromium`：`4 passed`，覆盖未认证重定向、注册/原子 onboarding/refresh/logout、注册错误脱敏和并发标签页恢复且不持久化 token。
- `npm audit --omit=dev --audit-level=high`：退出码 `0`；`0 high`、`0 critical`，仍有 Next.js 内嵌 PostCSS 的 `2 moderate`。
- `.\scripts\verify-compose.ps1`：2026-07-18 按当前源码执行当前 Compose 项目 `down -v`、无缓存构建和启动；Postgres healthy，backend、frontend、worker、scheduler、Redis、MinIO 七个服务运行，OpenAPI/前端探针通过，迁移为唯一 `20260716_0012 (head)`，backend/worker/scheduler runtime UID 非 root。
- `/api/health/ready`：弱开发凭据和缺少 remote provider key 时返回预期 `503 not_ready`；验收脚本同时证明 HTTP 服务可达，不把预期 503 当作宕机。
- GitHub Actions：`backend-tests`、`frontend-quality`、`frontend-e2e`、`migration-postgres`、`docker-build` 五个 Job 全绿；PostgreSQL Job 完成 `head → 20260626_0004 → head` 并检查 vector 扩展、关键表、唯一索引与单 head。
- Alembic：当前只有一个 head `20260716_0012`；稳定基线没有新增迁移。
- 真实 MinIO/Redis/Celery worker 作业 smoke：通过。经 Compose backend API 创建用户、上传 unsupported mime 文档，worker 从 Redis 收到 `documents.process_upload`，从对象存储恢复内容后返回 `parse_error="unsupported document mime type: application/x-smoke"`，API list 显示 `parse_status=failed`。
- backend 容器 Tesseract smoke：通过。`tesseract --version` 显示 5.5.0，`tesseract --list-langs` 包含 `eng`、`chi_sim`、`osd`。
- `/api/health/ready`（Compose production env）：按预期返回 `503`，弱默认凭据和缺外部 key 写入 `missing`，实际依赖探针返回 `unavailable=[]`，证明已配置的 Postgres、Redis、MinIO 可达且不会误报生产 ready。
- 真实 Beat/Worker 非空 Outbox smoke：通过。API 上传 Markdown 后即时投递第 1 次因缺 remote embedding key 回到 pending；两次强制到期事件均由 Beat claim 并交给 Worker，第 3 次进入 failed 死信，公开 `parse_error` 为受控文本；smoke 用户、DB 记录和 MinIO 对象随后清理。
- PostgreSQL 数据迁移 smoke：通过。在独立临时库从 `0008` 构造重复 active plan 与指向 loser 的 snapshot，升级 `0009` 后得到 `v1=replaced`、`v2=active`、snapshot id/version/JSON 均指向 v2；另验证 `head -> 0004 -> head` 可完整降级并重升级。
- `python -m alembic -c backend\alembic.ini upgrade head`：通过。

未完全通过或未能证明真实闭环：

- 真实 remote embedding/LLM/Brave key 未配置，readiness 会拒绝生产就绪；Postgres/pgvector deterministic 路径已有历史 smoke，remote provider 成功链路仍需外部 key 后专门验证。
- `2 moderate` 来自 Next.js 内嵌 PostCSS，当前 `npm audit --omit=dev --audit-level=high` 门禁通过，但仍需跟踪上游安全版本，禁止用 `npm audit fix --force` 降级 Next.js。
- 真实诊断和真实 multipart 文件上传尚未开始；稳定基线通过不能替代后续阶段验收。

## 2026-07-18 稳定基线补充

- 认证已从历史 `X-User-Id` 占位切换为 Bearer JWT `Principal`。Access Token 仅驻留前端内存，Refresh Token 位于 HttpOnly Cookie；`0011/0012` 提供用户认证字段、会话和刷新令牌哈希/轮换状态。
- 前端依赖升级为 Next.js `16.2.10`、React/React DOM `19.2.7`、ESLint `9.39.5`、Playwright `1.61.1`，并迁移到 ESLint flat config。
- 仓储卫生测试禁止跟踪生成物、`X-User-Id` router 回流、重复异常抛出和多 Alembic head。E2E/pytest 临时目录均按单次运行隔离并清理，失败时保留 E2E 日志。
- 独立验收证据见 `docs/engineering/stable-baseline-acceptance-2026-07-18.md`。

## 2026-07-11 反向审计补充

- 已修复现有用户可在 goal 创建入口缺少身份 header 时被修改的问题；前端 goal 创建同步发送所选用户身份。
- 已修复初次 broker 发布失败把 durable pending 上传误报为 503 的问题；周期 dispatcher 可继续重投。
- 已修复查询 embedding 不可用时 tutor 500 和工具审计误记 success 的问题；API 返回 `unavailable/failed`、空引用，数据库 ToolCall 记录 failed 原因。
- 已增加原始 HTTP 请求、解码后文件、PDF 页数、图片像素、累计提取字符和 chunk 数上限，避免 JSON/base64、压缩 PDF、OCR 和 embedding quota 资源耗尽。
- 已增加迁移 `20260711_0010`：回填历史 pending 文档 Outbox 事件并建立 due-event 调度索引；数据回填和 `0007/0009` 历史重复状态收敛均明确为前向不可逆。
- readiness 现在验证生产 provider mode，并用独立超时探测 PostgreSQL、Redis、MinIO；配置缺失和依赖不可达分别报告。
- 计划调整与 diagnosis 使用同一 goal 行作为锁根；SQL 锁顺序回归和真实 PostgreSQL 双提案并发 apply 均已通过，结果为一个 `200`、一个 `409`，active plan 只递增一个版本。
- Docker 前端只在 builder 阶段接收 `NEXT_PUBLIC_API_BASE_URL`，无效的容器运行时设置已删除并在 README 说明 build-time 语义。
- Playwright 增至 8 条并改为跨平台 Node 生命周期 runner：除空 Today 外，覆盖原子 onboarding 失败、身份切换清理、旧身份迟到响应隔离、refresh 排队和上传草稿竞态；runner 会拒绝已占用端口、验证所属子进程存活，并在 `SIGINT/SIGTERM/SIGHUP` 后清理完整进程组。

## 2026-07-13 反向审计补充

- 新增 `/api/onboarding/initialize`，goal、diagnosis、初始 plan/state 在同一事务内完成；curriculum seed 不再自行 commit，诊断失败会回滚 user/goal。
- `APP_ENV`、文档处理 mode 和 RAG backend 统一去空白并小写，避免 readiness 通过但运行时走错 provider。
- PostgreSQL undefined-table `ProgrammingError`（SQLSTATE `42P01`）与 SQLite 缺表都返回可执行迁移提示的 `503`。
- RAG 数据库语句失败在 savepoint 内降级，返回空引用和 `retrieval_database_error`，并持久化 failed `rag.retrieve` 工具审计。
- 并发重复邮箱提交在唯一约束输家回滚后统一转换为业务 `409`，不再泄露 500。
- 第二轮复审修复了空白 `APP_ENV` 绕过、非缺表数据库错误误报迁移、未知文档 owner、非邮箱 IntegrityError 误分类、冷启动 curriculum seed 竞争和 RAG backend 元数据分歧；SQLite 本地/测试引擎现强制外键。
- 前端用身份代次拒绝旧用户迟到响应，同类 mutation 使用同步 busy lock，强制 refresh 会串行排队；上传完成只在草稿未变化时清空。Compose 支持可选根 `.env` 与 shell 凭据覆盖，并同步参数化 PostgreSQL/MinIO 服务端凭据；E2E runner 在中断信号下等待并升级清理完整进程组，失败/身份测试也增加了有效行为断言。
- 截至 2026-07-13，当时验证为后端 `179 passed, 1 warning`、Chromium E2E `8 passed`，且安全依赖审计仍失败；该状态已被 2026-07-18 稳定基线取代。

截至 2026-07-13 当时的剩余重大决策（现均已完成）：

1. JWT access/refresh + `auth_sessions` + `Principal` 的会话协议和迁移策略。
2. Next.js 14 到安全版本的跨大版本升级；当前 npm 安全门禁不通过，因此在升级并全量复验前不能宣称生产正确或提交最终重构。

## 2026-07-01 重构后进度

本轮按 `learning_system_refactor_plan.md` 先执行低风险边界重构和已具备回归测试的 P0/P1 修复收口，保持现有 API 行为不变：

- 已创建 Python 3.11 `.venv` 并按 `pyproject.toml` 安装完整运行/开发依赖；`celery`、`redis`、`psycopg`、`mcp` 可导入。
- 已新增结构性回归测试 `tests/test_stage3_refactor_boundaries.py`，要求运行时代码不再导入 `backend.app.services.stage3`，且 `stage3.py` 只能作为兼容门面。
- 已将 `stage3.py` 的业务实现拆至 `backend/app/application/*_service.py`、`backend/app/infrastructure/persistence/repositories/*` 和 `backend/app/core/exceptions.py`。
- 已将 router、worker 和 `backend/app/services/learning.py` 改为依赖拆分后的 application/repository 模块。
- 已保持 P0 认证占位链路的行为：私有接口仍要求 `X-User-Id`，legacy body/query `user_id` 必须与 header 匹配，跨用户资源返回 `404`；`/api/tools/search-official-learning-sources` 也已纳入占位认证，避免生产 Brave/search 配额裸露。
- 已保留兼容导入 `backend.app.services.stage3`，供旧测试或外部调用逐步迁移。
- 已修复重复 email 创建 goal 的业务错误处理：不同 `user_id` 复用同一 email 时返回可读 `409`，不再泄露数据库唯一约束异常。
- 已修复前端测验提交空答案自动代答问题：空答案会作为空字符串提交，避免污染测评证据。
- 已修复 `today_tasks` 的核心日期语义：当天任务按 active plan 中的 `scheduled_date == date.today()` 选择，不再只固定取 `scheduled_day == 1`。
- 已补齐 MCP server startup smoke，覆盖 `create_mcp_server()` 实例化、服务名、工具名和参数 schema。
- 已补充 `README.md`、`scripts/test.ps1` 和 `scripts/dev-backend.ps1`，固定 Windows 本地 pytest 临时目录与推荐迁移/启动命令；`scripts/test.ps1` 现在会传播 `compileall`/`pytest` 失败退出码。
- 已修复 LLM Gateway 远程失败直抛问题：短暂 HTTP 失败会按 `LLM_MAX_RETRIES` 重试；仍失败或解析失败会进入 `degraded` fallback，并通过 `runtime_metadata.llm.mode`、`retry_count` 和脱敏错误类型暴露；空白 base/key/model 会被视为缺失或默认值，不会触发无效远程请求。Embedding backend/key/model 也会做同样的空白配置归一化。
- 已修复 Brave 官方来源搜索失败直抛问题：live provider HTTP/解析失败会转为 `503`，并记录 `failed` tool call 审计；空白 `OFFICIAL_SEARCH_PROVIDER` 会回到 URL template 默认值，空白 `BRAVE_SEARCH_API_KEY` 会按缺失处理且不会发起 live 请求。
- 已为前端 demo fallback 增加显式 `Demo mode` banner，避免无 `goalId` 时本地样例数据被误认为真实后端闭环。
- 已修复任务开始接口的重复点击事件污染：已有 active session 时不会重复写入 `task_started` 学习事件。
- 已修复后端空答评分语义：空白测验答案按 0 分处理，并记录 `answer_status=blank` 与 `unanswered` 标签。
- 已将 CORS origin 改为环境化配置：默认保留本地前端，`CORS_ALLOWED_ORIGINS` 可追加预览/部署域名。
- 已新增 `/api/health/ready` 生产 readiness 检查：`APP_ENV=production` 时会对数据库、Celery、MinIO、remote LLM、remote embedding 和 Brave live search 的关键配置缺失返回 `503` 与 `missing` 列表；默认 `tutor/tutor` 数据库凭据和 `minioadmin/minioadmin` MinIO 凭据同样会被拒绝。即使 `LLM_BASE_URL` 和 `LLM_API_KEY` 同时缺失，或关键环境变量只是空白字符串，也不会误报 ready；官方搜索和对象存储 builder 也已对空白 provider/key/backend/MinIO 配置做缺失归一化。
- 已推进 `WorkflowAction` 动作协议：Phase2 raw engine 只返回 assessment/plan/snapshot/audit/tool-call 持久化动作，backend application `_run_engine()` 统一执行这些动作。
- 已新增 `outbox_events` 迁移、claim-specific `dispatch_token`、Celery Beat dispatcher 和按 event id 幂等处理的 worker 入口；due/stale lease 会自动重投，发布失败只释放自己的 claim，重复 delivery 由数据库 CAS 排除，处理失败通过 savepoint 保留可提交的重试状态。
- 已补未提交对象的补偿删除：内部 object key 包含唯一 document id，inline/数据库事务失败时不会留下孤儿对象；数据库异常只记录受控公开错误，不把 SQL/参数写入 `parse_error`。
- 已补 active plan / active learning session 部分唯一索引及历史重复数据收敛迁移；重复 diagnosis 会替换旧 active plan，过期 proposal 返回 `409`，任务 start/complete 和 assessment submit 对重复或并发调用保持幂等。
- 已修复引擎失败审计与业务事务同回滚的问题：业务写入先回滚，随后单独提交只包含异常类型的 failed agent-run。
- 已将阶段测验纳入 planner 硬门控，并把缺失观察指标显式标为不可自动调整；前端对空 `today_tasks` 显示空态，不再因 `currentTask` 为空崩溃。
- 已新增 `documents.parse_error` 迁移和序列化字段；低质量 OCR、空 PDF、unsupported mime type 等解析失败会持久化并返回用户可读失败原因。
- 已将 Playwright E2E 改为跨平台 Node 进程生命周期 runner，避免嵌套 webServer 收尾卡住、端口串用和 Python 路径只适配 Windows；runner 还会处理 `SIGINT/SIGTERM` 并等待进程树退出。
- 当前已验证：`.\scripts\test.ps1`（包含 `compileall`，`179 passed, 1 warning`）、前端 `npm run test:ui-routes`、`npm run lint`、`npm run build`、Chromium E2E（`8 passed`）和 `docker compose config` 均通过；2026-07-11 Compose rebuild/start 是前一源码快照证据，最终安全依赖/JWT 修改后必须重建复验。生产 readiness 因弱默认数据库/MinIO 凭据及缺 remote LLM/embedding/Brave key 返回预期 `503`。Docker context 已排除 `.venv`、`frontend/node_modules`、`.next` 等本地产物；应用镜像以非 root 用户运行，三个后端服务复用同一镜像构建。

仍未完成或刻意延后：

- 截至 2026-07-01，JWT 会话仍未替换 `X-User-Id`；该历史缺口已于 2026-07-18 前完成。
- pgvector schema/index、PostgreSQL 检索分支和文档 Outbox 状态机已存在；Compose worker 与 Beat scheduler 分别处理任务和 due-event 重投，claim token、过期租约、数据库 CAS、savepoint、死信收敛和未提交对象补偿删除均有回归覆盖；Postgres/pgvector deterministic 成功入库与检索已验证。remote embedding 成功入库仍待外部 key，告警和人工重放界面仍未落地。
- LangGraph 持久化动作已升级为显式 `WorkflowAction` 协议；完全 session-free workflow、全局 Unit of Work 和跨服务失败回滚策略仍待做。
- `.tmp/` 是本地 pytest 临时目录，不属于项目产物，已加入 `.gitignore` 并由 `scripts/test.ps1` 自动使用。
- `frontend/.next/` 已加入 `.gitignore`，但仓库历史上已跟踪的 107 个构建产物仍在 Git 索引中；本轮不把它们混入功能重构提交，后续应独立清理。

## 项目完整性评估

| 模块 | 当前状态 | 完整性判断 |
|---|---|---|
| 01 产品、课程与入学诊断 | 有课程 seed、goal 创建、规则诊断、初始计划 | 原子 happy path 可用，但前端仍提交硬编码诊断；版本化模板、真实答题和幂等请求尚未实现 |
| 02 后端 API 与学习域服务 | FastAPI 路由、Bearer JWT Principal、状态/任务/测验/计划/资料接口存在 | 认证与跨用户隔离已收口；真实诊断和 multipart 上传仍是下一阶段 |
| 03 状态知识库与数据库 | ORM 模型和 12 个 Alembic 迁移存在，当前唯一 head 为 0012 | active plan/session、认证会话和 refresh 轮换状态存在；CI 已持续覆盖 PostgreSQL 升降级、扩展、关键表和索引 |
| 04 LangGraph 与 Agent 编排 | 使用真实 LangGraph，节点包含 load/retrieve/teacher/assessment/observer/planner/persist；业务与审计持久化通过 `WorkflowAction` 返回给 application 执行 | 图是真的；主要持久化边界已收窄，但 Agent 智能仍主要靠规则和离线 fallback，全局事务边界仍需补强 |
| 05 RAG 与文档入库 | Markdown/PDF 可解析入库，document_chunks 可被检索；Postgres 迁移含 pgvector column/index，文档 outbox 状态机已落地 | 轻量文本链路已可用；Compose 启动、MinIO/Redis/Celery unsupported parse 作业、Postgres/pgvector deterministic 成功入库与检索已验证；上传文件名已规范化，内部 object-key owner/filename 组件会编码，本地对象存储会拒绝绝对/逃逸 key，公开响应不暴露内部存储 key/hash；embedding unavailable/维度错误会先回到 pending 并记录 `parse_error`，partial embedding failure 不会留下半索引，未到 `available_at` 的 pending 事件不会被提前重试，event_type 错误、payload 无效或缺失 document 的坏 outbox 事件会即时 failed，超过尝试上限后进入 failed；remote embedding 成功入库、告警和重放仍未生产级闭合 |
| 06 测验、掌握度与计划调整 | 测验/作答/掌握度/计划调整可持久化，计划调整可 apply | 阶段测验已阻止未评分/未通过时 advance，重复提交与过期 proposal 已收口；评分与题目生成仍是确定性规则 |
| 07 前端学习体验 | 页面齐全，lint/build 通过，能调用后端 happy path；无 `goalId` 时会显示 Demo mode banner | 空任务已有真实空态；硬编码资源/节点数据仍会降低真实感，真实模式错误处理还需持续补强 |
| 08 MCP、LLM Gateway 与部署 | LLM Gateway wrapper、MCP server 文件、Compose 配置和 `/api/health/ready` 存在 | Compose 构建/启动已验证；readiness 可暴露生产关键配置缺失；远程 embedding/Brave key、限流/熔断和真实外部调用仍待补强 |

## 按项目优先度重排的问题清单

下面是本报告的主阅读顺序。优先级按“是否阻断真实多用户使用、是否阻断本地/部署运行、是否造成假闭环误判、是否影响学习效果可信度、是否影响体验成熟度”排序。后面的分类附录保留原始证据和细节。

### P0：必须先处理，否则不能进入真实多用户/真实运行

1. **跨用户 / 跨目标归属校验缺失（已修）**
   - 类别：明面 bug、安全与数据完整性。
   - 旧影响：attacker 可以混用自己的 `user_id` 和别人的 `goal_id` 访问 chat、assessment、replan 等写接口；测验提交只按 `assessment_id` 查对象，也可污染 owner 的学习状态。
   - 当前状态：私有入口统一从 Bearer JWT 解析 `Principal`，并使用 owned-resource 查询；跨用户资源返回 `404`，客户端 user 字段不作为权限来源。
   - 优先原因：这是从 demo 进入真实用户环境前的硬阻断。

2. **身份模型不统一**
   - 类别：潜在 bug、架构风险。
   - 旧影响：部分接口用 `X-User-Id`，大量写接口仍信任 body/query 里的 `user_id`。
   - 当前状态：JWT access/refresh、`auth_sessions`、`refresh_tokens` 和 `Principal` 已落地；Access Token 保持内存态，Refresh Token 使用 HttpOnly Cookie 与数据库哈希轮换。
   - 优先原因：它是跨用户漏洞的系统性根因，必须和归属校验一起修。

3. **本地 `.venv` 依赖与项目声明不一致（已修）**
   - 类别：明面 bug、运行环境断点。
   - 影响：旧状态下 `.venv` 缺少 `celery`、`redis`、`psycopg`、`mcp`，Postgres、Celery、MCP 路径会直接失败。
   - 当前状态：已按 `pyproject.toml` 安装依赖并有导入测试覆盖；Compose build/start 也验证了 backend、worker 与 MCP 依赖可安装。

4. **fresh DB 不迁移时 API 直接 500**
   - 类别：明面 bug、启动闭环断点。
   - 影响：新 SQLite DB 直接调用 `/api/goals` 会 `no such table: users`。
   - 优先原因：新环境启动必须有明确迁移入口或可读错误，否则真实使用第一步就断。

5. **裸 `alembic.exe` 在 Windows 本地失败**
   - 类别：明面 bug、开发者体验/部署脚本风险。
   - 影响：`.venv\Scripts\alembic.exe -c backend\alembic.ini upgrade head` 找不到 `backend`；`python -m alembic` 或设置 `PYTHONPATH` 才能跑。
   - 优先原因：迁移命令不稳定会放大数据库启动问题。

6. **Celery 文档处理路径在当前环境不可用**
   - 类别：明面 bug、运行环境断点。
   - 影响：`.env.example` 默认 `DOCUMENT_PROCESSING_MODE=celery`，但本地缺 `celery`；文档上传会直接 `ModuleNotFoundError`。
   - 优先原因：资料入库是 RAG 闭环入口，不能默认指向不可用模式。

### P1：真实闭环 blockers，看起来能跑但关键链路不真实

7. **假 RAG 闭环（已修）**
   - 类别：假闭环。
   - 影响：旧实现无文档时会返回 `_default_citations()`，让回答看起来有真实引用。
   - 当前状态：当前 `SQLAlchemyRagRepository.retrieve()` 无成功入库 chunk 时返回 `[]`，API `runtime_metadata.rag.fallback_citations` 为 `False`，已有回归测试覆盖。

8. **假官方联网搜索 + 前端默认搜索会 400（部分已修）**
   - 类别：明面 bug + 假闭环。
   - 影响：旧实现前端默认带 `platform.openai.com` 时会因白名单缺失返回 400；URL template 模式不真实联网。
- 当前状态：白名单已包含 `platform.openai.com`，Brave live provider 成功和失败路径均有测试；失败会返回 `503` 并记录 failed tool call；空白 provider/key 会按默认/缺失处理，避免误触发无效 live 请求；URL template fallback 仍明确标记 `retrieval_mode=url_template`、`is_live_search=false`。

9. **假 LLM 闭环（部分已修）**
   - 类别：假闭环。
   - 旧影响：`LLM_API_KEY` 为空时静默进入 `_offline_complete()`，用户不知道当前不是远程模型回答。
- 当前状态：API 已通过 `runtime_metadata.llm.mode` 暴露 `offline`/`remote`/`degraded`，远程 HTTP/解析失败会重试后降级并记录脱敏错误类型；空白 remote base/key/model 配置会按缺失或默认值处理，不会触发无效请求；前端已显示 LLM mode，但生产级限流、熔断和真实外部 key smoke 仍待补强。

10. **假向量检索 / 假 pgvector（部分已修）**
    - 类别：假闭环、架构差距。
    - 影响：旧实现 embedding 是 hash 字节数组，列类型是 JSON，检索在 Python 内存排序，不是 pgvector。
    - 当前状态：Postgres 迁移已创建 `embedding_vector vector(1536)` 和 ivfflat 索引，repository 在 PostgreSQL + `RAG_RETRIEVAL_BACKEND=pgvector` 下使用 `<=>`；Compose Postgres healthy 启动和 deterministic pgvector 成功入库/检索 smoke 已验证。

11. **假 MinIO / 对象存储闭环（部分已修）**
    - 类别：假闭环、运行架构差距。
    - 影响：旧实现 Compose 有 MinIO、DB 有 `object_key`，但代码没有真实上传/下载对象。
- 当前状态：当前有 `DocumentObjectStorage`、本地对象存储和 MinIO client，上传先规范化 filename，并编码内部 object-key owner/filename 组件后再写 object storage；本地对象存储会拒绝绝对/逃逸 key，对象存储 builder 会把空白 backend/MinIO 配置按默认或缺失处理，公开 API 不返回内部 `object_key`/`sha256`，defer/celery 会创建 `outbox_events`，worker 可按 event id 幂等恢复；Compose MinIO/Redis/Celery worker 作业 smoke 已验证 unsupported parse 路径；embedding unavailable/remote embedding HTTP 失败会统一进入 pending + `parse_error`，remote embedding 成功入库仍待外部 key 验证。

12. **假 OCR 闭环 / 图片上传语义不一致（部分已修）**
    - 类别：明面 bug + 假闭环。
    - 影响：旧实现文档承诺图片 OCR；实际图片上传 HTTP 201，但 `parse_status=failed`，`NoopOCRClient` 没进入上传解析路径。
    - 当前状态：当前图片解析路径会调用 OCR client 并写入 `source_type=image_ocr` chunks；低质量 OCR/空内容会写入并返回 `parse_error`，已有 fake OCR 成功与失败回归测试；backend 容器已验证 Tesseract 5.5.0 与 `eng`/`chi_sim` 语言包。

13. **SQLite 测试与 Postgres 真实行为可能不同**
    - 类别：潜在 bug、验证缺口。
    - 影响：当前多数测试用 SQLite 和 `create_all()`；Postgres、JSON、外键、并发、迁移行为未被充分覆盖。
    - 优先原因：真实部署目标是 Postgres/pgvector，测试层不能只证明 SQLite demo。

14. **资料上传没有真实对象存储恢复能力（部分已修）**
    - 类别：潜在 bug、数据链路风险。
    - 影响：旧实现 defer 模式只登记 DB；如果没有对象存储保存原文，worker 后续无法取回内容。
    - 当前状态：defer/worker 可从 object storage 恢复原文；Celery Beat 会扫描 due/stale lease，claim token 防止旧发布者释放新租约，数据库 CAS 防重复处理，savepoint 可在 chunk 写入失败后保留外层重试事务，未提交上传会补偿删除唯一对象；重复失败会收敛到 failed 死信。仍缺 remote embedding 成功入库、告警和人工重放界面。

### P2：学习系统可信度与业务正确性问题

15. **假测验智能闭环**
    - 类别：假闭环、学习效果风险。
    - 影响：题目模板固定，评分按关键词和长度，不是真实题库/LLM rubric/任务证据驱动。
    - 优先原因：测验质量直接决定掌握度和计划调整是否可信。

16. **前端会自动填充测验答案（已修）**
    - 类别：潜在 bug、学习证据污染。
    - 影响：用户空答案曾会被替换成固定句子，空答看起来像有效作答。
    - 当前状态：2026-07-01 已改为空答案按空字符串提交，并新增前端契约回归测试；后端空白答案按 0 分和 `unanswered` 证据处理。

17. **假计划调整闭环（部分已修）**
    - 类别：假闭环、业务逻辑风险。
    - 影响：缺失数据仍以 0.8/0 归一化供规则计算，但会显式标记 `automatic_adjustment_allowed=false`；阶段测验未评分时会阻止 advance，已评分且要求 review 时会触发 remediate。
    - 优先原因：计划调整是学习闭环的核心，不能靠默认值制造稳定假象。

18. **服务层 commit 分散，失败时可能部分写入（部分已修）**
    - 类别：潜在 bug、数据一致性风险。
    - 影响：多个服务函数内部直接 commit，外部依赖或引擎失败时可能留下半完成记录。
    - 当前状态：Phase2 raw engine 不再直接保存 assessment/plan/snapshot/audit/tool-call，而是返回 `WorkflowAction`；application 层统一执行这些动作。引擎失败会回滚业务写入并单独提交脱敏 failed agent-run；文档处理用 savepoint 保留 outbox 重试事务。全局 Unit of Work 和所有跨服务事务边界仍待做。

19. **计划版本和任务会话存在并发风险（已修核心不变量）**
    - 类别：潜在 bug、并发一致性风险。
    - 影响：`_next_plan_version()` 用 max+1；`start_task()` 原本查再建 active session 且重复点击会重复写事件。
    - 当前状态：ORM 与 Alembic 均包含 plan version、单 active plan 和单 active task session 约束；goal/snapshot 行锁串行化版本变更，过期 proposal 返回 `409`。重复 task start/complete、完成后再次 start、重复 assessment submit 和重复 diagnosis 均有回归覆盖。

20. **重复邮箱创建用户未做业务级错误处理（已修）**
    - 类别：明面 bug、API 可用性。
    - 影响：相同 email、不同 user_id 曾会触发数据库唯一约束异常，未转成 409/400。
    - 当前状态：2026-07-01 已在 goal 创建路径前置检查 email 冲突并返回 `409 email already exists`，新增回归测试。

21. **LLM Gateway 缺少生产级错误处理（部分已修）**
    - 类别：潜在 bug、稳定性风险。
    - 影响：模型调用失败曾向上抛，没有统一错误转换、重试、降级标记、限流和脱敏。
    - 当前状态：2026-07-02 已补短暂 HTTP 失败重试和 `retry_count` metadata；远程 HTTP/解析失败会转为 `degraded` fallback，并记录脱敏 `error_type`；限流、熔断仍待生产增强。

22. **`today_tasks` 不是真正的“今天”（部分已修）**
    - 类别：潜在 bug、产品语义风险。
    - 影响：原实现主要按 `scheduled_day == 1`，没有结合真实日期、计划起始日、时区和用户日程。
    - 当前状态：2026-07-01 已改为 active plan 中 `scheduled_date == date.today()`，新增回归测试；用户时区、计划起点跨时区和日程设置仍待做。

23. **假长期记忆闭环**
    - 类别：假闭环、产品承诺差距。
    - 影响：`memory_gate` 只返回空数组，前端也没有真实长期记忆查看/删除/审批链路。
    - 优先原因：如果产品宣称长期记忆，就必须有用户控制和审计。

### P3：体验、配置与成熟度问题

24. **假前端完整体验**
    - 类别：假闭环、产品体验风险。
    - 影响：无 `goalId` 时聊天、测验、计划调整、任务状态都走本地 demo；学习路径、资源列表、视频/文档详情大量硬编码。
    - 当前状态：2026-07-02 已新增显式 `Demo mode` banner，并用前端源码契约测试防止隐性回退；硬编码资源/节点数据仍待后续真实化。
    - 优先原因：会掩盖后端失败，但不如 P0/P1 那样直接阻断真实系统。

25. **CORS 只适合本地开发（已修）**
    - 类别：潜在 bug、部署配置。
    - 影响：旧实现只允许 `127.0.0.1:3000` 和 `localhost:3000`，真实域名需环境化。
    - 当前状态：`CORS_ALLOWED_ORIGINS` 可追加预览/部署域名，默认本地域名保留；`.env.example` 和 README 已补说明。

## 分类附录：明面上的 Bug

### 1. 跨用户 / 跨目标归属校验缺失（占位认证版已修）

受影响位置：

- `backend/app/routers/tutor.py`
- `backend/app/routers/assessments.py`
- `backend/app/routers/plans.py`
- `backend/app/routers/state.py`
- `backend/app/routers/tasks.py`
- `backend/app/routers/documents.py`
- `backend/app/routers/tools.py`
- `backend/app/application/*_service.py`
- `backend/app/services/learning.py`

旧现象：

- `POST /api/tutor/chat` 可以传入 attacker 的 `user_id` 和 owner 的 `goal_id`，接口仍返回 200。
- `POST /api/assessments` 可以用 attacker 的 `user_id` 给 owner 的 `goal_id` 创建测验，接口返回 201。
- `POST /api/assessments/{assessment_id}/submit` 只按 `assessment_id` 查测验，不校验测验归属；attacker 可以提交 owner 的测验，接口返回 200。
- `POST /api/plans/replan` 可以用 attacker 的 `user_id` 给 owner 的 `goal_id` 生成计划调整，接口返回 200。
- `GET /api/tasks/today?goal_id=<owner_goal>` 曾会在 attacker header 下返回 200 空列表，而不是私有资源 `404`。
- `POST /api/tools/search-official-learning-sources` 曾无需身份即可消耗官方搜索能力。

旧根因：

- 多数写接口信任请求体里的 `user_id`。
- 服务层没有统一校验 `LearningGoal.user_id == user_id`。
- 数据库外键只分别指向 `users.id` 和 `learning_goals.id`，没有复合约束阻止“存在的 user + 别人的 goal”组合。

当前状态：

- 2026-07-05 的占位边界已进一步升级为 Bearer JWT `Principal`；body/query `user_id` 不再是权限来源。
- chat、assessment create/phase/submit、diagnosis、replan/apply、task start/complete、state/current、tasks/today、documents upload/list 和 official-source tools 均已有回归覆盖。
- 跨用户资源统一返回 `404`；Access Token 内存态、HttpOnly Refresh Cookie、`auth_sessions`/`refresh_tokens` 和 refresh 轮换/重用检测已有测试覆盖。

### 2. 前端官方来源搜索默认会触发 400（已修）

受影响位置：

- `frontend/components/learning-provider.tsx`
- `backend/app/services/official_sources.py`

现象：

- 前端默认请求域名包含 `platform.openai.com`。
- 旧实现后端白名单没有 `platform.openai.com`。
- 旧实测 `POST /api/tools/search-official-learning-sources` 返回 `400 {"detail":"domain not whitelisted: platform.openai.com"}`。
- 当前后端白名单已包含 `platform.openai.com`，并有回归测试覆盖前端默认域名。

影响：

- 设置/资料区的“搜索官方来源”按钮不再因默认 OpenAI 域名失败。
- URL template fallback 和 Brave live provider 已通过 `retrieval_mode`/`is_live_search` 区分；Brave 失败路径已转为 503 并记录审计。

### 3. fresh DB 不迁移时 API 直接 500

受影响位置：

- `backend/app/db.py`
- `backend/app/main.py`
- `backend/alembic/env.py`
- `docker-compose.yml`

现象：

- 使用新的 SQLite URL，不先跑 Alembic，直接调用 `/api/goals` 会报 `no such table: users`。
- Docker Compose backend command 会先 `alembic upgrade head`，但本地直接 `uvicorn backend.app.main:app` 没有启动前迁移或明确错误提示。

影响：

- 新开发者或本地运行时很容易遇到 500。
- 测试里大量 `Base.metadata.create_all()` 会让这个问题不明显。

### 4. 本地 `.venv` 依赖与项目声明不一致

受影响位置：

- `pyproject.toml`
- `.env.example`
- `backend/app/worker.py`
- `backend/app/mcp_server.py`
- `backend/app/db.py`

现象：

- `pyproject.toml` 声明了 `celery[redis]`、`mcp[cli]`、`psycopg[binary]`。
- 当前 `.venv` 已安装这些依赖，`test_runtime_optional_dependencies_are_installed` 覆盖导入。
- `DATABASE_URL=postgresql+psycopg://...` 的依赖已随 `psycopg[binary]` 安装。
- `DOCUMENT_PROCESSING_MODE=celery` 的 import failure 路径已有 `503` 回归测试，Compose worker 已连接 Redis 并 ready。
- `create_mcp_server()` 已有 startup smoke 覆盖服务名、工具名和参数 schema。

影响：

- 依赖缺失本身已不是当前阻塞。
- `.env.example` 的生产外部 key 仍为空，readiness 会返回 `503`；真实 Celery 作业、MinIO 写读和 pgvector deterministic 检索 smoke 已完成，remote LLM/embedding/Brave 仍需外部 key 后独立 smoke。

### 5. 裸 `alembic.exe` 在 Windows 本地失败

受影响位置：

- `backend/alembic/env.py`
- 本地运行方式

现象：

- `.venv\Scripts\alembic.exe -c backend\alembic.ini upgrade head` 失败：找不到 `backend`。
- `.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head` 成功。
- 显式设置 `PYTHONPATH='.;src'` 后裸 `alembic.exe` 成功。

影响：

- 文档或脚本如果建议直接运行 `alembic.exe`，Windows 用户会遇到迁移失败。

### 6. 重复邮箱创建用户未做业务级错误处理（已修）

受影响位置：

- `backend/app/services/learning.py`
- `backend/app/routers/goals.py`

现象：

- 用不同 `user_id` 但相同 `email` 创建 goal，会触发数据库唯一约束异常。
- 2026-07-01 已改为创建 `User` 前按 email 查询冲突；不同 `user_id` 复用同一 email 会返回 `409 email already exists`，避免请求级 500 和数据库约束泄露。

影响：

- API 客户端现在可以区分“邮箱已存在”和系统故障。
- 仍待后续身份模型升级统一注册/登录语义，避免目标创建承担隐式建用户职责。

### 7. 图片 OCR 与接口返回语义不一致（部分已修）

受影响位置：

- `docs/project_division/05_rag_document_ingestion.md`
- `backend/app/services/stage3.py`
- `backend/app/routers/documents.py`

现象：

- 分工书要求图片 OCR 文本提取。
- 当前图片解析路径会调用 OCR client，成功后写入 searchable chunks，metadata `source_type=image_ocr`。
- 低质量 OCR、空 PDF 和 unsupported mime type 会写入并返回 `parse_error`；backend 容器已验证 Tesseract 5.5.0 与 `eng`/`chi_sim` 语言包。

影响：

- 前端/用户看到“上传成功”，实际不能检索。
- 文档承诺与代码行为不一致。

## 分类附录：潜在 Bug 与风险

### 1. 身份模型不统一

- `/api/state/current` 使用 `X-User-Id`。
- `/api/goals`、`/api/onboarding/diagnosis`、`/api/tutor/chat`、`/api/assessments`、`/api/plans/replan`、`/api/documents/upload` 多数使用 body/query 中的 `user_id`。
- 这会导致权限、审计和前端调用方式长期不一致。

### 2. 计划版本和任务会话存在并发风险（部分已修）

- `_next_plan_version()` 仍以 max version + 1 计算，但 diagnosis 持有 goal 行锁、plan adjustment 持有 snapshot 行锁，数据库另有 `(user_id, goal_id, version)` 唯一约束。
- `learning_plans` 对 active user/goal、`learning_sessions` 对 active user/task 使用部分唯一索引；迁移会先收敛历史重复 active 数据，`0009` 同时把 snapshot 的 active plan id/version/`generated_from.active_plan_id` 指向 winner。
- `start_task()`、`complete_task()`、assessment submit 和 plan adjustment apply 使用幂等返回或条件更新，重复/过期请求不会重复写学习证据。

### 3. SQLite 测试与 Postgres 真实行为仍可能不同

- 当前主测试多为 SQLite，但 `0009` 重复 active plan 数据修复已在 SQLite 回归和独立 PostgreSQL 临时库中同时验证。
- SQLite 默认外键行为、JSON 行为、并发行为与 Postgres 不一致。
- `Base.metadata.create_all()` 测试路径仍可能掩盖 Alembic 与 ORM 的差异。

### 4. 服务层 commit 分散，失败时状态可能部分写入（部分已修）

- 多个服务函数内部直接 `session.commit()`。
- 部分路径先写审计/事件/状态，再跑引擎或外部依赖。
- 一旦外部 API、Celery、LLM 或 DB 抛错，可能出现半完成记录。
- 2026-07-01 已将 Phase2 engine 的 assessment/plan/snapshot/audit/tool-call 写入改为 `WorkflowAction`，由 backend application `_run_engine()` 统一执行；raw engine 单测已验证不直接写 repository 或 audit sink。
- 2026-07-10 已补引擎失败业务回滚 + 独立脱敏失败审计，以及文档 chunk savepoint；这覆盖已识别的高风险失败路径，但不是完整全局 Unit of Work。
- 仍需全局 Unit of Work 和跨服务错误回滚策略。

### 5. LLM Gateway 缺少生产级错误处理（部分已修）

- 外部模型短暂 HTTP 失败会按 `LLM_MAX_RETRIES` 重试；最终 HTTP/解析失败已转为 `degraded` fallback，路由层可通过 `runtime_metadata.llm.mode` 识别。
- 当前 metadata 记录 `error_type`、`reason`、`retry_count`、`base_url` 和模型名，不包含 API key 或完整异常文本。
- 仍缺少限流、熔断和更细的降级策略。
- `LLM_API_KEY` 为空或空白时会进入 `offline`，调用方可通过 `runtime_metadata.llm.mode` 识别；空白 `LLM_MODEL` 会使用默认模型名。

### 6. `today_tasks` 不是真正的“今天”（部分已修）

- 2026-07-01 已将 `get_today_tasks()` 改为按 active plan 的 `scheduled_date == date.today()` 过滤，避免每天固定返回第 1 天任务。
- 真实系统里仍应该结合用户时区、计划起始日跨时区、任务状态和用户日程。

### 7. 前端会自动填充测验答案（已修）

- 2026-07-01 已改为空答案按空字符串提交，不再用固定句子替代空答案。
- 后端 scoring 已显式识别未答/空答：空白答案为 0 分，证据包含 `answer_status=blank` 和 `unanswered`。

### 8. 资料上传没有真实对象存储闭环（部分已修）

- 当前上传会先规范化 filename，并编码内部 object-key 的 owner/filename 组件，再通过 `DocumentObjectStorage.put_bytes()` 写入对象存储、保存内部 `Document.object_key`；公开文档响应不返回内部 object key 或 sha256。
- Celery 模式传递 `outbox_events.id`，worker 可通过 event payload 和 object storage 恢复原文，不再把大文件 base64 放进任务消息。
- Compose smoke 已通过 MinIO/Redis/worker 的 unsupported mime 对象写入、读取与 Celery 执行；当前代码另完成非空 pending 事件的 Beat 到期重投和第 3 次失败死信 smoke，并有 claim token、租约、CAS、savepoint 回归。当前 scheduler/worker 容器级 rebuild 和周期派发均已取得证据。
- 2026-07-10 已补 Beat due-event dispatcher、claim-specific token、过期 lease、发布失败释放、worker CAS、chunk savepoint 和未提交对象补偿删除；对象存储失败恢复的剩余范围是外部存储灾难恢复、告警和人工操作界面。

### 9. CORS 只适合本地开发（已修）

- `backend/app/main.py` 默认允许 `127.0.0.1:3000` 和 `localhost:3000`。
- `CORS_ALLOWED_ORIGINS` 可追加真实部署域名、预览环境或移动端 WebView origin。

## 分类附录：假闭环：看起来完成但不是真实情况

### 1. 假 RAG 闭环（已修）

旧行为：

- 没有任何成功入库文档时，`SQLAlchemyRagRepository.retrieve()` 曾会返回 `_default_citations()`。
- 返回内容看起来像可信引用：`AI App Dev V1 - RAG Foundations`，并带 LangChain URL。

当前状态：

- 当前实现无 chunk 时返回空列表，不再伪造引用。
- `tests/test_document_ingestion_worker.py::test_rag_retrieve_returns_no_citations_when_corpus_has_no_chunks` 和 API workflow 已覆盖无引用、无 fallback citation 行为。

建议：

- 无检索结果时应明确返回“无可用资料”，或在响应里标记 `source_type=fallback_demo`。
- 生产路径不要用默认 citation 冒充检索结果。

### 2. 假官方联网搜索（部分已修）

当前行为：

- `search_official_learning_sources()` 支持 `OFFICIAL_SEARCH_PROVIDER=brave` 的真实联网搜索，并过滤到白名单域名；Brave HTTP/解析失败会转为统一不可用错误并审计失败，空白 provider/key 不会误触发 live 请求。
- 默认/无 key 路径仍可走 URL template fallback，它会明确返回 `retrieval_mode=url_template` 和 `is_live_search=false`。

问题：

- URL template fallback 没有检索网页、没有校验页面存在、没有真实摘要。

建议：

- 生产环境应使用 Brave live provider 或其他真实搜索 provider，并在前端继续展示 `retrieval_mode`/`is_live_search`。

### 3. 假 LLM 闭环（部分已修）

当前行为：

- `LLM_API_KEY` 为空或空白时，`LLMGatewayClient` 会返回离线模板回答，并把 `mode=offline` 写入 metadata。
- 远程 HTTP/解析失败会按配置重试，仍失败时进入 `mode=degraded` fallback，并记录脱敏错误类型。

问题：

- 离线模板回答仍不是远程模型能力，生产可信度依赖真实 key、限流、熔断和外部调用 smoke。

建议：

- 保持前端展示 `runtime_metadata.llm.mode`，并在生产环境继续补限流、熔断和真实远程调用 smoke。

### 4. 假向量检索 / 假 pgvector（部分已修）

当前行为：

- embedding 是 `sha256(text.lower())` 的 16 维字节数组。
- `document_chunks.embedding` 是 JSON，不是 pgvector column。
- 检索是 Python 内存排序，不是数据库向量索引。
- 2026-07-01 复核确认迁移 `20260626_0004` 会在 Postgres 上创建 `embedding_vector vector(1536)` 和 ivfflat 索引，repository 在 PostgreSQL + pgvector 配置下使用 `<=>` 排序。2026-07-05 已修复 ORM 将 `embedding_vector` 作为 `VARCHAR` 绑定导致 Postgres 拒绝写入的问题，模型在 PostgreSQL dialect 下会编译为 `CAST(... AS vector(1536))`，并通过真实 Postgres/pgvector deterministic 文档入库与检索 smoke。

问题：

- “向量检索可用”只在形式上成立，语义相关性不可靠。
- SQLite/local 路径仍不能代表真实 remote embedding 语义效果；真实 Postgres/pgvector 容器 deterministic smoke 已跑通，但 remote embedding 成功入库仍待外部 key 后验证。

建议：

- 使用真实 embedding provider 或本地 embedding 模型。
- 继续补 remote embedding 成功入库 smoke。

### 5. 假 MinIO / 对象存储闭环（部分已修）

当前行为：

- Compose 有 MinIO。
- `Document.object_key` 有值。
- 代码已有 `LocalDocumentObjectStorage` 与 `MinioDocumentObjectStorage`，上传会写对象存储，本地对象存储会拒绝绝对/逃逸 key，空白 backend/MinIO 配置会按默认或缺失处理，worker 会按内部 `object_key` 读取，API list 不暴露该字段。

问题：

- Compose MinIO 容器启动已验证；unsupported mime 文档经 API 上传、worker 从对象存储恢复后失败解析，证明写入/读取基础链路可用。
- Outbox 状态机、Beat dispatcher、claim token、租约重投、worker CAS、savepoint 与死信均已落地；真实 MinIO/broker 作业已有失败解析 smoke，embedding unavailable/remote HTTP 失败已有 pending + `parse_error` 回归。remote embedding 成功入库、监控告警和人工重放界面尚未闭合。

建议：

- 继续补 remote embedding 成功入库 smoke。
- 继续补监控告警、人工重放和外部对象存储灾难恢复；自动重投与未提交对象补偿删除已完成。

### 6. 假 OCR 闭环（部分已修）

当前行为：

- 当前生产路径使用 `TesseractOCRClient`，图片上传路径会调用 OCR client；空白 `OCR_BACKEND` 会回到默认 tesseract，空白 `TESSERACT_LANG` 会回到 `eng+chi_sim`，避免配置存在但不可用。
- 已有测试用 fake OCR 覆盖图片内容进入 searchable chunks。
- 低质量图片/OCR 无文本会记录 `parse_error` 并返回给 API/list 调用方；backend 容器已验证 Tesseract 5.5.0 与 `eng`/`chi_sim` 语言包。

问题：

- 分工书承诺图片 OCR；当前路径已接入，容器 OCR 二进制和语言包已验证，但真实图片端到端 OCR + embedding 入库仍需在 remote embedding 可用后补 smoke。

建议：

- 真实 Tesseract 二进制和语言包 smoke 已完成；用户可读失败原因已通过 `documents.parse_error` 与回归测试覆盖常见解析失败。

### 7. 假测验智能闭环

当前行为：

- 题目模板固定：`Explain key idea ...`。
- 评分基于关键词和长度。
- 前端空答案会被替换成固定答案。

问题：

- 这更像 deterministic demo rubric，不是能评估真实理解的验收系统。

建议：

- 题目来源应与知识点、资料片段、任务证据绑定。
- **已完成**：空答案按空答案评分，不再前端代答；后端空答证据含 `answer_status=blank` 和 `unanswered`。

### 8. 假计划调整闭环

当前行为：

- 缺失数据默认：completion/correctness 默认 0.8，mastery_delta 默认 0。
- 无真实学习证据时也能生成 `keep` 调整。
- 阶段测验状态会写入，但 `decide_observer_action()` 主要只看 completion/correctness/mastery_delta。
- 2026-07-10 已把阶段测验纳入硬门控：未评分时禁止 advance，评分后要求 review 时触发 remediate；缺失指标会标记为不可自动调整。

问题：

- “计划调整已生成”不一定代表系统真的理解学习状态。
- 题目与评分仍是 deterministic 规则，因此门控输入的学习效果可信度仍有限。

建议：

- 缺失数据应阻止自动调整或降级为“需人工确认”。
- phase assessment 硬规则已完成；继续提升题目与评分证据质量。

### 9. 假长期记忆闭环

当前行为：

- `memory_gate` 节点存在。
- 实现里 `approved_memories = []`。
- 前端设置页提到数据/长期记忆，但没有真实查看、删除、审批链路。

问题：

- 架构上有 memory gate，产品上像有长期记忆，但实际没有长期记忆闭环。

建议：

- 明确 V1 不做长期记忆，或补齐候选记忆、审批、删除、审计表。

### 10. 假前端完整体验

当前行为：

- 无 `goalId` 时，聊天、测验、计划调整、任务状态都能本地 demo fallback；当前 UI 会显示 `Demo mode` banner。
- 学习路径、资源列表、视频/文档详情大量硬编码。

问题：

- 页面看起来很完整，但很多操作没有真实后端状态来源。

建议：

- demo fallback 已有明显 UI 标识；继续减少硬编码资源/节点数据。
- 真实模式下后端失败不能被 demo 数据覆盖。

## 修复路线图

### P0：先封住真实多用户风险

1. **已完成**：私有接口统一从 Bearer JWT `Principal` 取当前 user，body/query 中的 legacy `user_id` 不再作为权限来源。
2. **已完成**：chat、assessment create/phase/submit、diagnosis、replan、plan adjustment apply、task start/complete、state/current、tasks/today、document upload/list 和 official-source tools 等入口已校验当前 user 与资源归属。
3. **已完成**：跨用户读写统一返回 `404` 或 legacy mismatch `400`，避免泄露私有资源存在性。
4. **已完成**：已有回归测试覆盖 chat、assessment create/submit、phase assessment、diagnosis、replan、plan adjustment、task、state read、document list/upload 和 official-source tools。
5. **已完成**：JWT access/refresh、`Principal`、`auth_sessions`/`refresh_tokens` 与前端内存 Access Token、HttpOnly Refresh Cookie 会话管理已同步落地。

### P0：修复运行环境闭环

1. **已完成（本机）**：已用 Python 3.11 创建 `.venv` 并安装 `pyproject.toml` + dev 依赖，确认可跑完整测试。
2. **已完成**：已有测试覆盖裸 `alembic.exe -c backend/alembic.ini current` 可导入 backend。
3. **已完成**：fresh DB 未迁移会返回包含 `alembic upgrade head` 的可读 `503`。
4. **已完成**：补充 `README.md`、`scripts/test.ps1` 和 `scripts/dev-backend.ps1`，固定推荐命令和本地临时目录，避免 pytest 再落到无权限的系统 Temp；`scripts/test.ps1` 已补退出码传播契约，避免失败测试被调用方误判为通过。
5. **已完成**：`/api/health/ready` 会在 `APP_ENV=production` 下检查数据库、Celery、MinIO、remote LLM、remote embedding 和 Brave live search 必需配置，缺失时返回 `503` 与 `missing` 列表；remote LLM base/key 同时缺失、关键变量为空白字符串都有回归测试覆盖。

### P1：把假闭环显式标成 demo 或接成真实实现

1. **已完成**：RAG 无结果时返回空 citation，不再返回默认假引用。
2. **已完成**：LLM offline mode 已透出到 API `runtime_metadata.llm.mode`。
3. **部分完成**：官方来源搜索默认域名已修，URL template 模式明确带 `retrieval_mode`/`is_live_search=false`；Brave live provider 成功和失败路径已有回归测试，但真实联网仍依赖外部 key。
4. **部分完成**：文档可写入对象存储并由 worker 恢复；Outbox 的 Beat dispatcher、claim token、过期租约、发布失败释放、worker CAS、savepoint、死信和未提交对象补偿删除已落地。remote embedding 成功入库、监控告警和人工重放仍待做。
5. **部分完成**：图片 OCR 路径可通过 OCR client 进入 searchable chunks；低质量 OCR/空内容会通过 `parse_error` 返回用户可读失败原因；backend 容器 Tesseract 与 `eng`/`chi_sim` 语言包 smoke 已完成，真实图片端到端 OCR + embedding 入库仍待 remote embedding 可用后验证。

### P1：补齐真实测试

1. **部分完成**：已有 Alembic migrated SQLite workflow、migration/ORM 约束一致性测试、active plan/session 并发不变量、Compose Postgres healthy 启动和真实 Postgres/pgvector deterministic 成功入库/检索 smoke；PostgreSQL 另已验证 `0008 -> 0009` 历史脏数据修复、`head -> 0004 -> head` 迁移循环，以及同一旧计划两个 remediation 并发 apply 只有一个成功。remote embedding 成功入库与持续集成中的 Postgres 压力测试仍待做。
2. **部分完成**：Celery 发布失败、周期重投、过期租约、重复 delivery、数据库 savepoint、embedding unavailable 和死信收敛均有回归；Compose worker 已完成 unsupported parse 作业和真实非空 Beat 重投至第 3 次死信 smoke。remote embedding 成功作业和生产监控仍待做。
3. **已完成**：MCP server startup smoke 已覆盖 `create_mcp_server()` 实例化、服务名、工具名和参数 schema。
4. **已完成**：已有前端默认 OpenAI 官方域名白名单测试。
5. **已完成**：已有 fresh DB 未迁移返回可读 `503` 测试。
6. **已完成**：Playwright 级别前端到后端真实工作流测试已覆盖创建学习路径时的真实后端 API 调用。
7. **已完成**：新增 stage3 边界重构结构测试，防止业务实现回流到兼容门面。

### P2：提高学习系统可信度

1. 用真实题库/LLM rubric/人工规则混合生成测验。
2. 掌握度更新记录更多证据：任务完成质量、答题时间、错因、复习间隔。
3. **部分完成**：计划调整保持 proposed/apply 两步确认，过期 proposal 与重复 apply 返回 `409`，单 active plan 有数据库约束；显式拒绝/回滚历史版本界面仍待做。
4. `today_tasks` 按真实日期/时区/计划起点计算。
   - **部分完成**：后端已按 active plan 的 `scheduled_date == date.today()` 返回当天任务，并新增回归测试；用户时区、计划起点跨时区和日程设置仍待做。
5. **部分完成**：CORS、API base、白名单域名已环境化/配置化；生产 readiness 已覆盖数据库、Celery、MinIO、remote LLM、remote embedding 和 Brave live search 的关键配置，且 remote LLM base/key 全缺失不会误报 ready；模型限流/熔断等运行策略仍待补强。
6. **已完成**：重复 email 创建用户/目标返回 `409 email already exists`。
7. **已完成**：前端提交测验不再为空答案自动填充固定答案。
8. **已完成**：后端空白测验答案按 0 分和 `unanswered` 证据处理。

## 参考证据文件

- `docs/project_division/00_project_division_index.md`
- `docs/project_division/04_langgraph_and_agents.md`
- `docs/project_division/05_rag_document_ingestion.md`
- `docs/project_division/06_assessment_mastery_and_replanning.md`
- `docs/project_division/07_frontend_learning_experience.md`
- `docs/project_division/08_mcp_llm_gateway_and_deployment.md`
- `backend/app/auth.py`
- `backend/app/main.py`
- `backend/app/db.py`
- `backend/app/services/learning.py`
- `backend/app/services/stage3.py`
- `backend/app/services/llm_gateway.py`
- `backend/app/services/official_sources.py`
- `backend/app/routers/tutor.py`
- `backend/app/routers/assessments.py`
- `backend/app/routers/plans.py`
- `backend/app/routers/documents.py`
- `frontend/components/learning-provider.tsx`
- `frontend/components/learning-shell.tsx`
- `frontend/lib/learning-data.ts`
- `pyproject.toml`
- `.env.example`
- `docker-compose.yml`
