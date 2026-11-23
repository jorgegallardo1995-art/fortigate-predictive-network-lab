# Bootstrap de diferencias de PR-AUC (Sección 4.9) — reproducido vs tesis

B=3000, semilla=42, remuestreo por evento. Reproducible: `python -m scripts.bootstrap_prauc_diferencias --db snap.db`.


## Régimen limpio (13 eventos) — 13 eventos, n_test=832, prevalencia=0.166

| comparación | reproducido (IC95%) | tesis (IC95%) | |Δmedia| | estado |
|---|---|---|---|---|
| LSTM - oraculo | -0.113 [-0.374, +0.071] | -0.113 [-0.375, +0.065] | 0.000 | INCONCLUSO |
| LSTM - umbral | +0.041 [-0.026, +0.166] | +0.040 [-0.031, +0.153] | 0.001 | INCONCLUSO |
| RF - LSTM | +0.191 [+0.084, +0.282] | +0.194 [+0.095, +0.284] | 0.003 | CONFIRMA |

## Histórico aleatorizado (74 eventos) — 74 eventos, n_test=5709, prevalencia=0.145

| comparación | reproducido (IC95%) | tesis (IC95%) | |Δmedia| | estado |
|---|---|---|---|---|
| LSTM - oraculo | +0.220 [+0.130, +0.302] | +0.220 [+0.130, +0.302] | 0.000 | CONFIRMA |
| LSTM - umbral | +0.288 [+0.223, +0.354] | +0.287 [+0.222, +0.356] | 0.001 | CONFIRMA |
| RF - LSTM | +0.053 [-0.002, +0.109] | +0.053 [-0.002, +0.105] | 0.000 | INCONCLUSO |
