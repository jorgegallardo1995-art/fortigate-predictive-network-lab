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

`docs/modelo/`:
- `run_20261120T150158Z/` — modelo sobre el histórico completo:
  `model.keras`, `metrics.json`, `run_metadata.json`, `scaler.json`.
- `run_20261122T013409Z/` — modelo sobre el régimen de inyección aleatorizado.
- `crossval_randomized_FRESH.json` — validación cruzada walk-forward (4 pliegues, por evento).

Cada `metrics.json` trae las métricas en el conjunto de prueba (PR-AUC, ROC-AUC, precisión,
recall, F1, matriz de confusión, falsas alarmas/día y lead time) para la LSTM y las líneas
base (Random Forest y naive de calendario). Cada `run_metadata.json` documenta configuración,
features, particiones, semilla y commit.

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

# 3. Entrenamiento full-history
python -m ml.train --db snap.db --data-source REAL

# 4. Validación cruzada walk-forward por evento
python -m ml.crossval --db snap.db --folds 4
```

Los resultados deben coincidir con los artefactos de `docs/modelo/` (determinismo acotado por
`seed=42`; TensorFlow puede tener variación menor por kernels no deterministas).

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
