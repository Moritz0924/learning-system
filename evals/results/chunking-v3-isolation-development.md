# Hybrid Chunking V3 Ablation

- Phase: `isolation`
- Split: `development`
- Variants: `A, P, B, C, D, E`
- Retrieval: `vector_only`, top_n=`20`
- Promotion eligible: `True`

Offline outputs are algorithm and runner checks only; they are not Promotion Evidence.

D fixed threshold: `0.2` (development-only)

## Paired bootstrap

```json
{
  "B_minus_A": {
    "ci95_high": 0.11936538350529899,
    "ci95_low": 0.027600276154107716,
    "mean_delta": 0.0746962210550781,
    "resamples": 1000
  },
  "C_minus_A": {
    "ci95_high": 0.11512278450670335,
    "ci95_low": 0.0033638362054262084,
    "mean_delta": 0.06061874584504069,
    "resamples": 1000
  },
  "C_minus_B": {
    "ci95_high": 0.025543867156605594,
    "ci95_low": -0.05788662191964322,
    "mean_delta": -0.014077475210037415,
    "resamples": 1000
  },
  "D_minus_A": {
    "ci95_high": 0.10773561462304385,
    "ci95_low": -0.002642287284785427,
    "mean_delta": 0.053837427422413965,
    "resamples": 1000
  },
  "E_minus_A": {
    "ci95_high": 0.1082167387415984,
    "ci95_low": 0.016855862115643157,
    "mean_delta": 0.0648212741514769,
    "resamples": 1000
  },
  "E_minus_C": {
    "ci95_high": 0.03906241625114533,
    "ci95_low": -0.02221181160313202,
    "mean_delta": 0.004202528306436207,
    "resamples": 1000
  },
  "E_minus_D": {
    "ci95_high": 0.04942601492561024,
    "ci95_low": -0.01871051419583081,
    "mean_delta": 0.010983846729062935,
    "resamples": 1000
  },
  "P_minus_A": {
    "ci95_high": 0.27019722017166437,
    "ci95_low": 0.1734420923351983,
    "mean_delta": 0.22251029764313532,
    "resamples": 1000
  }
}
```
