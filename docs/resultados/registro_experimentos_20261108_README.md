# Registro de experimentos (respaldo de la Figura 10)

Archivo: `registro_experimentos_20261108.csv` — exportado de la tabla `experiments` el
**2026-11-08** (corte del dataset a esa fecha). **591 experimentos**; de ellos **506 tienen
instante de falla (T_f) registrado** y son los que respaldan la **Figura 10** de la tesis.

## Columnas
| columna | descripción | unidad |
|---|---|---|
| `experiment_id` | identificador del experimento (p. ej. `EXP-CONG-20260929T200000Z`) | — |
| `scenario` | CONGESTION / WAN_FAILURE / LATENCY_DEGRADATION | — |
| `mechanism` | `deliberate_induced` u `organic_unplanned` | — |
| `start_utc` | inicio de la perturbación | timestamp UTC |
| `end_utc` | fin de la perturbación (restablecimiento) | timestamp UTC |
| `duration_min` | duración | minutos |
| `failure_instant_Tf_utc` | instante de falla T_f (vacío si el experimento no lo registró) | timestamp UTC |
| `name` | nombre legible del experimento | — |
| `notes` | traza de ejecución (trigger, método, estado: completed/failed/aborted) | — |

## Conteos (verificables)
- Experimentos totales: **591**.
- Con T_f (Figura 10): **506** → CONGESTION 476, WAN_FAILURE 29, LATENCY_DEGRADATION 1.

## Cómo se generó / cómo regenerar la figura
Exportado de `experiments` (snapshot 2026-11-08). La figura se regenera con:
```bash
python -m scripts.figura10_desde_registro
# -> docs/graficos/figura10_registro_eventos.png
```

## Privacidad
El registro **no contiene** IPs, MAC, seriales, credenciales ni datos de clientes (solo metadatos
de los experimentos de inyección). No se anonimizó nada porque no hay datos sensibles.
