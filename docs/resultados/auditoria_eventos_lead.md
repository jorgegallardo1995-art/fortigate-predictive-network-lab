# Auditoría: conteo de eventos (74 vs 75) y definición de anticipación (lead)

Base del Release (corte 2026-11-14T23:59:59Z), modelos `run_20261122T144431Z` (histórico) y
`run_20261122T144340Z` (régimen limpio). Definición de evento: **corrida contigua de ventanas
positivas** (igual que `ml/evaluate.py` `lead_times`, líneas 154–165).

---

## PROBLEMA 1 — ¿74 o 75 eventos?

**1a. Nº de eventos del bootstrap y código.** El bootstrap publicado usó **75** corridas contiguas
del subconjunto aleatorizado (end ≥ 2026-11-07). El agrupamiento es el mismo de `ml/evaluate.py`
`lead_times` (racha contigua de `y_true==1`). **Nota de reproducibilidad:** el script del
bootstrap por evento fue un análisis ad-hoc **no incluido en el repositorio** (sí lo está la
definición de corrida en `ml/evaluate.py`); se documenta aquí su resultado y se corrige.

**1b/1c. El evento que sobra.** De las 75 corridas, **1 es de borde**: la última,
`inicio 2026-11-14 19:56:46 → fin 20:10:46`, con **T_f = 2026-11-14 20:10:47**, es decir **0,85 s
después del fin del test** (20:10:46,58). Es **el mismo evento de borde que se excluye del test
limpio** (por eso el test limpio tiene 13 y no 14). No es una corrida dividida ni un error del CSV:
es una corrida cuyo instante de falla cae fuera de la ventana de prueba.

**Conclusión: el número correcto es 74** (aplicando al test aleatorizado la **misma regla** que al
test limpio = excluir el borde con T_f fuera del test). El bootstrap incluyó el borde (75). El CSV
original (74) era, de hecho, el consistente.

**1d. Bootstrap recalculado con 74 (semilla 42, B=3000):**

| Comparación | 74 eventos | Publicado (75) | Estado |
|---|---|---|---|
| LSTM − calendario oráculo | +0,220 [+0,130, +0,302] | +0,219 [+0,128, +0,297] | **CONFIRMA** |
| LSTM − regla de umbral | +0,287 [+0,222, +0,356] | +0,287 [+0,224, +0,352] | **CONFIRMA** |
| RF − LSTM | +0,053 [−0,002, +0,105] | +0,053 [−0,005, +0,106] | **INCONCLUSO** |

**Ninguna conclusión cambia.** Excluir un evento de borde de 75 es despreciable para los IC.

**Cambio de cifra en la tesis:** «75 eventos» → **74 eventos** (los IC y las conclusiones se mantienen).

**1e. Eventos por partición (definición de corrida contigua):**
| | train | val | test | (de test) ≥7-nov |
|---|---|---|---|---|
| Histórico completo | 323 | 102 | 116 | 75 (74 con T_f dentro del test) |
| Régimen limpio (desde 7-nov) | 49 | 12 | 14 | — (13 con T_f dentro) |

---

## PROBLEMA 2 — Anticipaciones (lead) mayores que 6 min

**2a. Definición de `lead_LSTM_min`/`lead_RF_min`.** `ml/evaluate.py` `lead_times` (143–165) +
`_lead_time_for_run` (168–178): dentro de cada **racha positiva contigua**, se toma la **primera**
ventana con `proba ≥ umbral` y se reporta `lead = T_f − T_alerta`, con `T_f` ≈ última ventana
positiva de la racha. La racha positiva abarca **todo el horizonte de 15 min** previo a T_f, por lo
que la primera alerta puede estar hasta ~14 min antes de T_f, mientras la **rampa de congestión
empieza en T_f − 6 min**. Por eso aparecen leads > 6: son alertas **antes** de que empiece la
perturbación.

**2b. Eventos con lead > 6 (test aleatorizado):** 1 en LSTM, **6 en RF**. Random Forest:
| alerta (UTC) | T_f≈ | lead |
|---|---|---|
| 2026-11-07 02:51 | 03:00 | 9,0 |
| 2026-11-07 04:54 | 05:05 | 11,0 |
| 2026-11-07 10:51 | 11:05 | 14,0 |
| 2026-11-07 12:51 | 13:05 | 14,0 |
| 2026-11-09 14:01 | 14:15 | 14,0 |
| 2026-11-10 04:58 | 05:04 | 6,0 |

En **todos** estos eventos existe además una alerta **dentro de la ventana de rampa [T_f−6, T_f]**,
por lo que la detección es **genuina** (el modelo también alerta durante la perturbación); solo la
*primera* alerta (pre-rampa) infla el lead. No hay otra falla ni speed test solapado (el protocolo
garantiza exclusión mutua de eventos).

**2c. ¿Son falsas alarmas contadas como detección? NO.** Con la definición correcta (alerta dentro
de [T_f−6, T_f]) la **tasa de detección NO cambia**:

| modelo | detección (actual) | detección (corregida) | lead actual (med/mean) | lead corregido (med/mean) |
|---|---|---|---|---|
| LSTM | 55/74 | **55/74** | 4,0 / 3,7 | **5,0 / 4,3** |
| RF | 64/74 | **64/74** | 5,0 / 4,9 | **5,4 / 4,9** |

Ningún evento se detecta **solo** por una alerta pre-rampa. El CSV `eventos_prueba_aleatorizado.csv`
se regeneró con la definición corregida (leads ≤ 6; antes hasta 14).

**2d. Archivo de 13 eventos (`eventos_prueba.csv`):** leads LSTM `3,4,0,0,2,0,5,2,5` y RF
`5,5,1,0,2,5,0,5,5,5` — **todos ≤ 5 ≤ 6**. No tiene este problema; no cambia.

**2e. Anticipación media del histórico (umbral 0,70).** La tesis reporta ~4 min (lead_times = 4,1).
Con la definición corregida (dentro de la rampa) = **4,7 min**; solo **1** evento de 116 tiene
lead > 6. La detección (96/116) no cambia.

---

## Resumen de cambios (anterior → nuevo)
| Ítem | Anterior | Nuevo | ¿Cambia conclusión? |
|---|---|---|---|
| Eventos prueba aleatorizada | 75 | **74** | No (IC idénticos) |
| Leads del CSV aleatorizado | hasta 14 min (artefacto) | **≤ 6 min** (dentro de rampa) | No (detección igual) |
| Lead medio histórico (0,70) | 4,1 min | **4,7 min** (definición corregida) | No (detección igual) |
| Tasas de detección, PR-AUC, ROC, IC bootstrap | — | **sin cambios** | — |

CSV corregido: `docs/resultados/eventos_prueba_aleatorizado.csv` (74 filas, leads ≤ 6).
