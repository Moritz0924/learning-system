# 稳定基线验收记录（2026-07-18）

## 结论

阶段 A 稳定基线通过，可以进入阶段 B“真实诊断后端”。本结论只覆盖稳定基线，不表示真实诊断、真实 multipart 文件上传或生产外部 provider 已完成。

- 分支：`codex/github-snapshot-20260713`
- Draft PR：[Moritz0924/learning-system#1](https://github.com/Moritz0924/learning-system/pull/1)
- CI run：[29631198479](https://github.com/Moritz0924/learning-system/actions/runs/29631198479)
- 当前 Alembic head：`20260716_0012`，且只有一个 head
- 阶段 A 数据迁移：无

## 范围与边界

本阶段只完成依赖安全升级、生成物/临时目录卫生、CI/Dependabot、Compose 验证和文档同步。

本阶段没有：

- 新增长期记忆表、LangGraph checkpointer 或多轮历史；
- 实现混合 RAG、RRF、rerank 或新的 Agent；
- 新增诊断模板、评分、幂等 onboarding 字段或迁移 `0013`；
- 新增 `POST /api/documents` multipart 文件接口、处理元数据或迁移 `0014`。

## 本地验收证据

| 门禁 | 命令/证据 | 结果 |
|---|---|---|
| 后端编译与全量测试 | `.\scripts\test.ps1` | `218 passed, 1 warning`；warning 为 Starlette/httpx 兼容弃用提示 |
| Alembic | fresh SQLite `upgrade head`、`heads`、`current` | `20260716_0012 (head)`，单 head |
| 前端干净安装 | `npm ci` | 通过 |
| 路由契约 | `npm run test:ui-routes` | 通过 |
| 静态检查 | `npm run lint` | 通过 |
| 生产构建 | `npm run build` | Next.js `16.2.10` Turbopack build 通过 |
| 浏览器回归 | `npm run test:e2e -- --project=chromium` | `4 passed` |
| 生产依赖审计 | `npm audit --omit=dev --audit-level=high` | 退出码 `0`；`0 high`、`0 critical`、`2 moderate` |
| Compose | `.\scripts\verify-compose.ps1` | 无缓存重建与 smoke 通过 |
| Git 差异卫生 | `git diff --check`、仓储卫生测试 | 通过 |

Chromium E2E 覆盖：

1. 未认证用户访问学习路由时跳转登录；
2. 注册、原子 onboarding、refresh 和 logout；
3. 注册错误不泄露原始响应；
4. 并发标签页恢复且不把 Access/Refresh Token 写入浏览器存储。

## Compose 验收

`verify-compose.ps1` 只对当前仓库 `docker-compose.yml` 所属项目执行操作。它首先运行 `down -v`，删除该项目的 PostgreSQL/MinIO 卷，然后执行无缓存构建、启动和以下检查：

- `postgres`、`redis`、`minio`、`backend`、`worker`、`scheduler`、`frontend` 七个服务均处于 running；
- PostgreSQL healthcheck 为 healthy；
- `/openapi.json` 与前端根路径可达；
- 弱开发凭据下 `/api/health/ready` 返回允许的 `503 not_ready`，而不是连接失败；
- 容器内 `alembic heads/current` 均指向唯一 `20260716_0012 (head)`；
- backend、worker、scheduler runtime UID 均非 root；frontend 镜像由仓储契约固定为 `USER node`；
- 失败时输出 `docker compose ps` 和最近日志，成功时保留运行中的服务供后续 smoke 使用。

## GitHub Actions

PR 只注册以下五个 Job，全部通过：

| Job | 结果 | 关键覆盖 |
|---|---|---|
| `backend-tests` | pass | Python 3.11、compileall、218 项 pytest |
| `frontend-quality` | pass | Node 20、npm ci、route、lint、build、生产 audit |
| `frontend-e2e` | pass | Python 3.11、Node 20、Chromium 安装与 E2E |
| `migration-postgres` | pass | pgvector PostgreSQL，`head → 20260626_0004 → head`，扩展/表/索引/单 head |
| `docker-build` | pass | Compose 配置和无缓存镜像构建 |

工作流只在 PR 和 `main` push 上运行，并用 concurrency 取消同一 PR 的旧运行，避免 feature branch push 与 PR 事件重复执行十个 Job。

## 当前架构事实

- 私有 API 使用 Bearer JWT `Principal`；Access Token 只驻留前端内存，Refresh Token 使用 HttpOnly Cookie。
- `0011` 为用户增加认证身份字段；`0012` 增加 `auth_sessions` 和 `refresh_tokens`。Refresh Token 只以哈希和轮换/撤销状态保存。
- teacher 已接收结构化 `TutorContext`；可信学习状态与不可信 RAG 正文在 LLM Gateway 中分层，API 响应不暴露内部上下文。
- 当前 onboarding 后端虽然能原子创建 goal/diagnosis/plan/state，但前端仍提交硬编码自评、答案、日期和偏好，不是真实诊断。
- 当前文档链路已有对象存储、Outbox、Celery 和解析状态，但前端/后端仍没有真实 multipart 文件上传入口。

## 已知风险与下一门禁

- `2 moderate` 来自 Next.js 内嵌 PostCSS；当前 high 门禁通过，但需要跟踪上游修复，禁止使用会把 Next.js 降级到不兼容版本的 `npm audit fix --force`。
- remote LLM、embedding 和 Brave 没有真实 key，本地 Compose 因此按设计保持 `not_ready`；不能把离线/确定性路径当作生产 provider smoke。
- 阶段 B 必须先完成版本化诊断模板和纯函数评分，再实现 `0013` 原子幂等 onboarding。阶段 B 自身测试、迁移和跨用户门禁通过前，不得进入真实诊断前端。
