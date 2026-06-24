# 自适应私人讲师系统 V1 项目分工索引

> 来源总架构：[adaptive_private_tutor_v1_architecture.md](../../adaptive_private_tutor_v1_architecture.md)

本文档是 V1 多模块协作入口。总架构文档继续作为唯一架构来源，本目录下的分工书用于拆分任务、明确边界、约定对接方式和验收标准。

---

## 模块清单

| 编号 | 分工书 | 核心职责 | 建议阶段 |
|---|---|---|---|
| 01 | [产品、课程与入学诊断](01_product_curriculum_and_diagnosis.md) | 产品边界、知识图谱、诊断题、个性化起点 | 阶段 1 |
| 02 | [后端 API 与学习域服务](02_backend_api_and_learning_domain.md) | FastAPI、学习计划、任务、学习记录、领域服务 | 阶段 1 |
| 03 | [状态知识库与数据库](03_state_knowledge_base_and_database.md) | PostgreSQL 表、状态快照、审计数据、共享状态层 | 阶段 1 |
| 04 | [LangGraph 与 Agent 编排](04_langgraph_and_agents.md) | 图状态流、规划/观察者/讲师/验收 Agent | 阶段 2 |
| 05 | [RAG 与文档入库](05_rag_document_ingestion.md) | 资料库、PDF/Markdown/图片 OCR、切分、引用 | 阶段 2 |
| 06 | [测验、掌握度与计划调整](06_assessment_mastery_and_replanning.md) | 日测/周测/阶段测验、掌握度、计划重排 | 阶段 2 |
| 07 | [前端学习体验](07_frontend_learning_experience.md) | 入学诊断、今日学习、讲师、测验、进度页面 | 阶段 3 |
| 08 | [MCP、LLM Gateway 与部署](08_mcp_llm_gateway_and_deployment.md) | 只读联网工具、模型网关、Docker Compose、安全约束 | 阶段 3 |

---

## 开发顺序

```text
阶段 1：核心状态与学习闭环
    01 产品、课程与入学诊断
    02 后端 API 与学习域服务
    03 状态知识库与数据库

阶段 2：Agent、RAG 与测验持久化
    04 LangGraph 与 Agent 编排
    05 RAG 与文档入库
    06 测验、掌握度与计划调整

阶段 3：前端、可解释性与完整演示
    07 前端学习体验
    08 MCP、LLM Gateway 与部署
```

阶段可以动态调整耗时，但不得跳过阶段验收。每个阶段的输出必须能被下一阶段稳定读取和复用。

---

## 统一对接原则

1. 用户相关状态统一按 `user_id + goal_id` 隔离。
2. 当前状态通过 `learning_state_snapshots` 或 `GET /api/state/current` 读取。
3. Agent 不直接写数据库表，只通过学习域服务或 LangGraph `persist` 节点落库。
4. RAG 只提供资料片段、引用来源和 OCR 文本，不保存当前学习状态。
5. 计划调整必须写入 `plan_adjustments`，前端展示调整差异以该表为依据。
6. 测验事实存入 `assessments` / `assessment_items`，阶段状态存入 `phase_assessment_states`。
7. 前端不计算掌握度、不直接调用模型、不直接拼装底层业务表。
8. MCP 只读联网工具不得写入用户数据、状态知识库或长期记忆。

---

## 跨模块接口

| 对接点 | 上游 | 下游 | 交付内容 |
|---|---|---|---|
| 入学诊断 | 01、07 | 02、03、06 | 用户答题、自评、诊断分、推荐入口知识点 |
| 状态快照 | 02、03、06 | 04、07 | 当前目标、计划、掌握度、阶段测验、最近调整 |
| RAG 检索 | 05、08 | 04、07 | 文档片段、引用来源、可信等级 |
| 测验评分 | 04、06 | 03、07 | 得分、错因、反馈、掌握度变化 |
| 计划调整 | 04、06 | 02、03、07 | 计划补丁、调整证据、前后差异、可解释理由 |

---

## 分工书统一模板

每份模块分工书必须包含：

```text
模块目标
V1 必须完成
明确不负责
上游输入
下游输出
对接接口/数据表/状态字段
边界规则
交付物
验收标准
依赖模块
```

