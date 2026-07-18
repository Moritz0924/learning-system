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

当前稳定基线只有一个 Alembic head：`20260716_0012`。阶段 A 没有新增或修改数据库迁移。

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

登录后前端调用 `GET /api/goals` 恢复最近的 active 学习目标及状态；异步上传资料可通过 `GET /api/documents/{document_id}` 查询 `pending`、`processing`、`success` 或 `failed` 状态。两个接口都按当前 JWT Principal 进行资源隔离。

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

真实版本化诊断模板/评分、真实四步诊断表单、`POST /api/documents` multipart 文件上传和处理元数据迁移尚未开始。现有 onboarding 仍提交前端硬编码诊断数据；现有文档创建入口仍是 JSON/Markdown 兼容链路。长期记忆、混合 RAG、RRF/rerank 和新 Agent 不在当前实施范围内。

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
