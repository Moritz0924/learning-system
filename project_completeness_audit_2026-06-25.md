# 项目完整性审计报告（2026-06-25）

## 结论摘要

当前项目已经不是纯文档或空壳：后端 FastAPI、SQLAlchemy/Alembic、Phase2 LangGraph、RAG/测验/计划调整服务、前端 Next.js 页面、Docker Compose 配置和测试套件都存在，并且主 happy path 可以跑通。

但从“真实用户、真实依赖、真实多租户、真实部署”的标准看，项目仍是一个可演示 V1 原型，不是完整生产闭环。最严重的问题有三类：

1. 多个写接口缺少 `goal_id` 与 `user_id` 的归属校验，存在跨用户读写/污染学习状态的风险。
2. 文档、RAG、MCP、LLM、OCR、pgvector、MinIO、Celery 等链路有明显 demo/占位实现，容易形成“看起来闭环、实际没真实工作”的假闭环。
3. 当前本地 `.venv` 与 `pyproject.toml` / `.env.example` 声明不一致，Postgres、Celery、MCP 路径在本机直接断。

## 本次验证结果

已通过：

- `.venv\Scripts\python.exe -m pytest`：`36 passed, 1 warning`
- `npm run test:ui-routes`：通过
- `npm run lint`：通过
- `npm run build`：通过
- `docker compose config`：通过
- `python -m alembic -c backend\alembic.ini upgrade head`：通过
- 显式 `PYTHONPATH='.;src'` 后，`.venv\Scripts\alembic.exe -c backend\alembic.ini upgrade head`：通过

未完全通过或未能证明真实闭环：

- `docker version` 显示 Docker Desktop Linux daemon 未运行，因此没有验证 `docker compose up` 的真实容器启动。
- 裸 `.venv\Scripts\alembic.exe -c backend\alembic.ini upgrade head` 在 Windows 本地失败：`ModuleNotFoundError: No module named 'backend'`。
- fresh SQLite DB 不先跑迁移直接调用 API 会失败：`sqlite3.OperationalError: no such table: users`。
- 本地 `.venv` 缺少 `celery`、`redis`、`psycopg`、`mcp`，与 `pyproject.toml` 和 `.env.example` 声明不一致。

## 项目完整性评估

| 模块 | 当前状态 | 完整性判断 |
|---|---|---|
| 01 产品、课程与入学诊断 | 有课程 seed、goal 创建、诊断规则、初始计划 | 可演示；规则固定，异常与身份边界弱 |
| 02 后端 API 与学习域服务 | FastAPI 路由基本齐全，状态/任务/测验/计划/资料接口存在 | happy path 可用；认证、授权、错误处理不足 |
| 03 状态知识库与数据库 | ORM 模型和 3 个 Alembic 迁移存在，迁移库测试通过 | 基础表完整；真实启动依赖迁移流程，create_all 测试仍会掩盖漂移 |
| 04 LangGraph 与 Agent 编排 | 使用真实 LangGraph，节点包含 load/retrieve/teacher/assessment/observer/planner/persist | 图是真的；Agent 智能主要靠规则和离线 fallback，不是完整智能体闭环 |
| 05 RAG 与文档入库 | Markdown/PDF 可解析入库，document_chunks 可被检索 | 只完成轻量文本链路；无真实 pgvector、无 MinIO 写入、无图片 OCR |
| 06 测验、掌握度与计划调整 | 测验/作答/掌握度/计划调整可持久化，计划调整可 apply | 可演示；评分与题目生成确定性，阶段测验未真正影响路径门控 |
| 07 前端学习体验 | 页面齐全，lint/build 通过，能调用后端 happy path | 体验完整度不错；大量 demo fallback 和硬编码数据会掩盖后端断点 |
| 08 MCP、LLM Gateway 与部署 | LLM Gateway wrapper、MCP server 文件、Compose 配置存在 | 形状存在；本地依赖缺失，MCP 搜索是伪搜索，真实 compose up 未验证 |

## 按项目优先度重排的问题清单

下面是本报告的主阅读顺序。优先级按“是否阻断真实多用户使用、是否阻断本地/部署运行、是否造成假闭环误判、是否影响学习效果可信度、是否影响体验成熟度”排序。后面的分类附录保留原始证据和细节。

### P0：必须先处理，否则不能进入真实多用户/真实运行

1. **跨用户 / 跨目标归属校验缺失**
   - 类别：明面 bug、安全与数据完整性。
   - 影响：attacker 可以混用自己的 `user_id` 和别人的 `goal_id` 访问 chat、assessment、replan 等写接口；测验提交只按 `assessment_id` 查对象，也可污染 owner 的学习状态。
   - 优先原因：这是从 demo 进入真实用户环境前的硬阻断。

2. **身份模型不统一**
   - 类别：潜在 bug、架构风险。
   - 影响：部分接口用 `X-User-Id`，大量写接口仍信任 body/query 里的 `user_id`。
   - 优先原因：它是跨用户漏洞的系统性根因，必须和归属校验一起修。

3. **本地 `.venv` 依赖与项目声明不一致**
   - 类别：明面 bug、运行环境断点。
   - 影响：当前 `.venv` 缺少 `celery`、`redis`、`psycopg`、`mcp`；Postgres、Celery、MCP 路径会直接失败。
   - 优先原因：如果依赖不闭合，`.env.example` 和 Docker/worker/MCP 说明都无法被真实验证。

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

7. **假 RAG 闭环**
   - 类别：假闭环。
   - 影响：无文档时仍返回 `_default_citations()`，让回答看起来有真实引用。
   - 优先原因：会误导用户以为系统完成了资料检索和引用校验。

8. **假官方联网搜索 + 前端默认搜索会 400**
   - 类别：明面 bug + 假闭环。
   - 影响：后端只是拼搜索 URL，不真实联网；前端默认带 `platform.openai.com`，后端白名单不含该域名，默认请求失败。
   - 优先原因：同时破坏前端功能和 MCP/官方来源真实度。

9. **假 LLM 闭环**
   - 类别：假闭环。
   - 影响：`LLM_API_KEY` 为空时静默进入 `_offline_complete()`，用户不知道当前不是远程模型回答。
   - 优先原因：模型能力是讲师体验核心，必须显式区分 offline/demo 与 remote。

10. **假向量检索 / 假 pgvector**
    - 类别：假闭环、架构差距。
    - 影响：embedding 是 hash 字节数组，列类型是 JSON，检索在 Python 内存排序，不是 pgvector。
    - 优先原因：RAG 质量和扩展性都会受影响，不能按真实向量库验收。

11. **假 MinIO / 对象存储闭环**
    - 类别：假闭环、运行架构差距。
    - 影响：Compose 有 MinIO，DB 有 `object_key`，但代码没有真实上传/下载对象。
    - 优先原因：Celery/大文件/重试处理都依赖对象存储真实闭合。

12. **假 OCR 闭环 / 图片上传语义不一致**
    - 类别：明面 bug + 假闭环。
    - 影响：文档承诺图片 OCR；实际图片上传 HTTP 201，但 `parse_status=failed`，`NoopOCRClient` 没进入上传解析路径。
    - 优先原因：这是分工书验收项与代码能力直接不一致。

13. **SQLite 测试与 Postgres 真实行为可能不同**
    - 类别：潜在 bug、验证缺口。
    - 影响：当前多数测试用 SQLite 和 `create_all()`；Postgres、JSON、外键、并发、迁移行为未被充分覆盖。
    - 优先原因：真实部署目标是 Postgres/pgvector，测试层不能只证明 SQLite demo。

14. **资料上传没有真实对象存储恢复能力**
    - 类别：潜在 bug、数据链路风险。
    - 影响：defer 模式只登记 DB；如果没有对象存储保存原文，worker 后续无法取回内容。
    - 优先原因：这会让异步文档处理无法可靠重试。

### P2：学习系统可信度与业务正确性问题

15. **假测验智能闭环**
    - 类别：假闭环、学习效果风险。
    - 影响：题目模板固定，评分按关键词和长度，不是真实题库/LLM rubric/任务证据驱动。
    - 优先原因：测验质量直接决定掌握度和计划调整是否可信。

16. **前端会自动填充测验答案**
    - 类别：潜在 bug、学习证据污染。
    - 影响：用户空答案会被替换成固定句子，空答看起来像有效作答。
    - 优先原因：会污染评分、掌握度和计划调整。

17. **假计划调整闭环**
    - 类别：假闭环、业务逻辑风险。
    - 影响：缺失数据默认 0.8/0；阶段测验状态未成为路径推进的硬门控。
    - 优先原因：计划调整是学习闭环的核心，不能靠默认值制造稳定假象。

18. **服务层 commit 分散，失败时可能部分写入**
    - 类别：潜在 bug、数据一致性风险。
    - 影响：多个服务函数内部直接 commit，外部依赖或引擎失败时可能留下半完成记录。
    - 优先原因：会造成审计、状态快照、计划和事件之间不一致。

19. **计划版本和任务会话存在并发风险**
    - 类别：潜在 bug、并发一致性风险。
    - 影响：`_next_plan_version()` 用 max+1，`start_task()` 查再建 active session，没有锁或唯一约束。
    - 优先原因：多端/重复点击/并发请求下会产生重复版本或重复会话。

20. **重复邮箱创建用户未做业务级错误处理**
    - 类别：明面 bug、API 可用性。
    - 影响：相同 email、不同 user_id 会触发数据库唯一约束异常，未转成 409/400。
    - 优先原因：真实注册/目标创建会出现不可读 500。

21. **LLM Gateway 缺少生产级错误处理**
    - 类别：潜在 bug、稳定性风险。
    - 影响：模型调用失败向上抛，没有统一错误转换、重试、降级标记、限流和脱敏。
    - 优先原因：远程模型接入后会成为高频故障点。

22. **`today_tasks` 不是真正的“今天”**
    - 类别：潜在 bug、产品语义风险。
    - 影响：主要按 `scheduled_day == 1`，没有结合真实日期、计划起始日、时区和用户日程。
    - 优先原因：会影响每日学习体验和完成率统计可信度。

23. **假长期记忆闭环**
    - 类别：假闭环、产品承诺差距。
    - 影响：`memory_gate` 只返回空数组，前端也没有真实长期记忆查看/删除/审批链路。
    - 优先原因：如果产品宣称长期记忆，就必须有用户控制和审计。

### P3：体验、配置与成熟度问题

24. **假前端完整体验**
    - 类别：假闭环、产品体验风险。
    - 影响：无 `goalId` 时聊天、测验、计划调整、任务状态都走本地 demo；学习路径、资源列表、视频/文档详情大量硬编码。
    - 优先原因：会掩盖后端失败，但不如 P0/P1 那样直接阻断真实系统。

25. **CORS 只适合本地开发**
    - 类别：潜在 bug、部署配置。
    - 影响：只允许 `127.0.0.1:3000` 和 `localhost:3000`，真实域名需环境化。
    - 优先原因：部署前必须处理，但可晚于核心数据/运行闭环。

## 分类附录：明面上的 Bug

### 1. 跨用户 / 跨目标归属校验缺失（高优先级）

受影响位置：

- `backend/app/routers/tutor.py`
- `backend/app/routers/assessments.py`
- `backend/app/routers/plans.py`
- `backend/app/services/stage3.py`

现象：

- `POST /api/tutor/chat` 可以传入 attacker 的 `user_id` 和 owner 的 `goal_id`，接口仍返回 200。
- `POST /api/assessments` 可以用 attacker 的 `user_id` 给 owner 的 `goal_id` 创建测验，接口返回 201。
- `POST /api/assessments/{assessment_id}/submit` 只按 `assessment_id` 查测验，不校验测验归属；attacker 可以提交 owner 的测验，接口返回 200。
- `POST /api/plans/replan` 可以用 attacker 的 `user_id` 给 owner 的 `goal_id` 生成计划调整，接口返回 200。

根因：

- 多数写接口信任请求体里的 `user_id`。
- 服务层没有统一校验 `LearningGoal.user_id == user_id`。
- 数据库外键只分别指向 `users.id` 和 `learning_goals.id`，没有复合约束阻止“存在的 user + 别人的 goal”组合。

影响：

- 多用户场景下会污染别人的 goal、assessment、plan_adjustment、agent_runs、learning_events 和 mastery_records。
- 这是项目从单机 demo 进入真实用户环境前必须先修的问题。

### 2. 前端官方来源搜索默认会触发 400

受影响位置：

- `frontend/components/learning-provider.tsx`
- `backend/app/services/official_sources.py`

现象：

- 前端默认请求域名包含 `platform.openai.com`。
- 后端白名单没有 `platform.openai.com`。
- 实测 `POST /api/tools/search-official-learning-sources` 返回 `400 {"detail":"domain not whitelisted: platform.openai.com"}`。

影响：

- 设置/资料区的“搜索官方来源”按钮默认失败。
- 用户会认为官方来源检索坏了。

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
- 当前 `.venv` 中这些包缺失。
- `DATABASE_URL=postgresql+psycopg://...` 时，导入 `backend.app.db` 会 `ModuleNotFoundError: No module named 'psycopg'`。
- `DOCUMENT_PROCESSING_MODE=celery` 时，上传文档会 `ModuleNotFoundError: No module named 'celery'`。
- `create_mcp_server()` 会失败：`RuntimeError: MCP SDK is not installed`。

影响：

- 测试通过不代表 `.env.example` 描述的本地/部署运行方式可用。
- Celery Worker、MCP Server、Postgres 真实链路没有在当前环境闭合。

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

### 6. 重复邮箱创建用户未做业务级错误处理

受影响位置：

- `backend/app/services/learning.py`
- `backend/app/routers/goals.py`

现象：

- 用不同 `user_id` 但相同 `email` 创建 goal，会触发数据库唯一约束异常。
- 当前没有捕获 `IntegrityError` 并转成 409/400，而是请求级 500。

影响：

- 真实注册/目标创建会出现不可读错误。
- API 客户端无法区分“邮箱已存在”和系统故障。

### 7. 图片 OCR 与接口返回语义不一致

受影响位置：

- `docs/project_division/05_rag_document_ingestion.md`
- `backend/app/services/stage3.py`
- `backend/app/routers/documents.py`

现象：

- 分工书要求图片 OCR 文本提取。
- 当前图片上传会返回 HTTP 201，但 `parse_status` 是 `failed`，没有 OCR 入库。
- `NoopOCRClient` 存在，但实际上传解析路径没有使用它处理图片。

影响：

- 前端/用户看到“上传成功”，实际不能检索。
- 文档承诺与代码行为不一致。

## 分类附录：潜在 Bug 与风险

### 1. 身份模型不统一

- `/api/state/current` 使用 `X-User-Id`。
- `/api/goals`、`/api/onboarding/diagnosis`、`/api/tutor/chat`、`/api/assessments`、`/api/plans/replan`、`/api/documents/upload` 多数使用 body/query 中的 `user_id`。
- 这会导致权限、审计和前端调用方式长期不一致。

### 2. 计划版本和任务会话存在并发风险

- `_next_plan_version()` 用查询 max version + 1，没有锁。
- `start_task()` 先查 active session 再创建，没有唯一约束。
- 并发请求下可能创建重复版本或重复 active session。

### 3. SQLite 测试与 Postgres 真实行为仍可能不同

- 当前主测试多为 SQLite。
- SQLite 默认外键行为、JSON 行为、并发行为与 Postgres 不一致。
- `Base.metadata.create_all()` 测试路径仍可能掩盖 Alembic 与 ORM 的差异。

### 4. 服务层 commit 分散，失败时状态可能部分写入

- 多个服务函数内部直接 `session.commit()`。
- 部分路径先写审计/事件/状态，再跑引擎或外部依赖。
- 一旦外部 API、Celery、LLM 或 DB 抛错，可能出现半完成记录。

### 5. LLM Gateway 缺少生产级错误处理

- 外部模型调用失败会向上抛异常，路由层没有统一转换。
- 没有超时以外的重试、限流、熔断、模型降级标记、敏感信息脱敏。
- `LLM_API_KEY` 为空时静默进入离线回答，调用方不容易知道当前不是实时模型。

### 6. `today_tasks` 不是真正的“今天”

- `get_today_tasks()` 主要按 `scheduled_day == 1` 过滤。
- 真实系统里应该结合日期、计划起始日、时区、任务状态和用户日程。

### 7. 前端会自动填充测验答案

- 提交测验时，如果用户没有填答案，前端会用固定句子替代空答案。
- 这会让“用户没答题”看起来像“用户提交了一个可评分答案”。

### 8. 资料上传没有真实对象存储闭环

- `Document.object_key` 被写入，但没有看到 MinIO/S3 上传逻辑。
- Celery 模式把文件内容 base64 放进任务消息。
- defer 模式下如果没有外部保存原文，后续 worker 无法从对象存储重新取回内容。

### 9. CORS 只适合本地开发

- `backend/app/main.py` 只允许 `127.0.0.1:3000` 和 `localhost:3000`。
- 真实部署域名、预览环境、移动端 WebView 都需要配置化。

## 分类附录：假闭环：看起来完成但不是真实情况

### 1. 假 RAG 闭环

当前行为：

- 没有任何成功入库文档时，`SQLAlchemyRagRepository.retrieve()` 会返回 `_default_citations()`。
- 返回内容看起来像可信引用：`AI App Dev V1 - RAG Foundations`，并带 LangChain URL。

问题：

- 用户会以为回答来自真实检索文档。
- 实际只是固定 fallback。

建议：

- 无检索结果时应明确返回“无可用资料”，或在响应里标记 `source_type=fallback_demo`。
- 生产路径不要用默认 citation 冒充检索结果。

### 2. 假官方联网搜索

当前行为：

- `search_official_learning_sources()` 没有真实联网。
- 它只是根据域名拼接 `https://domain/search?q=...`，生成固定 snippet。

问题：

- 分工书写的是“只读联网检索工具”，但当前没有检索网页、没有校验页面存在、没有摘要真实内容。

建议：

- 要么把功能命名为“官方文档搜索 URL 生成器”，要么接入真实搜索/抓取并保留审计。

### 3. 假 LLM 闭环

当前行为：

- `LLM_API_KEY` 为空时，`LLMGatewayClient` 静默返回 `_offline_complete()`。
- 回答会把用户 prompt 拼进模板句子。

问题：

- 前端显示“经后端 LLM Gateway”，但用户无法知道当前没有真实模型。

建议：

- 响应增加 `provider_mode: offline|remote`。
- 前端明确展示“离线演示回答”。

### 4. 假向量检索 / 假 pgvector

当前行为：

- embedding 是 `sha256(text.lower())` 的 16 维字节数组。
- `document_chunks.embedding` 是 JSON，不是 pgvector column。
- 检索是 Python 内存排序，不是数据库向量索引。

问题：

- “向量检索可用”只在形式上成立，语义相关性不可靠。
- Compose 使用 `pgvector/pgvector:pg16`，但代码没有真正用 pgvector。

建议：

- 使用真实 embedding provider 或本地 embedding 模型。
- 数据库列迁移为 vector 类型，并添加索引。

### 5. 假 MinIO / 对象存储闭环

当前行为：

- Compose 有 MinIO。
- `Document.object_key` 有值。
- 代码没有实际上传/下载 MinIO 对象。

问题：

- 对象存储只是部署清单里的服务，不是文档处理链路的一部分。

建议：

- 上传时先写对象存储，再把 object_key 交给 worker。
- worker 从对象存储读取，避免大文件塞进 Celery 消息。

### 6. 假 OCR 闭环

当前行为：

- `NoopOCRClient` 只返回固定文本。
- 图片上传路径没有真正调用 OCR。
- 图片最终 `parse_status=failed`。

问题：

- 分工书承诺图片 OCR，但实际没有。

建议：

- 接入真实 OCR 或把 V1 范围明确降级为 PDF/Markdown。

### 7. 假测验智能闭环

当前行为：

- 题目模板固定：`Explain key idea ...`。
- 评分基于关键词和长度。
- 前端空答案会被替换成固定答案。

问题：

- 这更像 deterministic demo rubric，不是能评估真实理解的验收系统。

建议：

- 题目来源应与知识点、资料片段、任务证据绑定。
- 空答案应按空答案评分，不应前端代答。

### 8. 假计划调整闭环

当前行为：

- 缺失数据默认：completion/correctness 默认 0.8，mastery_delta 默认 0。
- 无真实学习证据时也能生成 `keep` 调整。
- 阶段测验状态会写入，但 `decide_observer_action()` 主要只看 completion/correctness/mastery_delta。

问题：

- “计划调整已生成”不一定代表系统真的理解学习状态。
- 阶段测验并没有成为路径推进的强门控。

建议：

- 缺失数据应阻止自动调整或降级为“需人工确认”。
- phase assessment 状态应进入 planner 的硬规则。

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

- 无 `goalId` 时，聊天、测验、计划调整、任务状态都能本地 demo fallback。
- 学习路径、资源列表、视频/文档详情大量硬编码。

问题：

- 页面看起来很完整，但很多操作没有真实后端状态来源。

建议：

- demo fallback 应有明显 UI 标识。
- 真实模式下后端失败不能被 demo 数据覆盖。

## 修复路线图

### P0：先封住真实多用户风险

1. 所有需要身份的接口统一从认证层取 user，不再信任 body/query 里的 `user_id`。
2. 所有服务入口校验 `LearningGoal.id == goal_id AND LearningGoal.user_id == user_id`。
3. `assessment_id`、`adjustment_id`、`task_id`、`document_id` 都必须校验归属。
4. 增加跨用户攻击回归测试：chat、assessment create/submit、replan、document list/upload。

### P0：修复运行环境闭环

1. 重建 `.venv` 或补装 `pyproject.toml` 依赖，确认 `celery`、`redis`、`psycopg`、`mcp` 可导入。
2. 文档统一推荐 `python -m alembic` 或在脚本里设置 `PYTHONPATH`。
3. 本地启动命令必须明确先跑迁移，或启动时检测未迁移 DB 并给出可读错误。

### P1：把假闭环显式标成 demo 或接成真实实现

1. RAG 无结果时不要返回默认 citation。
2. LLM offline mode 透出到 API 和前端。
3. 官方来源搜索要么改名为 URL 生成，要么接真实检索。
4. 文档入库接真实对象存储，Celery 从 MinIO 取文件。
5. 图片 OCR 如果不做，就从 V1 验收标准里降级说明。

### P1：补齐真实测试

1. 添加 Postgres 测试或至少容器化迁移/CRUD smoke。
2. 添加 Celery mode upload smoke。
3. 添加 MCP server startup smoke。
4. 添加前端官方来源搜索默认域名测试。
5. 添加 fresh DB 未迁移时的启动提示测试。
6. 添加 Playwright 级别前端到后端真实工作流测试。

### P2：提高学习系统可信度

1. 用真实题库/LLM rubric/人工规则混合生成测验。
2. 掌握度更新记录更多证据：任务完成质量、答题时间、错因、复习间隔。
3. 计划调整引入用户确认、回滚和冲突处理。
4. `today_tasks` 按真实日期/时区/计划起点计算。
5. CORS、API base、模型配置、白名单域名都改成环境化配置。

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
