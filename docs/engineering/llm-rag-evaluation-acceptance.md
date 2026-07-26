# Learning System LLM/RAG 评测基础设施验收

## 当前结论

```text
评测基础设施：已完成
Mock Smoke：已完成
真实 LLM/RAG 效果指标：未执行
原因：缺少用户显式授权、Provider 凭证和已确认成本预算
Mock Smoke 仅验证基础设施，不代表模型质量
```

## 范围与证据边界

本次交付覆盖 G0–G5 的离线基础设施：固定 Corpus、40 条数据集、完整 Corpus Gold Chunk 映射、生产检索与 LLM 共享计时路径、Engine 兼容 Adapter、检索/引用/格式/Grounding 评分器、独立 Judge、安全 CLI、成本估算、逐案例报告、Prompt 对比和 Mock Smoke。

自动评分能够判断 JSON 结构、引用 ID、引用是否来自本轮检索、文档与 Chunk 是否匹配、拒答格式和敏感字段。它不能把“ID 合法”等同于“语义支持”。没有独立 Judge 或人工复核时：

```text
citation_support_rate = null
unsupported_answer_rate = null
semantic denominator = 0
```

Human Override 会保留原始 Judge Verdict、原始理由、人工 Verdict、复核理由和 Reviewer，不覆盖审计证据。

## 安全验收

- Seed 和正式评测要求独立 `EVALUATION_DATABASE_URL`。
- 同一数据库的判断忽略凭证差异与驱动别名，默认禁止与 `DATABASE_URL` 共库。
- 正式评测只接受 PostgreSQL + pgvector。
- 远程 LLM、Embedding 和远程 Seed 均要求 `--allow-remote`。
- Dry Run 与 Mock Smoke 不调用 Provider。
- Strict Remote 的缺配置、HTTP 错误、超时和非法响应均失败，不使用 Offline Fallback。
- `--reset` 只删除 Manifest 中固定文档 ID。

## 数据验收

```text
总案例：40
Development/Test：24/16
有答案/无答案：35/5
single_source/paraphrase/multi_evidence：12/8/8
unanswerable/prompt_injection/multi_turn：5/4/3
Corpus 文档：5
```

Gold Chunk Map 由完整 Corpus 使用生产 `split_text(max_chars=500)` 生成，不执行 Top-K。每个 Evidence Group 至少映射一个确定性 Chunk；Corpus Hash 或 Chunking Config Hash 变化会使旧映射失效。

## Prompt Candidate 后续接受门槛

- 格式遵循率至少 95%；或相对 Baseline 提升至少 10 个百分点且最终至少 90%。
- 引用链有效率和引用语义支持率降幅均不得超过 2 个百分点。
- Candidate 无依据回答率不得高于 Baseline。
- Prompt-only 实验的 Hit@5、Evidence Recall@5、AllEvidenceHit@5 必须完全一致。
- 平均回答延迟增幅不得超过 15%，P95 端到端延迟增幅不得超过 20%。
- 任何语义指标缺少 Judge/人工分母时，结论为 `manual_review_required`，不能接受替换 Baseline。

## 多轮外部依赖

当前 `Phase2TutorEngine` 未配置持久 Checkpointer，`graph.invoke()` 未使用 `configurable.thread_id`，且历史未进入 LLM `conversation_context`，因此 3 条多轮案例按依赖缺失跳过。不得仅凭相同 Thread ID 或新增构造参数声称多轮能力已完成。

## 后续真实 Test repeat=3 命令

仅在用户确认独立数据库、凭证、调用量和成本预算后执行：

```powershell
.\.venv\Scripts\python.exe scripts\run-rag-evaluation.py `
  --dataset evals/datasets/learning_qa_v1.jsonl `
  --prompt evals/prompts/tutor_candidate_v2.txt `
  --split test `
  --metric-cutoffs 1 3 5 `
  --retrieval-limit 5 `
  --generation-context-k 5 `
  --repeat 3 `
  --allow-remote
```

本次没有执行上述命令。
