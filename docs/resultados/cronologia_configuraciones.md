# H07 — Cronología de selección de configuraciones (solo evidencia)

Reconstruido de: historial de git, `run_metadata.json` (`generated_at`, `git_commit`, `config`,
`selected_threshold`), `cifras_capitulo4.csv` y los documentos `docs/resultados/respuestas_*`.

## Configuración fija (no cambia entre corridas) — verificable en `ml/config.py` y en cada run_metadata
Todas las corridas de modelo usan: **23 variables, ventana 15 min, horizonte 15 min, semilla 42,
LSTM "reference", Adam lr 0,001, batch 32, EarlyStopping paciencia 10**, `StandardScaler` ajustado
solo con train, partición temporal 70/15/15. El **umbral** se elige maximizando F1 en **validación**
(`ml/evaluate.py` `select_threshold`), nunca en prueba.

## Corridas PRINCIPALES (evidencia directa en run_metadata)
| run_id | generated_at | commit | ventana de datos | umbral | config | fuente |
|---|---|---|---|---|---|---|
| run_20261120T150158Z | 2026-11-20 15:01 | ab064cb | histórico (sin corte) | 0,70 | 23v/15/seed42/ref | run_metadata |
| run_20261122T144431Z | 2026-11-22 14:44 | 1183d85 | histórico, until 14-nov | 0,70 | 23v/15/seed42/ref | run_metadata |
| run_20261122T144340Z | 2026-11-22 14:43 | 1183d85 | aleatorizado 7-nov, until 14-nov | 0,60 | 23v/15/seed42/ref | run_metadata |

El umbral 0,70/0,60 proviene de `select_threshold` sobre **validación** (campo `selected_threshold`).

## Corridas del histórico de entrenamiento (automatizadas)
107 corridas entre 2026-09-13 y 2026-11-20 (3/día vía launchd), **todas con la misma config**
(23v/15/seed42/ref). Directorios `data/models/run_*` con su `run_metadata.json`. Fuente: listado de
`data/models/` + `cifras_capitulo4.csv` (crecimiento del dataset).

## Análisis de SENSIBILIDAD (reportados como robustez, NO usados para elegir la principal)
| análisis | configuración | selección | cifra | fuente | estado |
|---|---|---|---|---|---|
| 5 semillas | LSTM 23v, semillas {42,1,7,123,2024} | — (robustez) | clean 0,215±0,024; full 0,496±0,018 | respuestas_auditoria_IA_2.md §G.18 | PARCIAL (script ad-hoc no versionado) |
| arquitectura | LSTM 32→16, GRU 64→32, ventana 30 | — (robustez) | 0,207 / 0,289 / 0,336 | respuestas_auditoria_IA_2.md §G.19 | PARCIAL (ídem) |
| conjunto de variables | 20 var (sin isp_router) | — (sensibilidad) | clean 0,213; full 0,510 | respuestas_auditoria_IA_2.md §B | PARCIAL (ídem) |
| curva de aprendizaje | LSTM/RF con 25/50/75/100% train | — | plano ~0,20 / ~0,40 | docs/graficos/datos_curva_aprendizaje.csv | VERIFICADO |

El recuento aproximado de ~25 configuraciones LSTM y ~6 RF evaluadas sobre la prueba proviene de
`respuestas_auditoria_IA_2.md §G.20`. **Los scripts de varias de estas sensibilidades fueron ad-hoc
y NO quedaron versionados** → sus fechas/commits exactos son **NO VERIFICADO**; lo que SÍ es
verificable es que la configuración de las corridas principales es invariante (run_metadata) y que
las variantes se reportan como sensibilidad, no como selección.

## ¿Alguna decisión se tomó mirando la PRUEBA?
**No hay evidencia de ello.** El umbral se elige en **validación** (`ml/evaluate.select_threshold`);
el escalador se ajusta solo con **train** (`ml/preprocessing.SequenceScaler.fit`); la arquitectura,
los hiperparámetros, la ventana y las 23 variables están **fijos en `ml/config.py`** desde el commit
`6e738ed` (2026-11-07) y son idénticos en todas las corridas. Las variantes de arquitectura/semilla/
variables se **reportan** como robustez, no se usaron para elegir la principal. La única cifra que usa
el signo sobre la prueba es la **línea base de calendario ORÁCULO**, declarada explícitamente como
cota superior optimista (no es una decisión del modelo).
