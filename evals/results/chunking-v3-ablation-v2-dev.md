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
    "ci95_high": 0.08937680874078546,
    "ci95_low": -0.02968758374885467,
    "mean_delta": 0.02951975314164007,
    "resamples": 1000
  },
  "C_minus_A": {
    "ci95_high": 0.07711960056015409,
    "ci95_low": -0.02553131842262673,
    "mean_delta": 0.027337844379552844,
    "resamples": 1000
  },
  "C_minus_B": {
    "ci95_high": 0.06174815406668378,
    "ci95_low": -0.06766183733116926,
    "mean_delta": -0.0021819087620872256,
    "resamples": 1000
  },
  "D_minus_A": {
    "ci95_high": 0.0725305493025036,
    "ci95_low": -0.03550807868759677,
    "mean_delta": 0.015175245805487108,
    "resamples": 1000
  },
  "E_minus_A": {
    "ci95_high": 0.10557802283132527,
    "ci95_low": -0.010344287866042261,
    "mean_delta": 0.050109339418694124,
    "resamples": 1000
  },
  "E_minus_C": {
    "ci95_high": 0.06208631281312962,
    "ci95_low": -0.010852248850344748,
    "mean_delta": 0.022771495039141276,
    "resamples": 1000
  },
  "E_minus_D": {
    "ci95_high": 0.09644173270757139,
    "ci95_low": -0.03079931432489627,
    "mean_delta": 0.03493409361320701,
    "resamples": 1000
  },
  "P_minus_A": {
    "ci95_high": 0.18420220883491142,
    "ci95_low": 0.051030985787218944,
    "mean_delta": 0.11668845468956077,
    "resamples": 1000
  }
}
```
