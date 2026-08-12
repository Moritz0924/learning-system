# Hybrid Chunking V3 Ablation

- Phase: `isolation`
- Split: `test`
- Variants: `A, P, B, C, D, E`
- Retrieval: `vector_only`, top_n=`20`
- Promotion eligible: `False`

Offline outputs are algorithm and runner checks only; they are not Promotion Evidence.

D fixed threshold: `0.2` (development-only)

## Paired bootstrap

```json
{
  "B_minus_A": {
    "ci95_high": 0.24685638554324135,
    "ci95_low": -0.2723972209783895,
    "mean_delta": -0.04621127026409795,
    "resamples": 1000
  },
  "C_minus_A": {
    "ci95_high": 0.24685638554324135,
    "ci95_low": -0.2723972209783895,
    "mean_delta": -0.04621127026409795,
    "resamples": 1000
  },
  "C_minus_B": {
    "ci95_high": 0.0,
    "ci95_low": 0.0,
    "mean_delta": 0.0,
    "resamples": 1000
  },
  "D_minus_A": {
    "ci95_high": 0.24685638554324135,
    "ci95_low": -0.2723972209783895,
    "mean_delta": -0.04621127026409795,
    "resamples": 1000
  },
  "E_minus_A": {
    "ci95_high": 0.24685638554324135,
    "ci95_low": -0.2723972209783895,
    "mean_delta": -0.04621127026409795,
    "resamples": 1000
  },
  "E_minus_C": {
    "ci95_high": 0.0,
    "ci95_low": 0.0,
    "mean_delta": 0.0,
    "resamples": 1000
  },
  "E_minus_D": {
    "ci95_high": 0.0,
    "ci95_low": 0.0,
    "mean_delta": 0.0,
    "resamples": 1000
  },
  "P_minus_A": {
    "ci95_high": 0.24685638554324135,
    "ci95_low": -0.2723972209783895,
    "mean_delta": -0.04621127026409795,
    "resamples": 1000
  }
}
```
