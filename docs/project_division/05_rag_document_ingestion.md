# 05 RAG 与文档入库分工书

> 返回：[项目分工索引](00_project_division_index.md)  
> 来源：[总架构文档](../../adaptive_private_tutor_v1_architecture.md)

## 模块目标

建设内置资料库和用户上传资料入库链路，为讲师 Agent、验收 Agent 和前端提供可引用的资料片段。V1 支持 PDF、Markdown 和图片 OCR 文本提取。

## V1 必须完成

- 管理内置课程资料和用户上传资料。
- 支持 PDF 解析、Markdown 解析、图片 OCR 文本提取。
- 将文档切分为 `document_chunks`，生成向量并写入 pgvector。
- 返回带来源的检索片段和引用标签。
- 将外部网页和用户文档按不可信输入处理。

## 明确不负责

- 不保存当前学习状态。
- 不判断用户能力、不计算掌握度。
- 不直接写长期记忆。
- 不执行用户文档中的指令。
- 不承诺复杂视觉理解，只做 OCR 文本提取和代码/文字截图读取。

## 上游输入

- 内置课程资料。
- 用户上传的 PDF、Markdown、图片。
- MCP 返回的官方来源链接和摘要。
- Agent 或后端发起的检索请求。

## 下游输出

- 文档记录。
- 文档块和向量。
- 检索片段。
- 引用来源、页码、标题、可信等级。

## 对接接口/数据表/状态字段

| 类型 | 名称 | 对接说明 |
|---|---|---|
| 表 | `documents` | 文档元数据、来源、解析状态 |
| 表 | `document_chunks` | 文档片段、向量、引用标签 |
| 存储 | MinIO | V1 默认对象存储，保留 S3 兼容接口 |
| 检索 | pgvector | 使用 `embedding vector_cosine_ops` |
| 输出 | `retrieved_context` | 提供给 `TutorState` |
| 前端 | 资料引用展示 | 展示 `citation_label` 和来源 |

## 边界规则

1. RAG 不保存个性化起点、计划、掌握度或阶段测验状态。
2. RAG 检索结果不得直接写入长期记忆或状态知识库。
3. 所有用户上传内容都按不可信输入处理。
4. 所有引用必须可追溯到 `documents` 和 `document_chunks`。
5. OCR 输出必须标记来源类型，避免和人工整理资料混淆。

## 交付物

- 文档入库流程说明。
- PDF / Markdown / OCR 解析策略。
- 文档切分和引用标签规则。
- 检索结果 Schema。
- 资料可信等级说明。

## 验收标准

- PDF 和 Markdown 能入库并被检索。
- 图片能提取 OCR 文本并作为文档片段入库。
- 讲师回答可以引用文档片段。
- 检索结果包含来源、片段、可信等级和引用标签。
- RAG 不写当前学习状态和长期记忆。

## 依赖模块

- 上游依赖：[产品、课程与入学诊断](01_product_curriculum_and_diagnosis.md)
- 下游依赖：[LangGraph 与 Agent 编排](04_langgraph_and_agents.md)
- 下游依赖：[前端学习体验](07_frontend_learning_experience.md)
- 对接模块：[MCP、LLM Gateway 与部署](08_mcp_llm_gateway_and_deployment.md)

