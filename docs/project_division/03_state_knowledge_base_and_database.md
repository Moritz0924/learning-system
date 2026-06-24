# 03 状态知识库与数据库分工书

> 返回：[项目分工索引](00_project_division_index.md)  
> 来源：[总架构文档](../../adaptive_private_tutor_v1_architecture.md)

## 模块目标

建设 PostgreSQL 上的结构化状态知识库和业务事实表，为所有 Agent、后端和前端提供一致的当前学习状态。状态知识库保存快照，事实来源仍以诊断、计划、任务、测验、掌握度和调整记录为准。

## V1 必须完成

- 建立用户、画像、目标、课程图谱、计划、任务、学习证据、掌握度、测验、RAG、长期记忆和审计相关表。
- 新增并维护 `learning_state_snapshots`、`baseline_diagnostics`、`phase_assessment_states`、`plan_adjustments`。
- 提供按 `user_id + goal_id` 隔离的当前状态读取能力。
- 保证状态快照可重建，不能成为唯一事实来源。
- 记录 Agent 运行和工具调用审计。

## 明确不负责

- 不决定学习计划怎么调整。
- 不计算掌握度业务规则。
- 不保存 RAG 原文以外的当前状态到向量库。
- 不直接向前端暴露底层表。

## 上游输入

- 入学诊断结果。
- 计划生成和计划调整结果。
- 学习事件、任务完成、测验结果。
- RAG 文档解析结果。
- Agent 运行日志和工具调用摘要。

## 下游输出

- 当前状态快照。
- 个性化起点记录。
- 阶段测验状态。
- 计划调整审计。
- 掌握度和证据查询结果。

## 对接接口/数据表/状态字段

| 类型 | 名称 | 对接说明 |
|---|---|---|
| 表 | `learning_state_snapshots` | 当前状态聚合快照 |
| 表 | `baseline_diagnostics` | 个性化起点 |
| 表 | `phase_assessment_states` | 阶段测验状态 |
| 表 | `plan_adjustments` | 计划调整审计 |
| 表 | `mastery_records` | 知识点掌握度 |
| 表 | `agent_runs`、`tool_calls` | Agent 和工具审计 |
| API | `GET /api/state/current` | 状态读取出口 |
| 状态字段 | `mastery_summary`、`current_state`、`generated_from` | 状态快照核心字段 |

## 边界规则

1. 状态快照必须按 `user_id + goal_id` 唯一。
2. Agent 只能读取状态快照，不直接写状态表。
3. 状态更新只能由学习域服务或 LangGraph `persist` 节点执行。
4. `learning_state_snapshots.generated_from` 必须记录快照来源。
5. `plan_adjustments` 是计划差异展示的唯一审计依据。
6. RAG 外部内容不得直接写入状态知识库。

## 交付物

- 数据表设计和迁移清单。
- 关键索引和唯一约束说明。
- 状态快照生成规则。
- 状态快照重建流程。
- 审计字段约定。

## 验收标准

- 能按 `user_id + goal_id` 读取唯一当前状态。
- 入学诊断后能写入 `baseline_diagnostics` 并刷新状态快照。
- 阶段测验后能更新 `phase_assessment_states`。
- 计划调整后能写入 `plan_adjustments` 并刷新状态快照。
- 状态快照可以从事实表重建。

## 依赖模块

- 上游依赖：[产品、课程与入学诊断](01_product_curriculum_and_diagnosis.md)
- 上游依赖：[后端 API 与学习域服务](02_backend_api_and_learning_domain.md)
- 上游依赖：[测验、掌握度与计划调整](06_assessment_mastery_and_replanning.md)
- 下游依赖：[LangGraph 与 Agent 编排](04_langgraph_and_agents.md)
- 下游依赖：[前端学习体验](07_frontend_learning_experience.md)

