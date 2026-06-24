# 01 产品、课程与入学诊断分工书

> 返回：[项目分工索引](00_project_division_index.md)  
> 来源：[总架构文档](../../adaptive_private_tutor_v1_architecture.md)

## 模块目标

定义 V1 的产品边界、AI 应用开发课程知识图谱、入学诊断题和个性化起点。该模块负责回答“用户应该从哪里开始学、为什么从这里开始、哪些知识需要补齐”。

## V1 必须完成

- 固定 V1 学习领域为 AI 应用开发，不扩展到考研、语言学习或其他学科。
- 设计内置学习路径：Python 工程基础、FastAPI、LLM API、Prompt Engineering、LangChain、RAG、LangGraph、Agent 长期记忆、项目部署与验收。
- 为每个知识点定义名称、前置依赖、学习目标、推荐资料、练习类型、验收方式、掌握阈值和预计学习时长。
- 设计入学诊断问卷和基础测验，覆盖目标、截止日期、每周时间、基础水平、学习偏好和关键前置能力。
- 输出个性化起点：`baseline_summary`、`entry_node_id`、`knowledge_gaps`、`initial_mastery`、`evidence_json`。

## 明确不负责

- 不实现 FastAPI 接口和数据库写入。
- 不实现 Agent 推理、测验评分和计划重排。
- 不解析用户上传资料，不维护 RAG 文档库。
- 不负责前端页面视觉和交互实现。

## 上游输入

- 用户目标、截止日期、每周可投入时间。
- 用户自评基础和学习偏好。
- 入学诊断答题结果。
- V1 产品边界和课程范围。

## 下游输出

- 课程图谱种子数据。
- 入学诊断题库与评分规则。
- 个性化起点结构化结果。
- 初始掌握度建议。
- 推荐学习入口知识点。

## 对接接口/数据表/状态字段

| 类型 | 名称 | 对接说明 |
|---|---|---|
| API | `POST /api/onboarding/diagnosis` | 前端提交诊断结果，后端写入个性化起点 |
| 表 | `curricula` | 保存课程体系版本 |
| 表 | `knowledge_nodes` | 保存知识点 |
| 表 | `knowledge_edges` | 保存前置依赖 |
| 表 | `learning_goals` | 保存用户目标 |
| 表 | `baseline_diagnostics` | 保存个性化起点 |
| 状态字段 | `baseline_diagnostic_id` | 写入 `learning_state_snapshots` |

## 边界规则

1. 诊断结果必须可解释，不能只输出一个分数。
2. 所有推荐入口必须映射到 `knowledge_nodes`。
3. 诊断不能直接生成长期记忆，只能提供候选证据。
4. 初始掌握度是规则系统输入，不是最终事实。
5. 课程图谱变更必须记录版本，避免旧计划引用失效。

## 交付物

- AI 应用开发 V1 知识图谱清单。
- 入学诊断题与评分 Rubric。
- 个性化起点 JSON 示例。
- 课程节点与前置依赖种子数据说明。

## 验收标准

- 能基于一个新用户的目标、时间和答题结果生成明确的个性化起点。
- 每个个性化起点都包含知识缺口、推荐入口和证据说明。
- 所有推荐入口都能在知识图谱中找到对应节点。
- 输出能被 `POST /api/onboarding/diagnosis` 和 `baseline_diagnostics` 直接承接。

## 依赖模块

- 下游依赖：[后端 API 与学习域服务](02_backend_api_and_learning_domain.md)
- 下游依赖：[状态知识库与数据库](03_state_knowledge_base_and_database.md)
- 下游依赖：[测验、掌握度与计划调整](06_assessment_mastery_and_replanning.md)
- 下游依赖：[前端学习体验](07_frontend_learning_experience.md)

