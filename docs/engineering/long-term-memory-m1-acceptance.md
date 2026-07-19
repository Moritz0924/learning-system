# 长期记忆 M1 验收记录

## 提交链与范围

分支：`codex/long-term-memory-m1`。

| 提交 | 主题 |
| --- | --- |
| `51d6288c2dd761f4a88119dc8615d6742e86c59d` | `feat(memory): define structured long-term memory contracts` |
| `5c24bd6a161ca3fb35551fba3f49ad5260f5a01f` | `feat(memory): add long-term memory persistence schema` |
| `9a9e354a8f1fcd80f1ec4403f6a72d7893127811` | `feat(memory): implement transactional memory repository` |
| `c52cb4eae02afa93a053af99d5b5e4e017d64956` | `test(memory): enforce ownership idempotency and rollback boundaries` |
| 本文档所在提交（当前/最终提交） | `docs(memory): record M1 acceptance evidence` |

M1 仅提供结构化长期记忆的契约、数据库迁移与仓储持久化。未实现 API、router、应用服务、LangGraph/TutorContext、前端、向量记忆、checkpointer、多轮聊天、自动记忆捕获或 LLM 上下文注入。

## 最终数据库事实

Alembic 唯一 head 为 `20260718_0015`。`memories` 表具有以下列及可空性：

| 列 | 可空 |
| --- | --- |
| `id`、`user_id`、`memory_type`、`schema_version`、`content_json`、`content_hash`、`source_kind`、`source_metadata`、`importance`、`confidence`、`is_enabled`、`idempotency_key`、`created_at`、`updated_at` | 否 |
| `goal_id`、`source_ref_id`、`expires_at`、`disabled_at`、`disabled_reason` | 是 |

- `fk_memories_user_goal` 将 `memories[user_id, goal_id]` 约束到 `learning_goals[user_id, id]`，阻止跨用户目标归属。
- `uq_memories_user_idempotency` 对 `(user_id, idempotency_key)` 唯一；`uq_learning_goals_user_id_id` 支持上述复合外键。
- 检查约束：`ck_memories_importance_range`、`ck_memories_confidence_range`。
- 索引：`ix_memories_user_scope_type`（`user_id, goal_id, memory_type`）与 `ix_memories_user_enabled_expiry`（`user_id, is_enabled, expires_at`）。

## 已观察到的本地证据

| 门禁 | 命令/证据 | 结果 |
| --- | --- | --- |
| 记忆测试 | `.\scripts\test.ps1 -SkipCompile tests\memory` | `154 passed, 1 warning in 17.48s`；warning 为已知 Starlette/httpx 弃用提示。 |
| 完整后端测试 | `.\scripts\test.ps1` | `449 passed, 1 warning in 93.99s`；warning 同上。 |
| Alembic head | `.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini heads` | `20260718_0015 (head)`，单 head。 |
| 一次性 SQLite 迁移周期 | 在忽略的 `.tmp/long-term-memory-m1-acceptance.db` 上依次执行 `upgrade head`、`current`、`downgrade 20260718_0014`、`upgrade head`、`current` | 命令退出码为 `0`；两次 `current` 均输出 `20260718_0015 (head)`。未操作跟踪的 `adaptive_tutor_stage1.db`。 |
| Compose 配置 | `docker compose config` | 退出码 `0`。 |
| Python 编译 | `.\.venv\Scripts\python.exe -m compileall scripts\verify-postgres-migrations.py -q` | 退出码 `0`。 |
| Git 差异卫生 | `git diff --check` | 退出码 `0`。 |

## 未验证项

- PostgreSQL 验证脚本：**NOT VERIFIED**。本地未设置 `DATABASE_URL`，故未连接 PostgreSQL、未观察 pgvector/约束检查或事务内跨用户插入拒绝；脚本已要求外层事务回滚，并在嵌套 SAVEPOINT 中仅将 psycopg 诊断出的 `fk_memories_user_goal` 约束 `IntegrityError` 视为预期拒绝，其他约束或无法识别约束名的 `IntegrityError` 会原样抛出。
- GitHub Actions 的 `backend-tests`、`frontend-quality`、`frontend-e2e`、`migration-postgres`、`docker-build`：均为 **NOT VERIFIED**。最终提交尚未推送，未观察到其 CI job 结果。

## 目录外集成说明

M1 的集成改动包含原目录之外的 `backend/app/db.py`。其中仅为 SQLite 顶层 SAVEPOINT 增加 `BEGIN` hook：Python 3.11 的 sqlite3 在该边界不自动启动外层事务，hook 确保释放 SAVEPOINT 不会独立提交调用方事务。回归证据包括 `tests/memory/test_memory_repository.py::test_caller_rollback_makes_created_row_disappear`、`tests/memory/test_memory_idempotency.py::test_unique_race_recovers_inside_savepoint_and_keeps_outer_transaction_usable` 与 `tests/memory/test_memory_idempotency.py::test_outer_rollback_removes_newly_flushed_memory`；它们包含在上述 `154 passed` 和 `449 passed` 结果中。
