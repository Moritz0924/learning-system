# Fixed-K retrieval report

The run uses independent vector-only indexes, the same deterministic embedding identity, top_n=20, and canonical EvidenceAnchor mapping.

## Development

| Cutoff | Evidence Recall | MRR | nDCG | Context Density |
|---:|---:|---:|---:|---:|
| 1 | A: 0.1500 | P: 0.0500 | B: 0.0500 | C: 0.0500 | D: 0.0500 | E: 0.0500 |
| 10 | A: 0.5500 | P: 0.6000 | B: 0.6000 | C: 0.6000 | D: 0.6000 | E: 0.6000 |
| 3 | A: 0.2000 | P: 0.3000 | B: 0.3000 | C: 0.3000 | D: 0.3000 | E: 0.3000 |
| 5 | A: 0.4000 | P: 0.4000 | B: 0.4000 | C: 0.4000 | D: 0.4000 | E: 0.4000 |

## Test

| Cutoff | Evidence Recall | MRR | nDCG | Context Density |
|---:|---:|---:|---:|---:|
| 1 | A: 0.0000 | P: 0.1000 | B: 0.1000 | C: 0.1000 | D: 0.1000 | E: 0.1000 |
| 10 | A: 1.0000 | P: 1.0000 | B: 1.0000 | C: 1.0000 | D: 1.0000 | E: 1.0000 |
| 3 | A: 0.4000 | P: 0.2000 | B: 0.2000 | C: 0.2000 | D: 0.2000 | E: 0.2000 |
| 5 | A: 0.5000 | P: 0.4000 | B: 0.4000 | C: 0.4000 | D: 0.4000 | E: 0.4000 |

Offline deterministic outputs are algorithm checks only and are not Promotion Evidence.
