# learning-system 项目架构重构方案

> 范围：`stage3.py` 拆分、JWT 与统一资源归属校验、真实 RAG 链路、迁移执行计划。
> 原则：模块化单体；先保持行为，再调整边界；生产环境禁止“伪成功”降级。

## 0. 执行进度（2026-07-13）

- `stage3.py` 已拆为 application service、persistence repository、基础设施 adapter 和兼容 facade；运行时代码不再直接依赖旧大文件实现。
- 资源归属校验已先按 `X-User-Id` 占位认证完成收口：私有 API 从 header 获取当前用户，legacy body/query `user_id` 只做兼容校验；chat、assessment、diagnosis、plan、task、state、document 和 official-source tools 已有跨用户回归覆盖。
- JWT access/refresh token、`auth_sessions` 和 `Principal` 尚未切换；这会改变前后端会话协议，仍作为后续重大决策执行。
- RAG 入库已具备 object storage、outbox、Celery worker/Beat scheduler、`parse_error`、OCR、Postgres `pgvector vector(1536)` 和 deterministic pgvector smoke；初次 broker 发布失败会保留 durable pending 文档，由周期 dispatcher 重投。迁移 `0010` 会为历史 pending 文档补 Outbox 事件并增加 due-event 调度索引。查询时 embedding 或数据库检索失败都会返回 `retrieval_status=failed`、无伪引用，并在独立 savepoint 后把 `rag.retrieve` 工具审计持久化为 failed。上传同时限制原始请求体、解码后字节数、PDF 页数、图片像素数、累计提取字符数和 chunk 数。remote embedding 成功入库仍依赖外部 key，告警与人工重放界面仍待补。
- 生产 readiness 除关键配置和弱默认凭据外，还强制真实 provider mode，并实际探测 PostgreSQL、Redis 和 MinIO；配置问题写入 `missing`，依赖不可达写入 `unavailable`，探针超时由 `READINESS_PROBE_TIMEOUT_SECONDS` 限制。`APP_ENV` 为空白时会继续读取 `ENVIRONMENT`，LLM、embedding、RAG、官方搜索、OCR 和对象存储运行层会把空白环境变量归一化为缺失或默认值。
- 计划、任务和测验状态机已补并发不变量：每个 user/goal 只有一个 active plan，每个 user/task 只有一个 active session；旧 proposal、重复 assessment submit、重复 task complete 和完成后再次 start 均不会重复写学习证据；`0009` 收敛历史重复 active plan 时会同步 snapshot 的 plan id/version/`generated_from`，避免迁移后仍读取 loser；引擎失败会回滚业务写入并单独保存脱敏 failed agent-run。
- 阶段测验状态已成为计划推进硬门控；缺失观察指标会显式标记 `automatic_adjustment_allowed=false`。计划调整与 diagnosis 统一先锁 goal 行，再进入 snapshot/plan 更新；真实 PostgreSQL 双提案并发 apply 得到一个 `200`、一个 `409`，active plan 只递增一个版本。goal、diagnosis、初始 plan/state 现在可通过单一原子 onboarding 用例提交，诊断失败不会残留孤儿 goal；PostgreSQL 冷启动课程 seed 使用 transaction-scoped advisory lock，SQLite 也强制开启外键。前端身份变化会递增身份代次、清空所有身份绑定状态并忽略旧身份的迟到响应。空 `today_tasks` 使用真实空态。
- Docker build context 已通过 `.dockerignore` 排除本地依赖和生成产物，backend/worker/scheduler 复用同一个后端镜像，应用容器以非 root UID 运行；可选根 `.env` 和 shell 凭据会覆盖 `.env.example`，PostgreSQL/MinIO 服务端与应用客户端凭据均可同步参数化，不会再被示例空 key 锁死。前端根路径在服务器层返回 `307 + Location: /path`。浏览器 API URL 明确为 build-time 配置，不再设置无效运行时变量。E2E 生命周期改为跨平台 Node runner，启动前拒绝占用端口、验证所属进程存活，并在 `SIGINT/SIGTERM/SIGHUP` 时等待清理完整进程组。`.gitignore` 已忽略后续 `frontend/.next/` 产物，历史上已被 Git 跟踪的构建文件仍需清理索引。
- 当前本地后端验证为 `179 passed, 1 warning`；前端 route contract、lint、production build 和 Chromium E2E（`8 passed`）已通过。E2E 另覆盖并发 refresh 后的强制刷新排队，以及上传期间新笔记草稿不被旧响应清空。2026-07-11 的 Compose 全量镜像重建和 7 服务运行证据覆盖当时源码；本轮依赖与 JWT 决策完成后必须按当前源码重新 build/up 并复验 Postgres、OpenAPI、重定向、迁移头和非 root UID。`npm audit --omit=dev --audit-level=high --registry=https://registry.npmjs.org` 当前仍报告 Next.js critical/high 和 Playwright high 漏洞；JWT 会话协议、前端安全升级和真实外部 key smoke 是尚未闭合的重大边界。

## 1. 重构目标与边界

### 核心问题

| 问题 | 风险 | 目标 |
|---|---|---|
| `stage3.py` 职责混杂 | 改动牵连大、测试难 | 拆为用例服务、仓储、工作流和基础设施 |
| `X-User-Id` 占位认证 | 用户身份可伪造 | JWT + Principal + 资源查询时归属过滤 |
| 模拟能力和真实能力混用 | 形成伪检索、伪回答 | `live / degraded / unavailable` 状态协议 |
| LangGraph 直接写库 | 事务与推理耦合 | 工作流只返回动作，应用服务统一提交 |
| 文档索引状态不可靠 | 上传成功不等于可检索 | Document 状态机 + Outbox + Celery |

### 不变约束

- 保持模块化单体；当前不拆微服务。
- 继续使用 PostgreSQL/pgvector，不引入 Qdrant。
- 第一轮只调整边界，不重写学习规则。
- 生产环境外部能力不可用时必须显式降级或拒绝执行。

## 2. 目标目录

```text
backend/app/
├── api/
│   ├── v1/
│   └── deps.py
├── core/
│   ├── config.py
│   ├── security.py
│   ├── exceptions.py
│   └── uow.py
├── application/
│   ├── auth_service.py
│   ├── onboarding_service.py
│   ├── tutor_service.py
│   ├── learning_service.py
│   ├── assessment_service.py
│   ├── planning_service.py
│   └── document_service.py
├── domain/
│   ├── learning/
│   ├── documents/
│   └── tutor/
├── workflows/tutor/
├── infrastructure/
│   ├── persistence/repositories/
│   ├── auth/
│   ├── rag/
│   ├── llm/
│   ├── storage/
│   └── queue/
└── workers/
```

职责：API 只做 HTTP 与依赖注入；Application 负责用例和事务；Domain 保存纯规则；Workflow 负责推理和动作产出；Infrastructure 接入数据库、模型、存储、队列。

## 3. 拆分 stage3.py

| 当前职责 | 新文件 |
|---|---|
| `SQLAlchemyStateRepository` | `infrastructure/persistence/repositories/state_repository.py` |
| `SQLAlchemyRagRepository` | `infrastructure/persistence/repositories/rag_repository.py` |
| `SQLAlchemyAssessmentRepository` | `infrastructure/persistence/repositories/assessment_repository.py` |
| `SQLAlchemyPlanRepository` | `infrastructure/persistence/repositories/plan_repository.py` |
| `SQLAlchemyAuditSink` | `infrastructure/persistence/repositories/audit_repository.py` |
| Tutor 问答 | `application/tutor_service.py` |
| 测评 | `application/assessment_service.py` |
| 重规划 | `application/planning_service.py` |
| 学习任务 | `application/learning_service.py` |
| 文档生命周期 | `application/document_service.py` + `workers/document_tasks.py` |

### 工作流边界

```text
LangGraph → WorkflowResult(answer, citations, state_changes, plan_proposal, audit_events)
          → Application Service 校验、统一事务
          → Repository 保存
          → commit
```

- Repository 禁止 `commit()`，最多 `flush()`。
- Application Service 才决定提交/回滚。
- LangGraph 不持有 SQLAlchemy Session。

## 4. JWT 与资源归属校验

### Token 策略

- Access Token：15 分钟，前端内存。
- Refresh Token：7–30 天，`HttpOnly + Secure Cookie`。
- 数据库保存刷新令牌哈希、过期和撤销状态。

新增：

```text
users: password_hash, token_version, role
auth_sessions: id, user_id, refresh_token_hash, expires_at, revoked_at, created_at
```

### Principal

```python
@dataclass(frozen=True)
class Principal:
    user_id: str
    session_id: str
    role: str
```

所有 Router 仅从 `Principal` 获取 `user_id`，客户端不得传 `user_id`。

### 归属查询

- `goal_repository.get_owned(goal_id, user_id)`
- `task_repository.get_owned(task_id, user_id)`
- `assessment_repository.get_owned(assessment_id, user_id)`
- `document_repository.get_visible(document_id, user_id)`

私有资源查询不到统一返回 `404`，避免泄露资源存在性。

## 5. 真实 RAG 链路

### 入库

```text
上传 → MinIO → Document + OutboxEvent（同一事务）
    → Celery 解析/清洗/切分/Embedding/索引
    → ready 或 failed
```

### 查询

```text
用户问题 → 可见性过滤 → Dense Top20 + Keyword Top20
        → RRF → 可选 rerank → Top4-6 上下文
        → LLM 基于引用回答 → 返回答案与引用
```

### 核心数据字段

- `documents`：`status`、`parse_error_code`、`parser_version`、`index_version`、`retry_count`、`content_hash`
- `document_chunks`：`embedding VECTOR(n)`、`fts tsvector`、`heading_path`、页码、`embedding_model`、`is_active`
- `outbox_events`：`event_type`、`payload_json`、`status`、`attempts`、`available_at`

### 状态协议

```json
{
  "mode": "live | degraded | unavailable",
  "retrieval_status": "grounded | no_context | failed",
  "citations": [],
  "degraded_reason": null
}
```

禁止没有检索依据却伪造引用；禁止哈希向量伪装语义检索；禁止离线模拟回答不标记。

## 6. 迁移顺序

1. **Phase 0**：补权限、检索可见性、文档状态机的基线测试。
2. **Phase 1**：JWT、Session、Principal、`get_owned/get_visible`。
3. **Phase 2**：先仓储、再 Application Service、最后 Router/Worker，逐步拆掉 `stage3.py`。
4. **Phase 3**：LangGraph 只产出动作，不直接提交数据库；Plan/Snapshot 增加乐观锁。
5. **Phase 4**：Outbox、真实 Embedding、pgvector、混合检索、可验证引用。
6. **Phase 5**：集成测试、E2E、CI、监控。

## 7. 首批 PR

### PR 1：认证与资源隔离

- `pyproject.toml`
- `.env.example`
- `backend/app/models.py`
- `backend/alembic/versions/<auth_migration>.py`
- `backend/app/core/security.py`
- `backend/app/infrastructure/auth/*`
- `backend/app/api/v1/auth.py`
- `backend/app/api/deps.py`
- `tests/test_auth.py`
- `tests/test_resource_ownership.py`

### PR 2：拆分 `stage3.py`

- `backend/app/application/*_service.py`
- `backend/app/infrastructure/persistence/repositories/*`
- `backend/app/core/uow.py`
- `backend/app/workers/document_tasks.py`
- `src/adaptive_tutor/phase2/engine.py`
- 迁移完成后删除 `backend/app/services/stage3.py`

### PR 3：真实 RAG

- `backend/app/models.py`
- `backend/alembic/versions/<rag_vector_outbox_migration>.py`
- `backend/app/infrastructure/rag/*`
- `backend/app/application/document_service.py`
- `backend/app/workers/document_tasks.py`
- `tests/integration/test_rag_visibility.py`
- `tests/integration/test_document_indexing.py`
- `tests/integration/test_rag_retrieval.py`

## 8. 验收标准

1. 私有接口无 Token 返回 `401`。
2. 用户 A 读取用户 B 的私有资源返回 `404`。
3. 工作流不直接 `commit`，也不持有数据库 Session。
4. `stage3.py` 不再承载业务实现。
5. 外部模型或 Embedding 不可用时返回明确状态。
6. 文档只有完整索引成功后才为 `ready`。
7. 有知识库依据的回答可定位至文档、页码或标题路径。
8. 其他用户文档不出现在当前用户检索结果中。

## 9. 风险与回滚

`0007`、`0009` 的历史重复 active session/plan 收敛，以及 `0010` 的 pending-document Outbox 回填均是前向数据修复。downgrade 只移除对应索引，不会恢复被收敛的重复状态，也不会删除已回填的运维事件。

| 风险 | 控制 | 回滚 |
|---|---|---|
| JWT 切换会话失效 | 渐进式切换、刷新流程测试 | 回滚 API 版本，不回退字段 |
| 拆 `stage3.py` 行为变化 | 先做回归测试，保留兼容门面 | 按接口切回旧门面 |
| Embedding 更新向量不兼容 | 记录模型与索引版本 | 切换旧索引版本 |
| 异步任务重复 | Outbox 唯一键与任务幂等 | 重试失败事件 |
| 并发重规划覆盖 | revision/乐观锁 | 返回冲突并重读最新数据 |
