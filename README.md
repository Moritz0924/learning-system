# Learning System

这是一个本地优先的自适应学习系统原型，包含 FastAPI 后端、Next.js 前端、SQLAlchemy/Alembic 数据层、LangGraph 学习编排、文档入库、MCP 只读官方来源工具和 Docker Compose 配置。

## 本地环境

推荐在 Windows PowerShell 中使用仓库内虚拟环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd frontend
npm ci
cd ..
```

## 数据库迁移

Windows 本地推荐用 `python -m alembic`，避免裸 `alembic.exe` 在没有 `PYTHONPATH` 时找不到 `backend`：

```powershell
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```

当前只有一个 Alembic head：`20260718_0014`。`0013` 为 `baseline_diagnostics` 增加模板版本、模板哈希、评分明细和按用户隔离的幂等 `request_id`；旧记录回填为 `legacy_unversioned`。`0014` 为文档增加可空的大小、稳定错误码、页数、块数、解析器版本与处理时间字段，不伪造历史处理元数据。

## 启动开发服务

后端：

```powershell
.\scripts\dev-backend.ps1
```

前端：

```powershell
cd frontend
npm run dev
```

默认前端地址是 `http://127.0.0.1:3000`，后端地址是 `http://127.0.0.1:8000`。

预览环境或自定义域名需要把前端 origin 加入 `CORS_ALLOWED_ORIGINS`，多个值用英文逗号分隔。

## 身份与工作区恢复

私有 API 仅接受 Bearer JWT；Access Token 只驻留在页面内存，Refresh Token 只保存在 HttpOnly Cookie。注册错误统一返回 `detail.code`，包括 `auth.email_already_registered`、`auth.weak_password`、`auth.invalid_display_name` 与 `auth.registration_conflict`。

登录后前端调用 `GET /api/goals` 恢复最近的 active 学习目标及状态；真实文件使用 `POST /api/documents` multipart 上传，可通过 `GET /api/documents/{document_id}` 查询 `pending`、`processing`、`success` 或 `failed` 状态。上述私有接口都按当前 JWT Principal 进行资源隔离。

认证数据由 `users`、`auth_sessions` 和 `refresh_tokens` 承载。Refresh Token 只以哈希形式保存并在刷新时轮换；前端不会把 Access Token 或 Refresh Token 写入 localStorage/sessionStorage。

## 验证

Windows 上 pytest 默认临时目录可能没有权限。推荐使用仓库脚本，它会把 `TMP` 和 `TEMP` 固定到已忽略的 `.tmp/`：

```powershell
.\scripts\test.ps1
```

完整前端验证：

```powershell
cd frontend
npm run test:ui-routes
npm run lint
npm run build
npm run test:e2e -- --project=chromium
```

Compose 配置校验：

```powershell
docker compose config
```

完整 Compose 重建与 smoke：

```powershell
.\scripts\verify-compose.ps1
```

注意：`docker compose config` 只验证配置可解析，不等于容器真实启动成功。`verify-compose.ps1` 会精确针对当前仓库的 Compose 项目执行 `down -v`，因此会删除该项目的 PostgreSQL 与 MinIO 卷，然后无缓存重建、启动并检查 7 个服务、HTTP 探针、迁移 head 和非 root UID。运行它需要 Docker Desktop Linux daemon。

## 稳定基线状态（2026-07-18）

- 后端 `compileall + pytest`：`218 passed, 1 warning`。
- 前端：`npm ci`、route contract、ESLint、Next.js 16 production build 均通过；Chromium E2E 为 `4 passed`。
- `npm audit --omit=dev --audit-level=high`：退出码 `0`，`0 high`、`0 critical`，仍有 Next.js 内嵌 PostCSS 引出的 `2 moderate`。
- Compose：无缓存重建和 7 服务 smoke 通过；弱开发凭据使 `/api/health/ready` 返回预期的 `503 not_ready`，不代表服务不可达。
- Draft PR 的 `backend-tests`、`frontend-quality`、`frontend-e2e`、`migration-postgres`、`docker-build` 五个 Job 全绿。

完整证据见 [稳定基线验收记录](docs/engineering/stable-baseline-acceptance-2026-07-18.md)。

## 真实诊断状态（2026-07-18）

版本化诊断模板、纯函数评分、原子幂等 onboarding 与四步真实用户表单已经完成。前端从受认证的 `GET /api/onboarding/diagnostic-template?domain=ai_app_dev` 加载安全模板，并用一次性 `request_id` 提交目标、偏好、自评和知识题；后端在一个事务中创建 Goal、BaselineDiagnostic、Initial Plan 和 State Snapshot。模板响应不包含正确答案、难度或内部权重。

当前验证为后端 `262 passed, 1 warning`，前端 route contract、ESLint、Next.js production build 通过，Chromium E2E `9 passed`，覆盖必答题拦截、双击防重、401 refresh 重放 ID 不变、网络失败后保留全部表单和相同 ID 手动重试。真实诊断未增加环境变量。

## 真实文件上传状态（2026-07-18）

真实文件闭环已经完成：`POST /api/documents` 只接受由 JWT Principal 归属的单个 multipart `UploadFile`，分块读取并限制大小，校验文件名、扩展名、MIME、magic/content 和解析器能力；支持 PDF、PPTX、常见图片、Markdown 与 TXT。对象存储或数据库失败会回滚文档并补偿清理对象。原 JSON `POST /api/documents/upload` 已标记 deprecated，但继续支持 Markdown 笔记并复用同一创建服务。

`0014` 增加处理元数据；Worker 维护 `pending → processing → success/failed`，基础设施失败在重试耗尽前回到 pending。文档列表和单项响应只返回安全状态字段，不返回 object key、chunk、内部堆栈或 provider 信息。前端设置页提供独立的文件拖拽/选择与 Markdown 笔记入口、本地校验、取消选择、防重复上传、状态轮询和身份切换隔离。

当前验证为后端 `295 passed, 1 warning`，前端 route contract、ESLint、Next.js production build 通过，Chromium E2E `15 passed`。其中上传 E2E 覆盖 FormData boundary、成功/失败轮询、重新选择竞态、身份切换、非法文件、服务端拒绝和双击防重。本阶段没有新增环境变量。

长期记忆、LangGraph checkpointer、多轮历史、混合 RAG、RRF/rerank 和新 Agent 不在当前实施范围内；remote embedding/LLM/Brave 的真实 key smoke、生产告警与人工重放仍待外部环境验收。

`NEXT_PUBLIC_API_BASE_URL` is a frontend build-time value. Set it in the shell or a root `.env` file before `docker compose build` when the browser must call a non-default API URL. Changing only the running container environment cannot rewrite the already-built browser bundle; the local default is `http://127.0.0.1:8000`.

Compose loads `.env.example` as documented defaults and then an optional root `.env`. Provider keys and service credentials can also be supplied directly from the shell; shell/root `.env` values override the examples for backend, worker, and scheduler together. Production mode intentionally remains not-ready while database/MinIO credentials use their development defaults or `LLM_API_KEY`, `EMBEDDING_API_KEY`, or `BRAVE_SEARCH_API_KEY` are missing.

For the bundled PostgreSQL service, set `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`, plus a matching `DATABASE_URL` for the application services. `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` configure both MinIO itself and the application clients.

## MCP Server

MCP 只读官方来源工具入口：

```powershell
.\.venv\Scripts\python.exe -m backend.app.mcp_server
```

该工具只暴露 `search_official_learning_sources`，用于白名单官方来源检索。
## Runtime Configuration

LLM Gateway uses `LLM_MAX_RETRIES` for short transient HTTP retries before returning `runtime_metadata.llm.mode=degraded`.

Celery document ingestion uses a durable database Outbox. The `scheduler` Compose service runs Celery Beat and republishes due or stale-leased events. `DOCUMENT_OUTBOX_DISPATCH_INTERVAL_SECONDS` controls the scan interval, `DOCUMENT_OUTBOX_DISPATCH_LEASE_SECONDS` controls the publish lease, and `DOCUMENT_PROCESSING_MAX_ATTEMPTS` / `DOCUMENT_PROCESSING_RETRY_DELAY_SECONDS` control processing retry convergence.

Document uploads are bounded at the raw request, decoded file, parser, and embedding-work layers. `DOCUMENT_MAX_REQUEST_BYTES` limits the request before JSON parsing, `DOCUMENT_MAX_UPLOAD_BYTES` limits decoded content, `DOCUMENT_MAX_PDF_PAGES` / `DOCUMENT_MAX_IMAGE_PIXELS` limit parser work, and `DOCUMENT_MAX_EXTRACTED_CHARS` / `DOCUMENT_MAX_CHUNKS` bound decompressed text and embedding quota.

Production-like deployments should set `APP_ENV=production` and check readiness before routing traffic:

```powershell
curl http://127.0.0.1:8000/api/health/ready
```

`/api/health/ready` returns `503` with a `missing` list when production configuration is incomplete or unsafe and an `unavailable` list when a configured database, Redis, or MinIO dependency cannot be reached. Production also requires the real provider modes (`celery`, `minio`, `openai`, `pgvector`, `brave`, and `tesseract`). `READINESS_PROBE_TIMEOUT_SECONDS` bounds each dependency probe. The default `tutor/tutor` database credentials and `minioadmin/minioadmin` MinIO credentials in `.env.example` are development examples and are intentionally rejected in production readiness checks.

The backend, worker, and scheduler reuse one backend image and run as UID `10001`; the frontend runs as the non-root `node` user.
