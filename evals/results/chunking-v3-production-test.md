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
    "ci95_high": 0.24685638554324135,
    "ci95_low": -0.2723972209783895,
    "mean_delta": -0.04621127026409795,
    "resamples": 1000
  }
}
```
