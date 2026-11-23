# Declaración de tratamiento de datos e inventario del anexo técnico

Estado congelado: tag `tesis-cap4-freeze-v3` (commit `9b8302e`), código con `--until` en commit
`1183d85`. Base del estudio: Release "Dataset del estudio" (corte **2026-11-14T23:59:59 UTC**).

---

## 1. Declaración de tratamiento de datos (hechos verificables)

### 1.1 Qué se mide y qué NO
- Se captura **telemetría del propio equipo** (FortiGate 50E) y **sondas de alcanzabilidad** (ICMP
  a resolutores públicos y al router del ISP). **No se captura tráfico de clientes** ni contenido:
  no hay origen/destino de paquetes de usuarios, ni payloads de aplicación.
- **Conteos de sesión** (`active_sessions`, `new_sessions_per_second`, `tcp_sessions`,
  `udp_sessions`) son **agregados** del FortiGate (p. ej. 140), nunca sesiones individuales ni
  identificadores de usuario.

### 1.2 Campos identificatorios en la base y su tratamiento
| Dato | ¿Presente? | Tratamiento |
|---|---|---|
| IPs de clientes | **No** | nunca se almacenan |
| Serial del equipo | columnas `serial_number`, `device_hostname` y `raw_json` | **REDACTADAS** en el Release (ver §2) |
| MAC (FortiGate y Raspberry) | solo dentro de `telemetry_raw.raw_json` y `speed_tests.raw_json` | **REDACTADAS** en el Release |
| IP pública del sitio | solo dentro de `speed_tests.raw_json` | **REDACTADA** en el Release |
| IPs internas (10.254.0.0/24, 192.168.1.x) | dentro de los `raw_json` | **REDACTADAS** en el Release |
| IPs de sondas | `probe_results.address` | **se conservan** (objetivos de medición, no clientes): 1.1.1.1, 8.8.8.8, 9.9.9.9, 192.168.1.1 |
| Tokens / credenciales | **No** (ninguna columna) | viven solo en `.env` del Pi (no versionado, no en la base) |

Evidencia (esquema): tablas en `app/database/models.py`; columnas de `telemetry_raw` incluyen
`serial_number`, `device_hostname`, `raw_json` (payload crudo de la API). Las 23 variables de
entrada del modelo **no incluyen** ningún campo identificatorio (ver §3.2).

### 1.3 Política de retención (existe)
`config/config.yaml` → `retention.raw_json_keep_days: 3` (servicio `app/services/retention_service.py`):
borra `telemetry_raw.raw_json` (payload grande) en filas de más de 3 días; nunca toca columnas
estructuradas; se pausa si el backup falla. Backups: `backup.keep_days: 7`. **Nota:** por una falla
de backups la retención quedó pausada, por lo que el Release original conservaba `raw_json` en 4.127
filas — resuelto con la redacción de §2.

### 1.4 IPs de gestión / serial en el repositorio
- IP de gestión del FortiGate `10.254.0.254`: en el **repo** (`.env.example`, `app/core/config.py`).
- IP de sonda `192.168.1.1`: en el **repo** (`config/config.yaml`) y en la **base** (`probe_results.address`).
- Serial `FGT50E5619016282`: **no** aparece en el repo; estaba en la base y fue **redactado** en el Release (§2).

---

## 2. Limpieza del Release (qué se redactó y por qué)

El Release publicado (`fortigate_dataset_estudio.db.gz`) se regeneró **redactando a NULL** las
columnas no-estructurales que contenían identificadores (payloads crudos y metadatos de equipo).
El modelo **no usa** ninguna de estas columnas, por lo que las cifras no cambian.

| Tabla.columna redactada | Qué contenía |
|---|---|
| `telemetry_raw.raw_json` | payload crudo del FortiGate: serial, MAC `e8:1c:ba:c2:d6:ff`, IPs internas |
| `telemetry_raw.serial_number` | `FGT50E5619016282` |
| `telemetry_raw.device_hostname` | `FGT50E5619016282` |
| `speed_tests.raw_json` | resultado Ookla: MAC del Pi `B8:27:EB:EA:D3:27`, IP pública del sitio `181.42.179.81`, IP LAN `10.254.0.10`, ISP, URLs |
| `speed_tests.error` | logs de error crudos (JSON) |
| `speed_tests.result_url` | enlaces a resultados speedtest.net (exponen IP pública/ISP al abrirse) |

**Verificación (previa a publicar):**
- **2a — integridad:** comparación por columna (SHA-256) original vs limpia: **todas las columnas de
  estudio son idénticas**, filas idénticas en las 13 tablas; solo difieren las 6 columnas redactadas.
- **2b — reproducibilidad:** las 8 cifras del Cap. 4 se reproducen **exactas** desde la base limpia
  (régimen sin sesgo LSTM 0,197 / RF 0,404 / umbral 0,172 / calendario 0,253; prueba aleatorizada
  LSTM 0,431 / RF 0,484 / umbral 0,146 / oráculo 0,203; Δ = 0,000 en todas).
- **2c — privacidad:** escaneo en **todas** las columnas de la base limpia → **0** MAC reales,
  **0** IP pública (181.42.179.81), **0** serial, **0** MAC del Pi, **0** MAC del FortiGate.
  Se conservan solo las IPs de sonda (objetivos de medición).

**Hashes SHA-256:**
| Archivo | SHA-256 |
|---|---|
| Base ORIGINAL (con payloads crudos) | `073b014d46d46b6c0f98ccb8a2426fc938715af22a52101b28d820571ce7e39d` |
| Base LIMPIA (descomprimida) | `0880dac27e2625d5ddc26c1b599ea0a194d1e3532b3d6e6f13bd00b72c8f4a4d` |
| Asset publicado `fortigate_dataset_estudio.db.gz` (limpio) | `93ee8a624b946b6d61fba2447c85ada1d30d4f58dbed9364d28857afb3af6492` |

---

## 3. Inventario para el anexo técnico

### 3.1 Archivos clave del repositorio
| Ruta | Descripción |
|---|---|
| `app/` | Colector FastAPI + APScheduler + servicios (telemetría, sondas, huecos, backup, retención). |
| `ml/train.py` | Entrenamiento LSTM + líneas base; escribe `run_metadata.json`. |
| `ml/crossval.py` | Validación cruzada walk-forward por evento. |
| `ml/model.py` | Arquitectura LSTM (35.489 parámetros). |
| `ml/baselines.py` | Random Forest, naive de calendario, regla de umbral. |
| `ml/induced.py` | Exclusión de carga inducida (EXP-WANDOWN, colas post-T_f, speed tests). |
| `ml/preprocessing.py` | `SequenceScaler` (fit solo con train) + split temporal. |
| `ml/evaluate.py` | Métricas, selección de umbral (solo validación), lead time, McNemar. |
| `ml/config.py`, `ml/variables.py` | Configuración del modelo y lista de 23 variables. |
| `docs/GUIA_REVISOR.md` | Guía de revisión y reproducción. |
| `docs/modelo/` | Artefactos entrenados (`model.keras`, `metrics.json`, `run_metadata.json`). |
| `docs/resultados/` | `cifras_capitulo4.csv`, `eventos_prueba.csv`, `eventos_prueba_aleatorizado.csv`. |
| `docs/graficos/` | Curvas PR, walk-forward, curva de aprendizaje, lead, línea de tiempo (PNG + CSV). |
| `config/config.yaml` | Configuración no secreta (umbrales, programación, retención). |

### 3.2 Diccionario de las 23 variables de entrada
Fuente REST = API del FortiGate; ICMP = sonda del Pi. Tabla: `telemetry_raw` (device) + `probe_results` (ICMP, pivoteado).

| Variable | Unidad | Fuente | Tabla |
|---|---|---|---|
| latency_ms, jitter_ms, packet_loss_pct | ms, ms, % | REST (SLA WAN1) | telemetry_raw |
| rx_mbps, tx_mbps | Mbps | REST (interfaz) | telemetry_raw |
| rx_utilization_pct, tx_utilization_pct | % | REST (interfaz) | telemetry_raw |
| cpu_pct, memory_pct | % | REST (sistema) | telemetry_raw |
| active_sessions, new_sessions_per_second | conteo, conteo/s | REST (sesiones, agregado) | telemetry_raw |
| cloudflare_latency_ms, cloudflare_jitter_ms, cloudflare_loss_pct | ms, ms, % | ICMP (1.1.1.1) | probe_results |
| google_latency_ms, google_jitter_ms, google_loss_pct | ms, ms, % | ICMP (8.8.8.8) | probe_results |
| quad9_latency_ms, quad9_jitter_ms, quad9_loss_pct | ms, ms, % | ICMP (9.9.9.9) | probe_results |
| isp_router_latency_ms, isp_router_jitter_ms, isp_router_loss_pct | ms, ms, % | ICMP (192.168.1.1) | probe_results |

(Las columnas de estado `link_status`/`sla_status` se reservan y NO se usan como features.)

### 3.3 Corridas de referencia
| run_id | contenido | commit | corte | base (hash .db limpia) |
|---|---|---|---|---|
| `run_20261122T144431Z` | histórico completo | 1183d85 | 2026-11-14T23:59:59Z | fbdd13b… |
| `run_20261122T144340Z` | régimen aleatorizado (desde 2026-11-07) | 1183d85 | 2026-11-14T23:59:59Z | fbdd13b… |
| `crossval_randomized.json` | walk-forward 4 pliegues | 1183d85 | 2026-11-14T23:59:59Z | fbdd13b… |

### 3.4 Comandos de reproducción (desde el Release)
```bash
# 1. Descargar el asset del Release y descomprimir
#    https://github.com/jorgegallardo1995-art/fortigate-predictive-network-lab/releases/tag/tesis-cap4-freeze
gunzip fortigate_dataset_estudio.db.gz && mv fortigate_dataset_estudio.db snap.db

# 2. Histórico completo
python -m ml.train --db snap.db --data-source REAL --until 2026-11-14T23:59:59Z
# 3. Régimen aleatorizado
python -m ml.train --db snap.db --since 2026-11-07 --until 2026-11-14T23:59:59Z
# 4. Walk-forward
python -m ml.crossval --db snap.db --since 2026-11-07 --until 2026-11-14T23:59:59Z --folds 4
```

---

## 4. Conciliación de minutos (base del Release, corte 2026-11-14T23:59:59Z)

| Concepto | Valor |
|---|---|
| Período | 2026-09-01 05:32 → 2026-11-14 23:59 |
| Minutos del período | **107.667** |
| Filas crudas (`telemetry_raw`) | **102.520** |
| Ciclos exitosos (`collection_success=1`) | **94.760** |
| Ciclos fallidos (`=0`) | 7.760 |
| **Minutos netos ausentes** (período − filas) | **5.147** |
| `collection_gaps` (suma reportada) | 13.969 min |

**Conciliación con la Tabla 8 y con `conciliacion_huecos.csv`:**
- Tabla 8 (5.146 min) = período (107.666) − filas (102.520). Coincide con 5.147 (diferencia de 1 min
  por redondeo del período).
- La cifra previa **5.124** era del corte del **8-nov** (período 98.958 min, filas 93.834); la
  diferencia (~23 min) son huecos adicionales acumulados del 8 al 14 de noviembre.
- `collection_gaps` reporta **13.969 min** porque **sobrecuenta**: incluye los **7.760 ciclos fallidos**
  del incidente de IP de la Raspberry (fila presente pero sin telemetría), contados como pérdida y en
  parte dos veces. La cifra correcta de **ausencia física** es la de filas faltantes: **5.147 min**.

---

## Anexo F — notas de clasificación de eventos
- El **período de caída y los 15 min posteriores al restablecimiento se excluyen de ambas clases**
  (código `ml/induced.py` líneas 106–113): la ventana de caída (`EXP-WANDOWN`) y la cola post-T_f +
  recuperación no se etiquetan como positivo ni como negativo.
- **Intervalo mínimo entre eventos** en el régimen aleatorizado: rampas de congestión ~1 h, inyección
  netem ~2 h (`config/config.yaml`: `ramp_random_min_gap_hours: 1`, `netem.random_min_gap_hours: 2`).
- Tablas por evento: `docs/resultados/eventos_prueba.csv` (13 eventos de prueba) y
  `docs/resultados/eventos_prueba_aleatorizado.csv` (74 eventos; el bootstrap usó 75 corridas
  contiguas, una de borde cerca del 7-nov).
