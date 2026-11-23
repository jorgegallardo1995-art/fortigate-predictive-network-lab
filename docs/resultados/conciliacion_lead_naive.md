# Conciliación: lead del régimen sin sesgo y naive = oráculo

Base del Release, modelo `run_20261122T144340Z`, umbral LSTM=RF=0,60 (elegido en validación).

## 1. Lead del régimen sin sesgo (13 eventos): la diferencia es el T_f de referencia

La discrepancia entre `eventos_prueba.csv` (LSTM media 2,33 / mediana 2,0; RF 3,30 / 5,0) y
`scripts/audit_baseline_lead.py` (LSTM 3,43 / 4,5; RF 4,33 / 5,3) **no** es de umbral, partición ni
modelo (los tres son idénticos). Es el **instante de falla de referencia**:

- `eventos_prueba.csv` (versión anterior) medía el lead contra el **fin de la racha positiva**
  (`lead_times` usa `end_timestamps[end]` ≈ última ventana positiva). Cuando hay huecos de datos
  cerca de T_f, esa última ventana puede quedar **varios minutos antes** del T_f real.
- `audit_baseline_lead.py` mide contra el **T_f real del experimento** (`failure_timestamp`).

**Detalle por evento (umbral 0,60):** la *alerta* es la misma en ambas definiciones; solo cambia el
T_f. Ejemplos del hueco T_f_real − run_end: ev4 271 s (alerta 22:28:37 → proxy da lead 0,0, T_f real
22:33:09 da 4,5), ev6 178 s, ev11 51 s; eventos no detectados ev3/ev9/ev12 tienen huecos de 600–836 s.

**El valor correcto** (contra el T_f real) es:

| modelo | detección | media | IC 95 % | mediana | IC 95 % |
|---|---|---|---|---|---|
| LSTM | 9/13 | **3,43** | [2,07, 4,65] | **4,5** | [0,0, 5,1] |
| RF | 10/13 | **4,33** | [2,73, 5,45] | **5,3** | [2,6, 5,5] |

La definición anterior (proxy `run_end`) **subestimaba** el lead. Se corrigieron
`docs/resultados/eventos_prueba.csv`, `docs/graficos/lead_por_modelo.*` y las filas F.16 de
`cifras_capitulo4.csv`.

**Cambios de cifra (anterior → nuevo):**
| cifra | anterior (proxy) | nuevo (T_f real) |
|---|---|---|
| lead mediano LSTM / RF | 2,0 / 5,0 | **4,5 / 5,3** |
| lead medio LSTM / RF | 2,33 / 3,30 | **3,43 / 4,33** |
| lead ≥ 3 min LSTM / RF | 4 / 6 | **6 / 8** |
| lead ≥ 5 min LSTM / RF | 2 / 6 | **3 / 8** |
| detección LSTM / RF | 9/13 / 10/13 | **9/13 / 10/13** (sin cambio) |

El test aleatorizado (`eventos_prueba_aleatorizado.csv`) **ya usaba el T_f real**, por lo que queda
consistente. No cambia ninguna PR-AUC, ROC ni IC del bootstrap de los modelos.

## 2. Naive "tiempo desde la última falla" = oráculo (en el régimen limpio)

Las dos puntuaciones **son distintas**, pero producen el **mismo ranking** sobre las 832 secuencias,
por eso sus métricas de ranking (PR-AUC, ROC) coinciden.

```
naive ajustada: PR-AUC=0.2534 ROC=0.7166
oráculo (+x):   PR-AUC=0.2534 ROC=0.7166
Spearman(naive_proba, oráculo_score) = 1.0000
¿mismo vector numérico? False
primeros 5 naive_proba:   [0.4079 0.4274 0.4286 0.4298 0.4309]
primeros 5 oráculo_score: [33.3   50.3   51.3   52.3   53.3  ]   (= minutos desde la última falla)
```

**Por qué coinciden:** la naive ajustada es `sigmoide(a·x + b)`, una función **monótona** de
x = «minutos desde la última falla»; su ranking es el de x con el **signo aprendido en TRAIN**. El
oráculo es el **mejor signo de x** en test. En el régimen limpio el signo de train coincide con el
óptimo (ambos +x), así que ordenan las 832 secuencias de forma idéntica → **Spearman = 1,0000** →
PR-AUC y ROC idénticas, aunque los números sean distintos. (En la prueba aleatorizada el signo de
train se invierte respecto del óptimo, por eso ahí difieren: naive 0,098 vs oráculo 0,203.)

Evidencia del signo en train (no en prueba): `ml/baselines.py` `time_since_failure_baseline`
(`LogisticRegression().fit(x_tr, train.y)`). Reproducible con `scripts/audit_baseline_lead.py`.
