# H14 — Auditoría de privacidad del dataset publicado

`python -m scripts.auditoria_privacidad --db snap.db`

Tablas examinadas (13): alembic_version, collection_gaps, experiments, network_events, research_metadata, system_status, telemetry_features, telemetry_raw, probe_results, telemetry_sla, availability_timeline, speed_tests, load_steps

Patrones: MAC, IPv4 (privada/pública/resolutor), IPv6, serial FGT, email, URL.

## Coincidencias por tabla/columna/patrón (conteo de filas)
| tabla | columna | patrón | filas |
|---|---|---|---|
| collection_gaps | notes | IPv4 | 1 |
| research_metadata | payload | IPv4 | 10 |
| probe_results | address | IPv4 | 410080 |
| telemetry_sla | target | IPv4 | 284280 |

## IPv4 por categoría
- resolutores públicos esperados (sondas ICMP): {'1.1.1.1': 197283, '8.8.8.8': 197283, '9.9.9.9': 197283}
- privadas (RFC1918/link-local): 102522
- **públicas no esperadas: NINGUNA**

## Identificadores conocidos detectados por hash
- **NINGUNO** (0 serial, 0 MAC, 0 IP pública conocidos en la base).

## Columnas NULL (redactadas o vacías): 44
- system_status.id, system_status.timestamp_utc, system_status.fortigate_api_ok, system_status.collector_ok, system_status.scheduler_ok, system_status.database_ok, system_status.last_successful_collection, system_status.next_scheduled_collection, system_status.average_api_latency_ms, system_status.failed_requests_24h, system_status.collector_uptime_seconds, system_status.host_uptime_seconds, system_status.database_size_bytes, system_status.disk_available_bytes, system_status.notes, telemetry_features.rx_drop_rate, telemetry_features.tx_drop_rate, telemetry_raw.device_hostname, telemetry_raw.serial_number, telemetry_raw.rx_drops, telemetry_raw.tx_drops, telemetry_raw.rx_drop_rate, telemetry_raw.tx_drop_rate, telemetry_raw.uptime_seconds, telemetry_raw.temperature_c, telemetry_raw.tcp_sessions, telemetry_raw.udp_sessions, telemetry_raw.vpn_tunnels_up, telemetry_raw.vpn_tunnels_down, telemetry_raw.vpn_rx_bytes, telemetry_raw.vpn_tx_bytes, telemetry_raw.raw_json, telemetry_raw.label, telemetry_raw.failure_type, telemetry_raw.failure_timestamp, telemetry_raw.minutes_before_failure, telemetry_raw.scenario_id, telemetry_raw.experiment_id, telemetry_sla.probe_sessions, speed_tests.error, speed_tests.result_url, speed_tests.raw_json, load_steps.retransmits, load_steps.error

## Naturaleza de la limpieza
- Se aplicó **REDACCIÓN (NULL)** a columnas de payload crudo e identificación (telemetry_raw.raw_json/serial_number/device_hostname, speed_tests.raw_json/error). No es seudonimización (no hay tokens reversibles): los valores se eliminaron.
- Las IPs de sonda ICMP (1.1.1.1/8.8.8.8/9.9.9.9 y 192.168.1.1 privada) se **conservan** a propósito: son objetivos de medición, no datos de clientes.

## Límites de esta auditoría (qué NO detecta)
- No detecta identificadores ofuscados/codificados (base64, hex sin delimitadores), nombres propios en texto libre, ni coordenadas; no inspecciona BLOBs binarios.
- La detección por hash solo cubre los identificadores conocidos incrustados; la detección exacta adicional depende del archivo local `.privacy_known.txt` (no versionado).
- El patrón IPv6 puede solapar con MAC/timestamps; se reporta como indicio, no como certeza.
