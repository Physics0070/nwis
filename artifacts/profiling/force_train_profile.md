# Data profile — FORCE 2020 - train

- Source: `D:\SOHAM ALL\hackathons\SIH\data\raw\force2020\train.csv`
- Generated: 2026-09-06T18:17:00.265701+00:00
- Rows: 1,170,511
- Columns: 29

## Notes

- X_LOC/Y_LOC magnitudes are projected metres (North Sea UTM), not degrees. A CRS transform is required before these can be mapped.

## Columns

| Column | dtype | non-null | null % | min | max | mean |
|---|---|---:|---:|---:|---:|---:|
| `WELL` | object | 1,170,511 | 0.0% | — | — | — |
| `DEPTH_MD` | float64 | 1,170,511 | 0.0% | 136.1 | 5,437 | 2,184 |
| `X_LOC` | float64 | 1,159,736 | 0.9% | 4.269e+05 | 5.726e+05 | 4.856e+05 |
| `Y_LOC` | float64 | 1,159,736 | 0.9% | 6.407e+06 | 6.857e+06 | 6.681e+06 |
| `Z_LOC` | float64 | 1,159,736 | 0.9% | -5,396 | -111.1 | -2,139 |
| `GROUP` | object | 1,169,233 | 0.1% | — | — | — |
| `FORMATION` | object | 1,033,517 | 11.7% | — | — | — |
| `CALI` | float64 | 1,082,634 | 7.5% | 2.344 | 28.28 | 13.19 |
| `RSHA` | float64 | 630,650 | 46.1% | 0.0001 | 2,194 | 10.69 |
| `RMED` | float64 | 1,131,518 | 3.3% | -0.008419 | 1,989 | 4.987 |
| `RDEP` | float64 | 1,159,496 | 0.9% | 0.0317 | 2,000 | 10.69 |
| `RHOB` | float64 | 1,009,242 | 13.8% | 0.721 | 3.458 | 2.285 |
| `GR` | float64 | 1,170,511 | 0.0% | 0.1093 | 1,077 | 70.91 |
| `SGR` | float64 | 69,353 | 94.1% | -778 | 963.6 | 64.9 |
| `NPHI` | float64 | 765,409 | 34.6% | -0.03582 | 0.9996 | 0.332 |
| `PEF` | float64 | 671,692 | 42.6% | 0.09972 | 383.1 | 6.32 |
| `DTC` | float64 | 1,089,648 | 6.9% | 7.415 | 320.5 | 113.4 |
| `SP` | float64 | 864,247 | 26.2% | -999 | 526.5 | 60.03 |
| `BS` | float64 | 682,657 | 41.7% | 6 | 26 | 11.93 |
| `ROP` | float64 | 535,071 | 54.3% | -0.118 | 4.702e+04 | 137.4 |
| `DTS` | float64 | 174,613 | 85.1% | 69.16 | 676.6 | 204.7 |
| `DCAL` | float64 | 298,833 | 74.5% | -12.22 | 1.001e+04 | 1.224 |
| `DRHO` | float64 | 987,857 | 15.6% | -7,429 | 2.837 | 0.0122 |
| `MUDWEIGHT` | float64 | 316,151 | 73.0% | 0.1258 | 185.7 | 1.216 |
| `RMIC` | float64 | 176,160 | 85.0% | 0.05659 | 1e+04 | 7.797 |
| `ROPA` | float64 | 192,325 | 83.6% | -999.3 | 742.8 | 23.51 |
| `RXO` | float64 | 327,427 | 72.0% | -999.9 | 3.593e+04 | -95.78 |
| `FORCE_2020_LITHOFACIES_LITHOLOGY` | int64 | 1,170,511 | 0.0% | 30000 | 99000 | 6.139e+04 |
| `FORCE_2020_LITHOFACIES_CONFIDENCE` | float64 | 1,170,332 | 0.0% | 1 | 3 | 1.164 |
