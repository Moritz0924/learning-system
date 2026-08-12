# Fixed-token-budget retrieval report

The run uses independent vector-only indexes, the same deterministic embedding identity, top_n=20, and canonical EvidenceAnchor mapping.

## Development

| Cutoff | Evidence Recall | MRR | nDCG | Context Density |
|---:|---:|---:|---:|---:|
| 1024 | A: 1.0000 | P: 1.0000 | B: 1.0000 | C: 1.0000 | D: 1.0000 | E: 1.0000 |
| 2048 | A: 1.0000 | P: 1.0000 | B: 1.0000 | C: 1.0000 | D: 1.0000 | E: 1.0000 |
| 512 | A: 0.6500 | P: 0.7500 | B: 0.7500 | C: 0.7500 | D: 0.7500 | E: 0.7500 |

## Test

| Cutoff | Evidence Recall | MRR | nDCG | Context Density |
|---:|---:|---:|---:|---:|
| 1024 | A: 1.0000 | P: 1.0000 | B: 1.0000 | C: 1.0000 | D: 1.0000 | E: 1.0000 |
| 2048 | A: 1.0000 | P: 1.0000 | B: 1.0000 | C: 1.0000 | D: 1.0000 | E: 1.0000 |
| 512 | A: 1.0000 | P: 1.0000 | B: 1.0000 | C: 1.0000 | D: 1.0000 | E: 1.0000 |

Offline deterministic outputs are algorithm checks only and are not Promotion Evidence.
