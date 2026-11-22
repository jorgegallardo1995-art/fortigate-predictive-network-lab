# Guía para el revisor — Modelo de predicción temprana de fallas

Repositorio de la fase experimental de la tesis doctoral *"Algoritmo de continuidad
operativa en entornos digitales para redes de comunicaciones"*. Contiene el colector
de telemetría de red sobre un **FortiGate 50E** (ejecutado en una Raspberry Pi) y el
pipeline de entrenamiento/evaluación del modelo LSTM.

## 1. Qué contiene

- **Colector** (`app/`): captura telemetría multivariada del FortiGate y sondas ICMP,
  la almacena de forma trazable (observaciones crudas inmutables, features recomputables,
  huecos registrados), la valida y la exporta. No entrena modelos.
- **Pipeline del modelo** (`ml/`): `train.py`, `crossval.py`, `model.py`, `baselines.py`,
  `preprocessing.py`, `induced.py`, `evaluate.py`, `variables.py`, `config.py`.
- **Artefactos entrenados** (`docs/modelo/`): modelos listos para inspección sin reentrenar.
- **Resultados y gráficos** (`docs/resultados/`, `docs/graficos/`).

## 2. Dónde está el modelo entrenado

`docs/modelo/` (corridas con corte explícito `--until 2026-11-14T23:59:59Z`; su
`run_metadata.json` registra `until_cutoff`, `dataset_period` real y el commit del árbol limpio):
- `run_20261122T144431Z/` — modelo sobre el histórico completo:
  `model.keras`, `metrics.json`, `run_metadata.json`, `scaler.json`.
- `run_20261122T144340Z/` — modelo sobre el régimen de inyección aleatorizado.
- `crossval_randomized.json` — validación cruzada walk-forward (4 pliegues, por evento).

Cada `metrics.json` trae las métricas en el conjunto de prueba (PR-AUC, ROC-AUC, precisión,
recall, F1, matriz de confusión, falsas alarmas/día y lead time) para la LSTM y las líneas
base (Random Forest y naive de calendario). Cada `run_metadata.json` documenta configuración,
features, particiones, semilla y commit.

> **Conteo de eventos de prueba (13, no 14).** El campo `lead_time.events_evaluated` del
> `metrics.json` vale 14 porque cuenta *corridas positivas contiguas*. La tesis usa
> **13 eventos independientes**, excluyendo la corrida de borde 2026-11-14 19:56→20:10 UTC,
> cuyo instante de falla (T_f) cae justo fuera de la ventana de prueba. Con el criterio de 13
> eventos: la LSTM detecta **9/13** y el RF **10/13**; lead mediano **2,0 / 5,0 min**; con
> anticipación ≥ 3 min 4 / 6 y ≥ 5 min 2 / 6 (ver `docs/resultados/cifras_capitulo4.csv` y
> `docs/graficos/lead_por_modelo.*`). Este recuento **no afecta** ninguna métrica de modelo
> (PR-AUC, ROC-AUC, IC bootstrap, FA/día), que son independientes del evaluador de lead.

## 3. Configuración del modelo (fuente: `ml/config.py`)

LSTM(64) → Dropout(0,2) → LSTM(32) → Dropout(0,2) → Dense(16, ReLU) → Dropout(0,2) → Dense(1, sigmoid).
35.489 parámetros · ventana 15 min · horizonte 15 min · muestreo 60 s · 23 variables de entrada ·
`StandardScaler` ajustado **solo con train** · partición **temporal** 70/15/15 · umbral elegido en
validación · Adam lr 0,001 · batch 32 · EarlyStopping paciencia 10 · **semilla 42**.

Líneas base para contraste (`ml/baselines.py`): Random Forest sobre las mismas ventanas aplanadas
(media/último) y naive "tiempo desde la última falla" (solo calendario, sin telemetría).

## 4. Cómo reproducir

```bash
# 1. Entorno (Python 3.12)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # y requirements-ml.txt para TensorFlow

# 2. Base de datos del estudio (se entrega aparte por tamaño; ver §6) como ./snap.db

# 3. Entrenamiento full-history (con corte del periodo del estudio)
python -m ml.train --db snap.db --data-source REAL --until 2026-11-14T23:59:59Z

# 3b. Régimen de inyección aleatorizado
python -m ml.train --db snap.db --since 2026-11-07 --until 2026-11-14T23:59:59Z

# 4. Validación cruzada walk-forward por evento
python -m ml.crossval --db snap.db --since 2026-11-07 --until 2026-11-14T23:59:59Z --folds 4
```

El parámetro `--until` aplica el corte del periodo de estudio AL CARGAR los datos, de modo que
las etiquetas, los instantes de falla, `dataset_period` y el metadata reflejan solo datos dentro
del periodo. Los resultados deben coincidir con los artefactos de `docs/modelo/` (determinismo
acotado por `seed=42`; TensorFlow puede tener variación menor por kernels no deterministas). La
base publicada en Releases ya está acotada al periodo, por lo que `--until` es redundante sobre
ella pero deja el corte explícito y auto-documentado.

## 5. Integridad y anti-fuga (verificable en código)

- Escalador ajustado solo con train: `ml/preprocessing.py` (`SequenceScaler.fit`), aplicado a
  val/test en `ml/train.py`.
- Umbral elegido solo en validación: `ml/evaluate.py` (`select_threshold`).
- Carga inducida (speed tests, colas post-T_f, apagados programados) excluida del baseline
  orgánico: `ml/induced.py`.
- Orden del pipeline: segmentos continuos → ventanas → partición temporal → escalado.

## 6. Base de datos del estudio

El dataset de la fase experimental vive en una base SQLite. Por su tamaño no se incluye en el
repositorio; se entrega como **archivo descargable en la sección _Releases_** de este repositorio:
`fortigate_dataset_estudio.db.gz` (~102 MB comprimido, ~469 MB descomprimido).

```bash
# descargar desde la pestaña "Releases" del repositorio y descomprimir:
gunzip fortigate_dataset_estudio.db.gz
mv fortigate_dataset_estudio.db snap.db
# luego usar los comandos de §4
```

El esquema de tablas y el diccionario de las 23 variables están en cada
`docs/modelo/*/run_metadata.json` y en `app/database/models.py`.

## 7. Punto de referencia congelado

El estado evaluado está marcado con el tag **`tesis-cap4-freeze`**:

```bash
git checkout tesis-cap4-freeze
```
