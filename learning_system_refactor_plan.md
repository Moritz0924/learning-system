# learning-system 项目架构重构方案

> 范围：`stage3.py` 拆分、JWT 与统一资源归属校验、真实 RAG 链路、迁移执行计划。
> 原则：模块化单体；先保持行为，再调整边界；生产环境禁止“伪成功”降级。

## 0. 执行进度（2026-07-18）

- `stage3.py` 已拆为 application service、persistence repository、基础设施 adapter 和兼容 facade；运行时代码不再直接依赖旧大文件实现。
- JWT 会话链路已经完成：私有 API 从 Bearer JWT 解析 `Principal`，Access Token 仅驻留前端内存，Refresh Token 使用 HttpOnly Cookie，并由 `auth_sessions`/`refresh_tokens` 保存会话状态和令牌哈希。资源仓储以 `principal.user_id` 绑定目标、任务、测验和文档；跨用户私有资源返回 `404`。
- 当前 Alembic 只有一个 head `20260716_0012`。`0011` 增加用户认证身份字段，`0012` 增加会话与刷新令牌表；稳定基线阶段没有新增迁移。
- RAG 入库已具备 object storage、outbox、Celery worker/Beat scheduler、`parse_error`、OCR 和 PostgreSQL pgvector；讲师上下文已将可信结构化学习状态与不可信 RAG 正文分层。remote embedding/Brave 的真实 key smoke、告警与人工重放仍未闭合。
- 生产 readiness 会校验真实 provider mode、弱默认凭据并探测 PostgreSQL、Redis 和 MinIO；配置问题写入 `missing`，依赖不可达写入 `unavailable`。弱开发凭据下 `503 not_ready` 是预期结果。
- 计划、任务和测验状态机已具备单 active plan/session、幂等提交与旧 proposal 冲突控制；LangGraph 通过 `WorkflowAction` 把持久化交给 application 层。
- 前端已升级为 Next.js `16.2.10`、React/React DOM `19.2.7` 和 ESLint `9.39.5`，使用 flat config 与 ESLint CLI。`npm audit --omit=dev --audit-level=high` 为 `0 high / 0 critical`，仍有 Next.js 内嵌 PostCSS 的 `2 moderate`。
- 本地稳定基线：后端 `218 passed, 1 warning`；route contract、lint、production build 通过；Chromium E2E `4 passed`；2026-07-18 Compose 无缓存重建、7 服务、HTTP、唯一迁移 head 和非 root UID 验收通过。
- Draft PR 的五个 CI Job（backend、frontend quality、frontend E2E、PostgreSQL migration、Docker build）全部通过。CI 的 PostgreSQL Job 执行 `head → 20260626_0004 → head` 并校验 vector 扩展、关键表与索引。
- 下一阶段才开始版本化真实诊断模板/评分和原子幂等 onboarding；之后才进入真实 multipart 文件上传。当前 onboarding 前端仍提交硬编码诊断数据，当前文档入口仍是 JSON/Markdown 兼容链路。
- 当前明确不实施长期记忆、LangGraph checkpointer、多轮历史、混合 RAG、RRF/rerank 或新的 Agent。

## 1. 重构目标与边界

### 核心问题

| 问题 | 风险 | 目标 |
|---|---|---|
| `stage3.py` 职责混杂 | 改动牵连大、测试难 | 拆为用例服务、仓储、工作流和基础设施 |
| 历史 `X-User-Id` 占位认证（已修） | 用户身份可伪造 | 已由 JWT + Principal + 资源查询时归属过滤替换 |
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

- Access Token：默认 15 分钟，只驻留前端内存。
- Refresh Token：HttpOnly Cookie；生产环境要求 Secure，并受 idle/absolute TTL 约束。
- 数据库只保存刷新令牌哈希、父子轮换关系、使用/过期/撤销和重用检测状态。

已落地的数据结构：

```text
users: password_hash, normalized_email, token_version, role, password_changed_at, last_login_at
auth_sessions: id, user_id, status, idle_expires_at, absolute_expires_at, revoked_at, revoke_reason
refresh_tokens: id, session_id, token_hash, parent_token_id, replaced_by_token_id, used_at, revoked_at, reuse_detected_at
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
用户问题 → 用户/文档可见性过滤 → pgvector 相似度 TopK
        → 受限上下文与引用
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
5. **Phase 4**：Outbox、真实 Embedding、pgvector、可验证引用；混合检索/RRF/rerank 延后且不属于当前任务。
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

`0007`、`0009` 的历史重复 active session/plan 收敛，以及 `0010` 的 pending-document Outbox 回填均是前向数据修复。downgrade 只移除对应索引，不会恢复被收敛的重复状态，也不会删除已回填的运维事件。`0011` downgrade 会丢弃用户认证字段，`0012` downgrade 会删除会话与刷新令牌数据；生产回滚前必须先评估登录中断与数据保留要求。

| 风险 | 控制 | 回滚 |
|---|---|---|
| JWT 切换会话失效 | 渐进式切换、刷新流程测试 | 回滚 API 版本，不回退字段 |
| 拆 `stage3.py` 行为变化 | 先做回归测试，保留兼容门面 | 按接口切回旧门面 |
| Embedding 更新向量不兼容 | 记录模型与索引版本 | 切换旧索引版本 |
| 异步任务重复 | Outbox 唯一键与任务幂等 | 重试失败事件 |
| 并发重规划覆盖 | revision/乐观锁 | 返回冲突并重读最新数据 |
