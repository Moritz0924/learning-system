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
    "ci95_high": 0.1503592275266562,
    "ci95_low": -0.09875950389080394,
    "mean_delta": 0.028120478853333852,
    "resamples": 1000
  },
  "C_minus_A": {
    "ci95_high": 0.19998363365321106,
    "ci95_low": -0.07643671963138632,
    "mean_delta": 0.06423461082396047,
    "resamples": 1000
  },
  "C_minus_B": {
    "ci95_high": 0.16704538583202969,
    "ci95_low": -0.08193390433948065,
    "mean_delta": 0.036114131970626615,
    "resamples": 1000
  },
  "D_minus_A": {
    "ci95_high": 0.10557637679373595,
    "ci95_low": -0.14894522580247435,
    "mean_delta": -0.021671540774971666,
    "resamples": 1000
  },
  "E_minus_A": {
    "ci95_high": 0.2024805645369403,
    "ci95_low": -0.05542188698684645,
    "mean_delta": 0.07767196482822118,
    "resamples": 1000
  },
  "E_minus_C": {
    "ci95_high": 0.053317526957291486,
    "ci95_low": -0.02539917706412288,
    "mean_delta": 0.013437354004260715,
    "resamples": 1000
  },
  "E_minus_D": {
    "ci95_high": 0.20824901124905132,
    "ci95_low": 0.0011043115674002236,
    "mean_delta": 0.09934350560319286,
    "resamples": 1000
  },
  "P_minus_A": {
    "ci95_high": 0.3640314368047246,
    "ci95_low": 0.013936933365010968,
    "mean_delta": 0.1937710788144196,
    "resamples": 1000
  }
}
```
