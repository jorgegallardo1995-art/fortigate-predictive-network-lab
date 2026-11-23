# Auditoría: naive "tiempo desde la última falla" e IC de la anticipación (lead)

Reproducible con `scripts/audit_baseline_lead.py` (ahora en el repo). Base del Release
(corte 2026-11-14T23:59:59Z). Semilla 42, B=3000, bootstrap por evento. Modelos
`run_20261122T144340Z` (régimen limpio) y `run_20261122T144431Z` (histórico).

**Comando:**
```bash
gunzip fortigate_dataset_estudio.db.gz && mv fortigate_dataset_estudio.db snap.db
python -m scripts.audit_baseline_lead --db snap.db
```

---

## 1. Línea base "tiempo desde la última falla"

**Signo aprendido en TRAIN, nunca en prueba.** `ml/baselines.py` → `time_since_failure_baseline`:
ajusta una `LogisticRegression` sobre `x_tr = minutes_since_last_failure(train.end_timestamps, …)`
con `train.y` (es decir, solo con train), y predice sobre test. El umbral de operación, cuando
aplica, se elige en validación (`ml/evaluate.py` `select_threshold`), nunca en prueba.

| Análisis | n_test | prevalencia | LSTM PR-AUC / ROC | naive PR-AUC / ROC |
|---|---|---|---|---|
| Régimen sin sesgo (13 eventos) | 832 | 0,166 | 0,197 / 0,474 | 0,253 / 0,717 |
| Prueba aleatorizada (74 eventos) | 5.709 | 0,145 | 0,431 / 0,675 | **0,098 / 0,309** |

**Diferencia LSTM − naive (bootstrap por evento, B=3000, semilla 42):**

| Análisis | media | IC 95 % | P(>0) | Estado |
|---|---|---|---|---|
| Régimen sin sesgo (13 ev) | −0,113 | [−0,374, +0,071] | 0,14 | **INCONCLUSO** |
| Prueba aleatorizada (74 ev) | **+0,335** | [+0,269, +0,403] | 1,00 | **CONFIRMA** |

**Lectura.** En el régimen limpio (13 eventos) la muestra es pequeña y la naive ajustada acierta el
signo sobre ese test (ROC 0,717), por lo que la LSTM no la supera de forma concluyente. En la prueba
aleatorizada (74 eventos), la naive honesta **colapsa** (PR-AUC 0,098, ROC 0,309 < 0,5: el signo
aprendido en train se invierte en el régimen aleatorizado) y la **LSTM la supera decisivamente**
(+0,335, CONFIRMA). Esta naive honesta es **más adversa para sí misma** que la cota oráculo usada
antes (0,203), por lo que la ventaja de la LSTM es **mayor** (+0,335 frente a +0,219 contra el
oráculo). No disminuye ninguna cifra publicada.

---

## 2. Intervalos de confianza de la anticipación (lead), definición corregida [T_f−6, T_f]

Eventos no detectados: cuentan como fallo en la tasa de detección, pero se **excluyen** del lead
medio/mediana (no tienen lead). La diferencia RF−LSTM se calcula **pareada por evento**, solo sobre
los eventos detectados por **ambos** modelos.

### Régimen sin sesgo (13 eventos)
| modelo | detección | IC 95 % | lead medio | IC 95 % | mediana | IC 95 % | n_det |
|---|---|---|---|---|---|---|---|
| LSTM | 9/13 (0,69) | [0,46, 0,92] | 3,43 | [2,07, 4,65] | 4,5 | [0,0, 5,1] | 9 |
| RF | 10/13 (0,77) | [0,54, 1,00] | 4,33 | [2,73, 5,45] | 5,3 | [2,6, 5,5] | 10 |

RF − LSTM lead medio (pareado, n=9): **+0,78 min**, IC 95 % [+0,22, +1,44] (RF anticipa más).

### Prueba aleatorizada (74 eventos)
| modelo | detección | IC 95 % | lead medio | IC 95 % | mediana | IC 95 % | n_det |
|---|---|---|---|---|---|---|---|
| LSTM | 55/74 (0,74) | [0,64, 0,84] | 4,26 | [3,84, 4,64] | 5,0 | [4,0, 5,2] | 55 |
| RF | 64/74 (0,86) | [0,78, 0,93] | 4,87 | [4,50, 5,19] | 5,4 | [5,2, 5,5] | 64 |

RF − LSTM lead medio (pareado, n=55): **+0,49 min**, IC 95 % [+0,25, +0,75] (RF anticipa más, significativo).

---

## Resumen
- La LSTM **supera de forma concluyente** a la naive honesta de calendario en la prueba aleatorizada
  (74 eventos): +0,335, CONFIRMA; en el régimen limpio (13 eventos) es inconcluso por tamaño de muestra.
- RF anticipa significativamente más que LSTM (lead medio +0,5 a +0,8 min, IC excluye 0).
- No cambia ninguna cifra publicada (PR-AUC, ROC, IC del bootstrap anterior). La naive honesta
  (train-sign) es 0,098 en el aleatorizado, más adversa que la oráculo 0,203 ya reportada.
