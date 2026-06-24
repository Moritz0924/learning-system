# 06 测验、掌握度与计划调整分工书

> 返回：[项目分工索引](00_project_division_index.md)  
> 来源：[总架构文档](../../adaptive_private_tutor_v1_architecture.md)

## 模块目标

实现 V1 学习闭环中的验收、掌握度更新和计划调整规则。该模块负责把学习证据转化为可解释的分数变化和计划变化。

## V1 必须完成

- 支持每日 3～5 道小题、每周 10～15 道综合题、阶段 Mini Project 和阶段测验状态。
- 持久化测验事实到 `assessments` 和 `assessment_items`。
- 记录作答到 `assessment_attempts` 和 `assessment_answers`。
- 按规则更新 `mastery_records`。
- 根据观察者决策生成计划调整，并写入 `plan_adjustments`。
- 更新 `phase_assessment_states` 和 `learning_state_snapshots`。

## 明确不负责

- 不运行用户任意代码。
- 不直接生成课程知识图谱。
- 不负责前端测验页面实现。
- 不直接调用外部网页资料作为评分事实。

## 上游输入

- 当前计划、任务、知识点范围和掌握度快照。
- 用户作答和学习证据。
- 讲师 Agent 生成的小练习。
- RAG 引用片段。
- 观察者 Agent 的结构化判断。

## 下游输出

- 测验草稿和题目。
- 测验得分、错因标签和反馈。
- 掌握度变化。
- 阶段测验状态。
- 计划调整记录和可解释理由。

## 对接接口/数据表/状态字段

| 类型 | 名称 | 对接说明 |
|---|---|---|
| API | `POST /api/assessments/phase` | 创建或更新阶段测验状态 |
| API | `POST /api/plans/replan` | 触发计划调整 |
| 表 | `assessments` | 测验事实 |
| 表 | `assessment_items` | 题目 |
| 表 | `assessment_attempts` | 作答记录 |
| 表 | `assessment_answers` | 单题答案和评分依据 |
| 表 | `mastery_records` | 掌握度 |
| 表 | `phase_assessment_states` | 阶段测验状态 |
| 表 | `plan_adjustments` | 计划调整审计 |
| 状态字段 | `mastery_summary`、`phase_assessment_state_id`、`latest_plan_adjustment_id` | 写入状态快照 |

## 边界规则

1. 所有掌握度输入先归一化到 `0～100`。
2. 最终掌握度必须 clamp 到 `0～100`。
3. 缺失数据使用最近有效值或中性默认值，并降低置信度。
4. 遗忘衰减最大扣分为 15，默认衰减系数为 0.6。
5. LLM 可以给评分建议，但不能绕过规则直接修改 `mastery_records`。
6. 每次计划调整必须写入 `plan_adjustments`。
7. 前端展示调整差异必须使用 `change_summary` 和 `rationale_json`。

## 交付物

- 测验类型与题型规则。
- 评分 Rubric。
- 掌握度计算说明。
- 计划调整规则。
- `plan_adjustments` 差异展示字段约定。

## 验收标准

- 每日、每周和阶段测验都能被创建、提交、评分和持久化。
- 掌握度更新记录计算版本、更新前后分数、置信度、证据来源和缺失数据处理方式。
- 连续未完成、正确率低、表现优秀等场景能生成不同计划调整。
- 计划调整后能刷新状态快照，并被前端读取展示。

## 依赖模块

- 上游依赖：[产品、课程与入学诊断](01_product_curriculum_and_diagnosis.md)
- 强依赖：[状态知识库与数据库](03_state_knowledge_base_and_database.md)
- 对接模块：[LangGraph 与 Agent 编排](04_langgraph_and_agents.md)
- 下游依赖：[前端学习体验](07_frontend_learning_experience.md)

