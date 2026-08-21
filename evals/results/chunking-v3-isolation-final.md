# Hybrid Chunking V3 Ablation

- Phase: `isolation`
- Split: `development`
- Variants: `A, P, B, C, D, E`
- Retrieval: `vector_only`, top_n=`20`
- Promotion eligible: `False`

Offline outputs are algorithm and runner checks only; they are not Promotion Evidence.

D fixed threshold: `0.2` (development-only)

## Paired bootstrap

```json
{
  "B_minus_A": {
    "ci95_high": 0.16593976713991285,
    "ci95_low": -0.21865996117364772,
    "mean_delta": -0.023428192771620678,
    "resamples": 1000
  },
  "C_minus_A": {
    "ci95_high": 0.16593976713991285,
    "ci95_low": -0.21865996117364772,
    "mean_delta": -0.023428192771620678,
    "resamples": 1000
  },
  "C_minus_B": {
    "ci95_high": 0.0,
    "ci95_low": 0.0,
    "mean_delta": 0.0,
    "resamples": 1000
  },
  "D_minus_A": {
    "ci95_high": 0.16593976713991285,
    "ci95_low": -0.21865996117364772,
    "mean_delta": -0.023428192771620678,
    "resamples": 1000
  },
  "E_minus_A": {
    "ci95_high": 0.16593976713991285,
    "ci95_low": -0.21865996117364772,
    "mean_delta": -0.023428192771620678,
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
    "ci95_high": 0.16593976713991285,
    "ci95_low": -0.21865996117364772,
    "mean_delta": -0.023428192771620678,
    "resamples": 1000
  }
}
```
