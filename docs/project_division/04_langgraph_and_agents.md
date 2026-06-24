# 04 LangGraph 与 Agent 编排分工书

> 返回：[项目分工索引](00_project_division_index.md)  
> 来源：[总架构文档](../../adaptive_private_tutor_v1_architecture.md)

## 模块目标

实现 LangGraph 状态流和四类 Agent 协作：规划 Agent、观察者 Agent、讲师 Agent、验收 Agent。该模块负责“何时调用哪个 Agent、如何产生结构化结果、如何交给领域服务持久化”。

## V1 必须完成

- 定义并使用 `TutorState`，包含 `state_snapshot`、`baseline_diagnostic`、`phase_assessment_state`、`plan_adjustment`。
- 实现 `load_context`、`diagnosis`、`retrieve_context`、`teacher`、`build_assessment`、`grade_assessment`、`observer`、`planner`、`memory_gate`、`persist` 等节点。
- 通过 `load_context` 统一读取状态知识库。
- 通过 `persist` 写入结构化业务结果和审计日志。
- 保证 Agent 输出可解释、可审计、可被后端和数据库承接。

## 明确不负责

- 不直接访问数据库表。
- 不直接修改掌握度、长期记忆或学习计划。
- 不解析文档、不生成向量、不管理对象存储。
- 不实现前端页面。

## 上游输入

- 后端传入的 `trigger_type`、`user_id`、`goal_id`、`thread_id` 和用户消息。
- 状态知识库返回的当前状态快照。
- RAG 返回的引用片段。
- 测验模块返回的评分结果和掌握度变化。

## 下游输出

- 讲师回答、引用、追问和练习。
- 测验草稿或评分反馈。
- 观察者决策：`keep`、`reduce`、`remediate`、`advance`。
- 计划补丁和 `plan_adjustment` 结构。
- 候选长期记忆和审计日志。

## 对接接口/数据表/状态字段

| 类型 | 名称 | 对接说明 |
|---|---|---|
| 状态 | `TutorState.state_snapshot` | 当前学习状态 |
| 状态 | `TutorState.baseline_diagnostic` | 个性化起点 |
| 状态 | `TutorState.phase_assessment_state` | 阶段测验状态 |
| 状态 | `TutorState.plan_adjustment` | 计划调整结果 |
| 节点 | `load_context` | 从状态知识库读取上下文 |
| 节点 | `persist` | 统一落库和审计 |
| 表 | `agent_runs` | 记录图运行 |
| 表 | `tool_calls` | 记录工具调用 |

## 边界规则

1. Agent 不直接写表，所有写入通过 `persist` 或学习域服务。
2. 规划 Agent 不能直接判定掌握度。
3. 观察者 Agent 不讲课，只判断是否偏离计划。
4. 讲师 Agent 不能直接修改计划、掌握度或长期记忆。
5. 验收 Agent 不能直接调整总计划。
6. 所有 Agent 输出必须包含足够结构化字段，不能只输出自然语言。

## 交付物

- LangGraph 状态图实现说明。
- `TutorState` 字段说明。
- 四类 Agent 的输入输出 Schema。
- Agent 提示词边界规则。
- `agent_runs` 和 `tool_calls` 审计写入说明。

## 验收标准

- 首次进入、学习提问、任务完成、测验到期、测验提交、手动重排都能路由到正确节点。
- 讲师回答能带 RAG 引用。
- 测验生成后能被持久化，而不是只返回临时内容。
- 计划调整能形成 `plan_adjustment` 并交给持久化节点。
- Agent 没有直接数据库写入路径。

## 依赖模块

- 强依赖：[状态知识库与数据库](03_state_knowledge_base_and_database.md)
- 强依赖：[RAG 与文档入库](05_rag_document_ingestion.md)
- 强依赖：[测验、掌握度与计划调整](06_assessment_mastery_and_replanning.md)
- 对接模块：[后端 API 与学习域服务](02_backend_api_and_learning_domain.md)
- 对接模块：[MCP、LLM Gateway 与部署](08_mcp_llm_gateway_and_deployment.md)

