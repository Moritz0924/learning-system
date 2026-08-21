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
    "ci95_high": 0.157531864411685,
    "ci95_low": -0.059997593971769625,
    "mean_delta": 0.06024377877737288,
    "resamples": 1000
  }
}
```
