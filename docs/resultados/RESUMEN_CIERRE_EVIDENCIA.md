# Resumen — cierre de evidencia (H04, H07, H10, H14, fuente de «2→36»)

Rama `cierre-evidencia-v70`. Modelos publicados y base del Release (corte 2026-11-14T23:59:59Z).
No se reentrenó ni se cambió código de `ml/`, `app/`, `config/`. No se movieron tags anteriores.

| Tarea | Qué se hizo | Resultado | Estado |
|---|---|---|---|
| **H04** | `scripts/bootstrap_prauc_puntual_vs_media.py`: descompone resta puntual vs media bootstrap | El signo (fijo vs reelegido) da **idéntico −0,1131**: NO es la causa. El sesgo media−puntual (−0,057 en limpio LSTM−oráculo) se debe a la **asimetría del bootstrap de PR-AUC con 13 eventos**. Mediana −0,075 entre punto (−0,056) y media (−0,113). 0 réplicas descartadas, sin drift de prevalencia. Conclusión INCONCLUSO sin cambio. | **VERIFICADO** |
| **H07** | `docs/resultados/cronologia_configuraciones.md` desde git + run_metadata | Config invariante (23v/15/seed42/ref) en todas las corridas; umbral elegido en **validación**. **Ninguna decisión se tomó sobre la prueba.** | **PARCIAL** (varios scripts de sensibilidad fueron ad-hoc y no quedaron versionados → sus fechas/commits exactos son NO VERIFICADO) |
| **H10** | `scripts/auditoria_ausencia_datos.py` | 0 timestamps duplicados; intervalo mediana 60 s (máx 1.842 min); reconciliación: ausencia física **5.147 min** (= tesis), ciclos fallidos 7.760, sin-telemetría-válida 12.907; `collection_gaps` (13.969) sobrecuenta | **VERIFICADO** |
| **H14** | `scripts/auditoria_privacidad.py` (detección por hash, sin valores en claro) | **0** serial / MAC / IP pública conocidos; IPs restantes = sondas ICMP (esperadas) + privadas. **Detectó un residuo:** `speed_tests.result_url` (3.428 enlaces a speedtest.net que exponen indirectamente la IP pública) — **se redactó** (base v2). | **VERIFICADO** (con acción pendiente: re-subir el asset del Release, abajo) |
| **Fuente 2→36** | `scripts/falsas_alarmas_validacion_prueba.py` (run_20261122T144340Z) | Umbral 0,93 (≤2 FA/día en validación) → **36,0 FA/día en prueba**. Reproducido exacto. | **VERIFICADO** |

## Acción pendiente derivada de H14 (importante)
El asset actual del Release (`fortigate_dataset_estudio.db.gz`, sha256 `946a4bb0…`) **todavía
contiene** `speed_tests.result_url`. Se regeneró la base **v2** con esa columna redactada:
- base v2 (db) sha256: `0880dac27e2625d5ddc26c1b599ea0a194d1e3532b3d6e6f13bd00b72c8f4a4d`
- gz v2 sha256: `93ee8a624b946b6d61fba2447c85ada1d30d4f58dbed9364d28857afb3af6492`
- archivo listo en `~/Downloads/fortigate_dataset_estudio_v2.db.gz`

Reemplazar el asset del Release por v2 (desde una sesión autenticada): Releases → `tesis-cap4-freeze`
→ editar → borrar el `.gz` actual y subir el v2. Los resultados del modelo **no cambian** (la columna
no la usa el pipeline). Tras re-subir, actualizar `docs/DECLARACION_DATOS.md` (6 columnas redactadas y
el nuevo hash).

## Texto sugerido para la tesis
- **H04:** «La diferencia entre la resta puntual (−0,056) y la media bootstrap (−0,113) de LSTM−oráculo
  en el régimen limpio se debe al sesgo del bootstrap de PR-AUC con 13 eventos, no a la selección del
  signo (ambos dan −0,113); la mediana es −0,075 y el estado (inconcluso) no cambia. Ver
  `scripts/bootstrap_prauc_puntual_vs_media.py`, tag `tesis-cierre-evidencia-v1`.»
- **H07:** «Ninguna decisión de arquitectura, hiperparámetros, ventana, umbral o variables se tomó
  sobre el conjunto de prueba; el umbral se selecciona en validación y la configuración es invariante
  (`docs/resultados/cronologia_configuraciones.md`).»
- **H10:** «La ausencia física neta de datos es 5.147 min (período − filas); los 13.969 min de
  `collection_gaps` sobrecuentan al incluir 7.760 ciclos fallidos (`scripts/auditoria_ausencia_datos.py`).»
- **H14:** «La auditoría de privacidad (`scripts/auditoria_privacidad.py`) confirma 0 seriales, MAC o
  IP públicas en el dataset publicado; las únicas IPs son objetivos de sonda ICMP.»
- **2→36:** «Un umbral calibrado a ≤2 falsas alarmas/día en validación produce 36/día en prueba
  (`scripts/falsas_alarmas_validacion_prueba.py`): el punto de operación no transfiere.»

## Discrepancias con lo que hoy dice la tesis
- Ninguna cifra de modelo cambia. La única acción material es la **re-subida del asset del Release**
  (redacción de `result_url`) y la actualización del hash en `DECLARACION_DATOS.md`.
- NO VERIFICADO: fechas/commits exactos de las ~25 configuraciones de sensibilidad (scripts ad-hoc no
  versionados); aspecto visual exacto de figuras impresas que no están en el repositorio.
