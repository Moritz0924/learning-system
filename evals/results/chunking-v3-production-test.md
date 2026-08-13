# Hybrid Chunking V3 Ablation

- Phase: `production`
- Split: `test`
- Variants: `A, E`
- Retrieval: `vector_only`, top_n=`20`
- Promotion eligible: `False`

Offline outputs are algorithm and runner checks only; they are not Promotion Evidence.

## Paired bootstrap

```json
{
  "E_minus_A": {
    "ci95_high": 0.2024805645369403,
    "ci95_low": -0.05542188698684645,
    "mean_delta": 0.07767196482822118,
    "resamples": 1000
  }
}
```
