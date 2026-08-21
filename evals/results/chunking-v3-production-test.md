# Hybrid Chunking V3 Ablation

- Phase: `production`
- Split: `test`
- Variants: `A, B`
- Retrieval: `production_orchestrator`, top_n=`20`
- Promotion eligible: `True`

Offline outputs are algorithm and runner checks only; they are not Promotion Evidence.

## Paired bootstrap

```json
{
  "B_minus_A": {
    "ci95_high": 0.1856004412865606,
    "ci95_low": 0.05075427105538234,
    "mean_delta": 0.11282326138731051,
    "resamples": 1000
  }
}
```
