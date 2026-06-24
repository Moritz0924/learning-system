# 02 后端 API 与学习域服务分工书

> 返回：[项目分工索引](00_project_division_index.md)  
> 来源：[总架构文档](../../adaptive_private_tutor_v1_architecture.md)

## 模块目标

实现 FastAPI 后端和学习域服务，承接前端请求、执行参数校验、调用领域规则、读写业务表，并为 Agent、状态知识库和前端提供稳定接口。

## V1 必须完成

- 提供用户、学习目标、入学诊断、当前状态、今日任务、学习会话、测验和计划重排的 API。
- 负责 `learning_goals`、`learning_plans`、`plan_tasks`、`learning_sessions`、`learning_events` 的领域逻辑。
- 调用状态知识库刷新 `learning_state_snapshots`。
- 调用 LangGraph 工作流，但不在 API 层写 Agent 决策逻辑。
- 支持流式响应给讲师页，但不让前端直接调用模型。

## 明确不负责

- 不决定用户是否掌握知识点。
- 不直接实现 Agent 提示词、RAG 检索算法或测验评分 Rubric。
- 不直接解析 PDF、Markdown 或图片 OCR。
- 不负责前端状态展示和页面交互。

## 上游输入

- 前端提交的目标、诊断、任务完成、学习会话、测验答案和手动重排请求。
- LangGraph 返回的结构化业务结果。
- 测验模块返回的掌握度变化和计划调整建议。

## 下游输出

- 当前学习状态 API 响应。
- 新建或更新后的计划、任务、学习记录和事件。
- 传给 LangGraph 的 `TutorState` 初始上下文。
- 写入状态知识库所需的结构化事实。

## 对接接口/数据表/状态字段

| 类型 | 名称 | 对接说明 |
|---|---|---|
| API | `GET /api/state/current` | 返回当前状态快照 |
| API | `POST /api/onboarding/diagnosis` | 接收入学诊断并初始化状态 |
| API | `POST /api/assessments/phase` | 创建或更新阶段测验状态 |
| API | `POST /api/plans/replan` | 触发计划重排 |
| 表 | `learning_plans` | 计划版本和状态 |
| 表 | `plan_tasks` | 今日任务、复习、练习、测验任务 |
| 表 | `learning_sessions` | 学习会话 |
| 表 | `learning_events` | 学习证据事件 |
| 状态字段 | `active_plan_id`、`active_plan_version`、`current_state` | 写入状态快照 |

## 边界规则

1. API 层只负责鉴权、参数校验、接口编排和响应，不写 Agent 决策。
2. 所有用户状态操作必须按 `user_id + goal_id` 隔离。
3. 后端可以调用 `persist` 或学习域服务刷新状态，但不能绕过领域规则直接改掌握度。
4. 所有计划版本变更都必须能追溯到 `plan_adjustments`。
5. 前端请求不得直接暴露底层表结构，API 返回面向页面的聚合数据。

## 交付物

- FastAPI 路由草案和 Pydantic Schema。
- 学习域服务接口说明。
- API 与业务表读写映射。
- 错误码和鉴权规则说明。

## 验收标准

- 前端可以通过 API 完成创建目标、提交诊断、读取当前状态、查看今日任务、提交测验和触发重排。
- API 返回的当前状态来自 `learning_state_snapshots` 或其领域服务聚合结果。
- API 层没有直接模型调用，也没有 Agent 决策逻辑。
- 所有会改变计划或掌握度的接口都留下可审计业务记录。

## 依赖模块

- 上游依赖：[产品、课程与入学诊断](01_product_curriculum_and_diagnosis.md)
- 强依赖：[状态知识库与数据库](03_state_knowledge_base_and_database.md)
- 下游对接：[LangGraph 与 Agent 编排](04_langgraph_and_agents.md)
- 下游对接：[前端学习体验](07_frontend_learning_experience.md)

