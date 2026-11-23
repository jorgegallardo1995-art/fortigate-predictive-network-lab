# Tarea 5 — Falsas alarmas: validación -> prueba (run_20261122T144340Z)

`python -m scripts.falsas_alarmas_validacion_prueba --db snap.db`

Días de validación = 1.147 | días de prueba = 1.196

## (a) Umbral de operación 0,60
- validación: FP=57, FA/día=49.7, recall=0.432
- prueba:     FP=108, FA/día=90.3, recall=0.232

## (b) Umbral elegido en VALIDACIÓN para <= 2 FA/día
- umbral = **0.93**
- validación: FP=2, FA/día=**1.7**, recall=0.272
- prueba:     FP=43, FA/día=**36.0**, recall=0.159

**Resultado:** un umbral calibrado a ≤2 FA/día en validación produce **36 FA/día en prueba** → el punto de operación NO transfiere (el umbral se fija solo en validación, nunca en prueba).
