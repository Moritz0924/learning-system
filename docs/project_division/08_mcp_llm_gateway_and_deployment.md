# 08 MCP、LLM Gateway 与部署分工书

> 返回：[项目分工索引](00_project_division_index.md)  
> 来源：[总架构文档](../../adaptive_private_tutor_v1_architecture.md)

## 模块目标

提供统一模型接入、只读联网检索工具和本地部署能力，保证 Agent 使用模型和外部资料时可控、可审计、可复现。

## V1 必须完成

- 提供统一 LLM Gateway，支持 OpenAI-compatible API。
- 提供 MCP 只读联网工具 `search_official_learning_sources()`。
- 限制 MCP 只搜索白名单官方文档或可信来源。
- 记录工具调用到 `tool_calls`。
- 提供 Docker Compose 部署方案，包含前端、后端、PostgreSQL、pgvector、Redis、Celery Worker、MinIO。

## 明确不负责

- 不实现 Agent 决策逻辑。
- 不写用户学习状态。
- 不解析用户上传文档。
- 不让 MCP 自动爬取全网资料。
- 不允许外部网页直接写入长期记忆或状态知识库。

## 上游输入

- Agent 发起的模型请求。
- Agent 或 RAG 发起的官方文档检索请求。
- 部署环境变量和模型服务配置。

## 下游输出

- 模型响应。
- 官方来源检索结果。
- 工具调用审计记录。
- Docker Compose 运行环境。

## 对接接口/数据表/状态字段

| 类型 | 名称 | 对接说明 |
|---|---|---|
| 工具 | `search_official_learning_sources()` | 只读官方资料检索 |
| 输出字段 | `title`、`url`、`snippet`、`published_at`、`retrieved_at`、`source_level` | MCP 返回结构 |
| 表 | `tool_calls` | 工具调用审计 |
| 网关 | LLM Gateway | 统一模型调用入口 |
| 部署 | Docker Compose | V1 本地部署 |

## 边界规则

1. MCP 工具只读，不写用户数据。
2. 外部网页内容必须按不可信输入处理。
3. 所有工具调用必须记录 `tool_calls`。
4. MCP 结果必须展示来源。
5. `published_at` 可为空，`retrieved_at` 必须记录检索时间。
6. 模型调用必须经 LLM Gateway，不允许前端直连模型。

## 交付物

- LLM Gateway 接入说明。
- MCP 工具 Schema。
- 白名单域名配置说明。
- Docker Compose 服务清单。
- 环境变量模板。
- 工具调用审计规则。

## 验收标准

- Agent 能通过 LLM Gateway 调用模型。
- MCP 能返回官方来源检索结果，并包含 `retrieved_at`。
- 工具调用能写入 `tool_calls`。
- 前端不能直接调用模型。
- Docker Compose 能启动 V1 所需基础服务。

## 依赖模块

- 下游依赖：[LangGraph 与 Agent 编排](04_langgraph_and_agents.md)
- 下游依赖：[RAG 与文档入库](05_rag_document_ingestion.md)
- 下游依赖：[后端 API 与学习域服务](02_backend_api_and_learning_domain.md)

