# Learning QA LLM/RAG 离线评测

本目录提供 `learning-qa-v1` 的可重复、可比较、可审计评测基础设施。固定资产包括 5 篇教学语料、40 个案例、完整 Corpus 上生成的 Gold Chunk Map、Prompt A/B、自动评分器、可选独立 Judge、逐案例结果和延迟报告。

Mock Smoke 只验证 `seed → chunk map → retrieval → Phase2TutorEngine → mock LLM → graders → metrics → report → comparison` 管线；它不会调用远程 LLM、Embedding 或 Judge，且所有报告均标记 `quality_metrics_are_representative=false`。

## 数据基线

- Corpus：`evals/corpus/learning_qa_v1/`
- Dataset：`evals/datasets/learning_qa_v1.jsonl`
- Gold Map：`evals/generated/learning_qa_v1_chunk_map.json`
- Prompt Baseline：`evals/prompts/tutor_baseline_v1.txt`
- Prompt Candidate：`evals/prompts/tutor_candidate_v2.txt`
- 固定 JSON Envelope：`evals/prompts/evaluation_response_envelope_v1.txt`
- Judge Prompt：`evals/prompts/citation_judge_v1.txt`

数据集固定为 40 条：12 条单来源、8 条改写、8 条多证据、5 条无答案、4 条 Prompt Injection、3 条多轮；Development/Test 为 24/16。有答案 35 条，无答案 5 条。

## 本地验证

```powershell
.\.venv\Scripts\python.exe scripts\verify-evaluation-dataset.py `
  --dataset evals/datasets/learning_qa_v1.jsonl

.\.venv\Scripts\python.exe scripts\build-evaluation-chunk-map.py `
  --dataset evals/datasets/learning_qa_v1.jsonl `
  --output evals/generated/learning_qa_v1_chunk_map.json

$env:NO_PROXY="*"
.\.venv\Scripts\python.exe -m pytest tests/evaluation -q `
  --basetemp .pytest-tmp/evaluation
```

运行 5 案例 Mock Smoke：

```powershell
.\.venv\Scripts\python.exe scripts\run-rag-evaluation.py `
  --dataset evals/datasets/learning_qa_v1.jsonl `
  --prompt evals/prompts/tutor_candidate_v2.txt `
  --split development `
  --max-cases 5 `
  --mock `
  --metric-cutoffs 1 3 5 `
  --retrieval-limit 5 `
  --generation-context-k 5 `
  --repeat 1
```

## 安全边界

正式 Seed 和远程评测只读取 `EVALUATION_DATABASE_URL`，并要求 PostgreSQL + pgvector。该地址未配置时立即失败；它与 `DATABASE_URL` 指向同一主机、端口和数据库时默认失败，即使用户名、密码或 SQLAlchemy 驱动别名不同。只有显式设置 `EVALUATION_ALLOW_SHARED_DATABASE=true` 才能解除同库保护，不建议这样做。

远程 LLM 和远程 Embedding 都要求命令显式包含 `--allow-remote`。已有 API Key 不能绕过此门。Seed 使用远程 Embedding 时也要求该参数。Dry Run 不建立数据库连接，也不会访问 Provider。

```env
EVALUATION_DATABASE_URL=
EVALUATION_ALLOW_SHARED_DATABASE=false
EVALUATION_CORPUS_NAMESPACE=learning-qa-v1
```

Judge 必须使用独立配置；三项不完整时自动关闭，不能复用待评测模型配置：

```env
JUDGE_LLM_BASE_URL=
JUDGE_LLM_API_KEY=
JUDGE_LLM_MODEL=
```

当前生产 `Phase2TutorEngine` 尚未同时满足 Checkpointer、`configurable.thread_id`、历史进入 `conversation_context` 和跨 Engine 恢复四项能力，因此 3 个多轮案例保留在数据集中，但执行结果固定记录为 `skipped_dependency / persistent_conversation_unavailable`。

## Dry Run 与成本估算

以下命令不调用远程服务：

```powershell
.\.venv\Scripts\python.exe scripts\run-rag-evaluation.py `
  --dataset evals/datasets/learning_qa_v1.jsonl `
  --prompt evals/prompts/tutor_candidate_v2.txt `
  --split test `
  --metric-cutoffs 1 3 5 `
  --retrieval-limit 5 `
  --generation-context-k 5 `
  --repeat 3 `
  --dry-run `
  --estimate-cost
```

价格参数可通过 `--input-cost-per-million` 和 `--output-cost-per-million` 显式提供；未提供时只估算调用量和 Token，不虚构价格。

## 正式评测命令（以后由用户显式执行）

先确认独立数据库、凭证、预计调用量和成本预算，然后执行：

```powershell
$env:EVALUATION_DATABASE_URL="<evaluation-postgres-url>"
$env:LLM_BASE_URL="<provider>"
$env:LLM_API_KEY="<key>"
$env:LLM_MODEL="<model>"
$env:EMBEDDING_BASE_URL="<provider>"
$env:EMBEDDING_API_KEY="<key>"
$env:EMBEDDING_MODEL="<embedding-model>"

.\.venv\Scripts\python.exe scripts\seed-evaluation-corpus.py `
  --corpus evals/corpus/learning_qa_v1 `
  --reset `
  --allow-remote

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

有效 Prompt A/B 对比要求 Baseline 与 Candidate 使用相同 split 和 repeat。若 Candidate Test 使用 `repeat=3`，Baseline Test 也必须使用 `repeat=3`。比较命令：

```powershell
.\.venv\Scripts\python.exe scripts\compare-prompt-evaluations.py `
  --baseline evals/results/<baseline-run>/summary.json `
  --candidate evals/results/<candidate-run>/summary.json `
  --output evals/results/<comparison-run>/comparison.md
```

比较器会拒绝控制变量不一致的输入；语义 Judge 分母为零时不会给出虚假的接受结论。

## 结果产物

每次运行生成 `run.json`、`config.json`、`cases.jsonl`、两个 CSV、`summary.json`、`summary.md`、`failed-cases.jsonl` 和 `human-review.csv`。Test 集失败案例全部进入人工复核表，自动完成案例按 Run ID 确定性抽取至少 20%。Prompt 对比生成 `comparison.json` 和 `comparison.md`。

自动评分只认引用链是否合法。引用语义支持率和无依据回答率在没有 Judge 或人工结果时保持 `null`，并记录实际分母。
