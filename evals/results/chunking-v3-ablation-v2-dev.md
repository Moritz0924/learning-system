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
    "ci95_high": 0.053511457252931195,
    "ci95_low": -0.052006980271295314,
    "mean_delta": 0.003284373069298472,
    "resamples": 1000
  },
  "C_minus_A": {
    "ci95_high": 0.09973276931662542,
    "ci95_low": -0.0399991183138242,
    "mean_delta": 0.030638704350935596,
    "resamples": 1000
  },
  "C_minus_B": {
    "ci95_high": 0.08449271044065088,
    "ci95_low": -0.030600389753145524,
    "mean_delta": 0.02735433128163713,
    "resamples": 1000
  },
  "D_minus_A": {
    "ci95_high": 0.06369564597058469,
    "ci95_low": -0.04718997546820086,
    "mean_delta": 0.0081633553325786,
    "resamples": 1000
  },
  "E_minus_A": {
    "ci95_high": 0.10734362720771531,
    "ci95_low": -0.02886097664852605,
    "mean_delta": 0.03865692414929701,
    "resamples": 1000
  },
  "E_minus_C": {
    "ci95_high": 0.03416968625301535,
    "ci95_low": -0.015083483827952077,
    "mean_delta": 0.008018219798361408,
    "resamples": 1000
  },
  "E_minus_D": {
    "ci95_high": 0.08731537866161362,
    "ci95_low": -0.026595366514493235,
    "mean_delta": 0.030493568816718407,
    "resamples": 1000
  },
  "P_minus_A": {
    "ci95_high": 0.15885270127617385,
    "ci95_low": -0.007663020428172211,
    "mean_delta": 0.07171694641209755,
    "resamples": 1000
  }
}
```
