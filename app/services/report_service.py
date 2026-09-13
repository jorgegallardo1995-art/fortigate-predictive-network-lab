"""Technical-report PDF.

A self-contained briefing of the dataset - every variable, its coverage and
basic statistics, the derived features and their formulas, the availability
record, and guidance for building the LSTM. Meant to be handed to an assistant
together with the CSV exports so it can help design the model.

Pure ``reportlab`` (no system libraries) so it also runs on the Raspberry Pi.
"""

from __future__ import annotations

from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import get_config, get_settings
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.core.version import COLLECTOR_VERSION, SCHEMA_VERSION
from app.database.database import session_scope
from app.database.models import DATA_SOURCE_MOCK, DATA_SOURCE_REAL
from app.services import availability_service, research_service
from app.services.feature_engineering import FEATURE_DEFINITIONS
from app.services.statistics_service import dataset_overview, variable_coverage, variable_statistics

logger = get_logger("report")

# unit + one-line description per telemetry_raw variable (for the AI reading this)
VAR_META: dict[str, tuple[str, str]] = {
    "timestamp_utc": ("ISO-8601", "Marca temporal UTC (referencia científica primaria)"),
    "timestamp_local": ("ISO-8601", "Marca temporal en la zona local (secundaria)"),
    "device_hostname": ("texto", "Hostname del FortiGate"),
    "device_model": ("texto", "Modelo (FGT50E)"),
    "serial_number": ("texto", "Serial (anonimizable en export)"),
    "fortios_version": ("texto", "Versión de FortiOS (v6.0.4)"),
    "vdom": ("texto", "VDOM consultado (root)"),
    "wan_interface": ("texto", "Interfaz WAN monitoreada (wan1)"),
    "latency_ms": ("ms", "Latencia agregada (link-monitor FortiGate; fallback ICMP Pi)"),
    "jitter_ms": ("ms", "Jitter agregado"),
    "packet_loss_pct": ("%", "Pérdida de paquetes agregada"),
    "availability": ("%", "Disponibilidad instantánea del enlace (100/0)"),
    "link_status": ("up/down", "Estado físico de la interfaz WAN"),
    "sla_status": ("up/down", "Estado agregado del SLA / link-monitor"),
    "rx_bytes": ("bytes", "Contador acumulado de bytes recibidos en wan1"),
    "tx_bytes": ("bytes", "Contador acumulado de bytes transmitidos en wan1"),
    "rx_packets": ("paquetes", "Contador acumulado de paquetes recibidos"),
    "tx_packets": ("paquetes", "Contador acumulado de paquetes transmitidos"),
    "rx_bps": ("bit/s", "Tasa de bajada (derivada: Δbytes·8/Δt)"),
    "tx_bps": ("bit/s", "Tasa de subida (derivada)"),
    "rx_mbps": ("Mbit/s", "Tasa de bajada en Mbps (derivada)"),
    "tx_mbps": ("Mbit/s", "Tasa de subida en Mbps (derivada)"),
    "interface_speed_mbps": ("Mbit/s", "Velocidad de referencia para % de utilización (contratada: 200)"),
    "rx_utilization_pct": ("%", "Utilización de bajada = rx_bps / capacidad"),
    "tx_utilization_pct": ("%", "Utilización de subida"),
    "rx_errors": ("errores", "Contador acumulado de errores de recepción"),
    "tx_errors": ("errores", "Contador acumulado de errores de transmisión"),
    "rx_drops": ("descartes", "Contador de descartes RX (NULL en FortiOS 6.0.4)"),
    "tx_drops": ("descartes", "Contador de descartes TX (NULL en FortiOS 6.0.4)"),
    "rx_error_rate": ("ratio", "Δerrores_rx / Δpaquetes_rx (derivada)"),
    "tx_error_rate": ("ratio", "Δerrores_tx / Δpaquetes_tx (derivada)"),
    "rx_drop_rate": ("ratio", "Δdescartes_rx / Δpaquetes_rx (NULL en este equipo)"),
    "tx_drop_rate": ("ratio", "Δdescartes_tx / Δpaquetes_tx (NULL en este equipo)"),
    "cpu_pct": ("%", "Uso de CPU del FortiGate"),
    "memory_pct": ("%", "Uso de memoria del FortiGate"),
    "disk_pct": ("%", "Uso de disco (reporta 0 en el 50E)"),
    "uptime_seconds": ("s", "Uptime del FortiGate (NULL en FortiOS 6.0.4 vía REST)"),
    "temperature_c": ("°C", "Temperatura (NULL: el 50E no tiene sensor)"),
    "active_sessions": ("sesiones", "Sesiones activas en el FortiGate"),
    "new_sessions_per_second": ("ses/s", "Tasa de establecimiento de sesiones"),
    "tcp_sessions": ("sesiones", "Sesiones TCP (NULL en este equipo)"),
    "udp_sessions": ("sesiones", "Sesiones UDP (NULL en este equipo)"),
    "interface_status": ("up/down", "Estado administrativo/operacional de wan1"),
    "interface_speed": ("texto", "Velocidad negociada del enlace"),
    "interface_duplex": ("full/half", "Dúplex del enlace"),
    "interface_mtu": ("bytes", "MTU configurado de wan1"),
    "default_route_present": ("bool", "Existe ruta por defecto activa (indicador de WAN operativa)"),
    "vpn_tunnels_up": ("túneles", "VPN activos (NULL: sin VPN configurada)"),
    "vpn_tunnels_down": ("túneles", "VPN caídos (NULL: sin VPN)"),
    "vpn_rx_bytes": ("bytes", "Tráfico VPN RX (NULL: sin VPN)"),
    "vpn_tx_bytes": ("bytes", "Tráfico VPN TX (NULL: sin VPN)"),
    "api_latency_ms": ("ms", "Tiempo de respuesta de la API REST del FortiGate"),
    "collection_duration_ms": ("ms", "Duración total del ciclo de adquisición"),
    "collection_success": ("bool", "El ciclo se completó correctamente"),
}

LSTM_GUIDANCE = [
    ("Objetivo del modelo",
     "Predicción temprana de degradación/falla en el enlace WAN. Framing recomendado: "
     "clasificación de secuencia (0=NORMAL, 1=DEGRADATION, 2=FAILURE) y/o regresión de "
     "'minutes_before_failure'. La ventana de anticipación (p. ej. predecir falla en los "
     "próximos 5-15 min) se define al etiquetar."),
    ("Etiquetas",
     "Los campos label, failure_type, failure_timestamp, minutes_before_failure, scenario_id, "
     "experiment_id están TODOS en NULL. Se asignan manualmente a partir de network_events y de "
     "los experimentos controlados. NO derivar labels de los umbrales del dashboard "
     "(son solo visuales) ni de reglas automáticas."),
    ("Construcción de secuencias",
     "Rejilla regular de 60 s. Usar availability_timeline como máscara: NUNCA construir una "
     "ventana que cruce un tramo collector_up=0. Segmentar el dataset por tramos continuos."),
    ("Partición train/val/test",
     "Split TEMPORAL, nunca aleatorio: entrenamiento con el período más antiguo, "
     "validación y test con los más recientes. Reportar el corte exacto."),
    ("Variables de entrada",
     "~40 variables RAW numéricas con datos + ~36 features de ventana (5/10/15 min). "
     "NO alimentar los contadores acumulados (rx_bytes, tx_bytes, rx_packets, tx_packets, "
     "rx_errors, tx_errors) directamente: usar las tasas derivadas. Excluir identificación "
     "(texto constante) y las columnas siempre-NULL de este equipo (rx_drops, tx_drops, "
     "rx_drop_rate, tx_drop_rate, uptime_seconds, temperature_c, tcp_sessions, udp_sessions, "
     "vpn_*)."),
    ("Normalización",
     "Ajustar el scaler SOLO con el set de entrenamiento, por variable. Guardar los parámetros."),
    ("Valores faltantes",
     "La primera muestra tras cada arranque no tiene deltas (rx_mbps, etc. = NULL). "
     "Decidir: descartar esas filas o imputar. Documentarlo."),
    ("Desbalance de clases",
     "Las fallas serán raras. Usar class weights / focal loss / muestreo estratificado. "
     "Reportar la matriz de confusión y métricas por clase, no solo accuracy."),
    ("Doble medición de calidad",
     "latency/jitter/loss agregados vienen del FortiGate (link-monitor). probe_results tiene "
     "la medición independiente de la Pi por destino (cloudflare/google/quad9/isp_router). "
     "Útil como feature adicional o para validación cruzada."),
]


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) >= 1e6 or (v != 0 and abs(v) < 1e-3):
            return f"{v:.3e}"
        return f"{v:,.3f}".rstrip("0").rstrip(".")
    return str(v)


def build_technical_report(data_source: str | None = None) -> Any:
    settings = get_settings()
    cfg = get_config()
    ds = data_source or (DATA_SOURCE_MOCK if settings.mock_mode else DATA_SOURCE_REAL)

    with session_scope() as session:
        meta = research_service.build_research_metadata(session)
        overview = dataset_overview(session, ds)
        var_stats = {v["variable"]: v for v in variable_statistics(session, ds)}
        coverage = variable_coverage(session, ds)
        av = availability_service.summary(session, ds)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("Small", parent=styles["Normal"], fontSize=7.5, leading=9.5))
    styles.add(ParagraphStyle("Cell", parent=styles["Normal"], fontSize=7, leading=8.5))
    styles.add(ParagraphStyle("H1c", parent=styles["Heading1"], fontSize=15, spaceBefore=4))
    h2 = styles["Heading2"]
    body = styles["BodyText"]

    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    path = settings.exports_dir / f"technical_report_{ds.lower()}_{stamp}.pdf"
    settings.exports_dir.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        topMargin=16 * mm, bottomMargin=16 * mm, leftMargin=15 * mm, rightMargin=15 * mm,
        title="Informe técnico del dataset - Predictive Network Lab",
    )
    S: list[Any] = []

    def p(text: str, style=body) -> None:
        S.append(Paragraph(text, style))

    def table(data: list[list[Any]], widths: list[float], header: bool = True) -> None:
        t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
        style = [
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8c8c8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        if header:
            style += [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a2331")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        t.setStyle(TableStyle(style))
        S.append(t)

    # --- cover ---
    p(cfg.get("app.display_name", "Predictive Network Lab"), styles["Title"])
    p(cfg.get("app.subtitle", ""), styles["Italic"])
    p(f"Informe técnico del dataset · generado {utc_now():%Y-%m-%d %H:%M} UTC", styles["Small"])
    S.append(Spacer(1, 6 * mm))
    p("1. Objetivo y contexto", h2)
    p("Fase experimental de una tesis doctoral sobre continuidad operativa digital y "
      "predicción temprana de fallas en redes de telecomunicaciones mediante redes "
      "neuronales recurrentes (LSTM). Esta plataforma <b>captura, almacena, valida y "
      "exporta</b> un dataset temporal multivariado, trazable y reproducible. No entrena "
      "modelos: este informe resume el dataset para diseñar el modelo por separado.")

    # --- setup ---
    p("2. Setup de captura", h2)
    dev, prov, acq = meta["device"], meta["provenance"], meta["acquisition"]
    table([
        ["Parámetro", "Valor"],
        ["Dispositivo", f"{dev['model']} · FortiOS {dev['fortios_version']} · interfaz {dev['wan_interface']}"],
        ["Colector", f"{prov['collector_architecture']} · {prov['os']} · Python {prov['python_version']}"],
        ["Versión colector / esquema", f"{COLLECTOR_VERSION} / {SCHEMA_VERSION} · git {prov.get('git_commit', '—')}"],
        ["Intervalo de muestreo", f"{acq['sampling_interval_seconds']} s"],
        ["Zona horaria", acq["timezone"]],
        ["Ventanas de features", " / ".join(str(w) for w in acq["feature_windows_minutes"]) + " min"],
        ["Fuentes de calidad de red",
         "FortiGate link-monitor 'Internet' (1.1.1.1/8.8.8.8/9.9.9.9) + ICMP desde la Pi por destino"],
    ], [45 * mm, 130 * mm])

    # --- dataset summary ---
    p("3. Resumen del dataset", h2)
    g = overview["gaps"]
    table([
        ["Métrica", "Valor"],
        ["Fuente de datos", ds],
        ["Período", f"{overview['first_observation']}  →  {overview['last_observation']}"],
        ["Días de captura", _fmt(overview["capture_days"])],
        ["Observaciones (telemetry_raw)", f"{overview['total_observations']:,}"],
        ["Muestras esperadas (rejilla 60s)", f"{overview['expected_samples']:,}"],
        ["Muestras perdidas", f"{overview['lost_samples']:,}"],
        ["Completitud", f"{overview['completeness_pct']} %"],
        ["Registros válidos / incompletos", f"{overview['valid_records']:,} / {overview['incomplete_records']:,}"],
        ["Gaps de captura", f"{g['count']} ({g['total_lost_samples']:,} muestras, {g['total_gap_seconds']/3600:.1f} h)"],
        ["Filas telemetry_features / telemetry_sla / probe_results",
         f"{coverage['related_tables']['telemetry_features']:,} / "
         f"{coverage['related_tables']['telemetry_sla']:,} / "
         f"{coverage['related_tables']['probe_results']:,}"],
        ["Experimentos registrados", str(overview["experiments_total"])],
    ], [70 * mm, 105 * mm])

    # --- availability ---
    p("4. Disponibilidad y continuidad operativa", h2)
    if av.get("total_slots"):
        p(f"Timeline continuo de {av['total_slots']:,} slots de {av['slot_minutes']:.0f} min. "
          f"Disponibilidad del colector <b>{av['collector_availability_pct']} %</b>; "
          f"disponibilidad de red medida <b>{av['network_availability_pct']} %</b> "
          f"(solo sobre slots con captura — durante un outage network_up es NULL).", styles["Small"])
        rows = [["Inicio (UTC)", "Fin (UTC)", "Duración", "Causa"]]
        for o in sorted(av["outages"], key=lambda x: -x["minutes"])[:15]:
            rows.append([o["start"][:16], o["end"][:16],
                         f"{o['minutes']/60:.1f} h" if o["minutes"] >= 60 else f"{o['minutes']:.0f} min",
                         o["reason"]])
        table(rows, [40 * mm, 40 * mm, 25 * mm, 70 * mm])
    else:
        p("Timeline de disponibilidad aún no construido.", styles["Small"])

    S.append(PageBreak())

    # --- RAW variables by family ---
    p("5. Inventario de variables RAW (telemetry_raw)", h2)
    p("Cobertura = % de observaciones con valor. Estadísticos sobre todo el dataset.", styles["Small"])
    for fam in coverage["families"]:
        p(fam["family"], styles["Heading3"])
        rows = [["Variable", "Unidad", "Cob.%", "Mín", "Máx", "Media", "Std", "Nulos%", "Descripción"]]
        for v in fam["variables"]:
            st = var_stats.get(v["variable"], {})
            unit, desc = VAR_META.get(v["variable"], ("", ""))
            rows.append([
                v["variable"], unit, _fmt(v["coverage_pct"]),
                _fmt(st.get("min")), _fmt(st.get("max")), _fmt(st.get("mean")),
                _fmt(st.get("std")), _fmt(st.get("null_pct")),
                Paragraph(desc, styles["Cell"]),
            ])
        table(rows, [30 * mm, 14 * mm, 11 * mm, 16 * mm, 16 * mm, 16 * mm, 14 * mm, 12 * mm, 46 * mm])
        S.append(Spacer(1, 2 * mm))

    S.append(PageBreak())

    # --- derived features ---
    p("6. Variables derivadas (telemetry_features)", h2)
    p(f"{len(FEATURE_DEFINITIONS)} columnas de feature, calculadas en ventanas de "
      f"{'/'.join(str(w) for w in acq['feature_windows_minutes'])} min. "
      "Cada fila de telemetry_features es (timestamp, ventana).", styles["Small"])
    rows = [["Feature", "Variable origen", "Ventana", "Fórmula"]]
    for d in FEATURE_DEFINITIONS:
        rows.append([d["name"], d["source"], d["window"], Paragraph(d["formula"], styles["Cell"])])
    table(rows, [34 * mm, 34 * mm, 20 * mm, 87 * mm])

    S.append(PageBreak())

    # --- SLA / ICMP ---
    p("7. Calidad de red por destino", h2)
    p("telemetry_sla (medido por el FortiGate) y probe_results (ICMP desde la Pi). "
      "FortiOS 6.0.4 permite un solo link-monitor por interfaz, de ahí un único agregado "
      "'fortigate_wan1'; el detalle por destino viene del prober de la Pi.", styles["Small"])
    rows = [["Tabla", "Etiqueta / destino", "Filas"]]
    for x in coverage.get("sla_by_label", []):
        rows.append(["telemetry_sla", x["label"], f"{x['records']:,}"])
    for x in coverage.get("icmp_by_label", []):
        rows.append(["probe_results", x["label"], f"{x['records']:,}"])
    table(rows, [40 * mm, 60 * mm, 30 * mm])

    # --- ML labelling ---
    p("8. Campos de etiquetado (todos en NULL)", h2)
    table([
        ["Campo", "Uso previsto"],
        ["label", "0=NORMAL · 1=DEGRADATION · 2=FAILURE (asignación manual)"],
        ["failure_type", "Categoría de la falla"],
        ["failure_timestamp", "Instante de la falla de referencia"],
        ["minutes_before_failure", "Minutos hasta la falla (target de regresión / anticipación)"],
        ["scenario_id", "NORMAL / LATENCY_DEGRADATION / PACKET_LOSS / CONGESTION / WAN_FAILURE / RECOVERY"],
        ["experiment_id", "Vínculo al experimento controlado"],
    ], [40 * mm, 135 * mm])

    S.append(PageBreak())

    # --- guidance ---
    p("9. Guía para el modelo LSTM", h2)
    for title, text in LSTM_GUIDANCE:
        p(f"<b>{title}.</b> {text}", styles["Small"])
        S.append(Spacer(1, 1.5 * mm))

    # --- exports ---
    p("10. Archivos de exportación disponibles (CSV)", h2)
    table([
        ["Export", "Contenido"],
        ["raw_dataset", "telemetry_raw completo, SLA por destino pivotado. 1 fila / minuto."],
        ["features_dataset", "telemetry_features: features de ventana, 1 fila / (minuto, ventana)."],
        ["availability_dataset", "Timeline continuo sin huecos: collector_up / network_up / reason."],
        ["events_dataset", "network_events: timeline operacional."],
        ["gaps_dataset", "collection_gaps: interrupciones con causa."],
        ["experiments_dataset", "experiments: ventanas de experimentos controlados."],
        ["icmp_dataset", "probe_results: ICMP por destino desde la Pi."],
        ["data_dictionary", "Diccionario completo: nombre, origen, ventana, fórmula de cada columna."],
    ], [40 * mm, 135 * mm])
    p("Todos disponibles en /dataset o GET /api/export/&lt;kind&gt;. Adjuntar este PDF + "
      "raw_dataset + features_dataset + availability_dataset + data_dictionary para diseñar el modelo.",
      styles["Small"])

    doc.build(S, onFirstPage=_footer, onLaterPages=_footer)
    logger.info("technical report written: %s (%d KB)", path.name, path.stat().st_size // 1024)
    return path


def _footer(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(15 * mm, 10 * mm, "Predictive Network Lab - informe tecnico del dataset")
    canvas.drawRightString(195 * mm, 10 * mm, f"pag. {doc.page}")
    canvas.restoreState()


_ARCHITECTURE_CODE = """Sequential([
    Input(shape=(window_steps, n_features)),
    LSTM(lstm1_units, return_sequences=True),
    Dropout(dropout),
    LSTM(lstm2_units, return_sequences=False),
    Dropout(dropout),
    Dense(dense_units, activation=dense_activation),
    Dropout(dropout),
    Dense(1, activation=output_activation),
])
model.compile(optimizer=Adam(learning_rate=learning_rate), loss=loss,
              metrics=["accuracy", Precision(), Recall(), AUC()])"""

_PIPELINE_MODULES = [
    ("ml/data_loading.py", "Lee telemetry_raw + probe_results directo de SQLite (solo lectura, SQL plano). Sin dependencia de app/."),
    ("ml/labeling.py", "y_t = 1 si ocurre una falla confirmada en (t, t+horizon_minutes]. Solo desde failure_timestamp de experimentos u eventos confirmados - nunca de umbrales del dashboard."),
    ("ml/segments.py", "Particiona el dataset en tramos continuos (sin huecos > gap_tolerance_seconds) para no construir ventanas que crucen una interrupción de recolección."),
    ("ml/sequences.py", "Construye (N, window_steps, n_features). Descarta cualquier ventana con NaN en algún paso - nunca imputa/fabrica datos faltantes."),
    ("ml/preprocessing.py", "Split temporal 70/15/15 (nunca aleatorio, nunca mezcla cronología). StandardScaler ajustado SOLO con train."),
    ("ml/model.py", "Arquitectura LSTM exacta (ver abajo). build_model(cfg) - TensorFlow importado de forma perezosa."),
    ("ml/baselines.py", "Regla de umbral reactiva + Random Forest sobre features aplanadas, para comparar contra el LSTM."),
    ("ml/evaluate.py", "Accuracy/precision/recall/F1/ROC-AUC/PR-AUC/matriz de confusión/falsas alarmas por día/Lead Time."),
    ("ml/train.py", "CLI end-to-end que orquesta todo lo anterior y escribe run_metadata.json + metrics.json + history.json + model.keras."),
]


def _loss_chart(history: dict[str, list[float]]):
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.shapes import Drawing, String

    loss = history.get("loss") or []
    val_loss = history.get("val_loss") or []
    if not loss:
        return None
    epochs = list(range(1, len(loss) + 1))
    series = [list(zip(epochs, loss, strict=True))]
    labels = ["loss (train)"]
    if val_loss and len(val_loss) == len(loss):
        series.append(list(zip(epochs, val_loss, strict=True)))
        labels.append("val_loss")

    d = Drawing(460, 190)
    lp = LinePlot()
    lp.x, lp.y = 45, 30
    lp.height, lp.width = 140, 400
    lp.data = series
    palette = [colors.HexColor("#3ea6ff"), colors.HexColor("#f85149")]
    for i, _ in enumerate(series):
        lp.lines[i].strokeColor = palette[i % len(palette)]
        lp.lines[i].strokeWidth = 1.2
    lp.xValueAxis.valueMin = 1
    lp.xValueAxis.valueMax = max(epochs)
    ymax = max([*loss, *val_loss]) if (loss or val_loss) else 1.0
    lp.yValueAxis.valueMin = 0
    lp.yValueAxis.valueMax = ymax * 1.1 if ymax > 0 else 1.0
    d.add(lp)
    for i, lbl in enumerate(labels):
        d.add(String(45 + i * 100, 178, lbl, fontSize=7, fillColor=palette[i % len(palette)]))
    return d


def build_run_report(run_id: str) -> Any:
    """Full documentation PDF for one ml/train.py run: architecture, pipeline code
    summary, dataset/labeling used, training curve, confusion matrix and metrics.
    Reads only the JSON artefacts under data/models/<run_id>/ - no TensorFlow
    dependency, so it also runs on the Raspberry Pi once a run is copied there.
    """
    import json

    from app.services import model_service

    run = model_service.get_run(run_id)
    if run is None:
        raise FileNotFoundError(f"no run found: {run_id}")
    meta = run["metadata"]
    metrics = run.get("metrics") or {}
    cfg = meta.get("config", {})

    settings = get_settings()
    history_path = settings.data_dir / "models" / run_id / "history.json"
    history = json.loads(history_path.read_text()) if history_path.is_file() else {}

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("Small", parent=styles["Normal"], fontSize=7.5, leading=9.5))
    styles.add(ParagraphStyle("Cell", parent=styles["Normal"], fontSize=7, leading=8.5))
    styles.add(ParagraphStyle("Mono", parent=styles["Normal"], fontName="Courier", fontSize=7.5, leading=9.5))
    h2 = styles["Heading2"]
    body = styles["BodyText"]

    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    path = settings.exports_dir / f"model_run_report_{run_id}_{stamp}.pdf"
    settings.exports_dir.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        topMargin=16 * mm, bottomMargin=16 * mm, leftMargin=15 * mm, rightMargin=15 * mm,
        title=f"Informe de entrenamiento {run_id} - Predictive Network Lab",
    )
    S: list[Any] = []

    def p(text: str, style=body) -> None:
        S.append(Paragraph(text, style))

    def table(data: list[list[Any]], widths: list[float], header: bool = True) -> None:
        t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
        style = [
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8c8c8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        if header:
            style += [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a2331")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        t.setStyle(TableStyle(style))
        S.append(t)

    # --- cover ---
    p("Predictive Network Lab", styles["Title"])
    p(f"Informe de entrenamiento LSTM &mdash; {run_id}", styles["Italic"])
    S.append(Spacer(1, 3 * mm))
    p(f"Generado: {utc_now().isoformat()} &middot; Fuente de datos: <b>{meta.get('data_source')}</b> "
      f"&middot; git commit: <code>{meta.get('git_commit')}</code>", styles["Small"])

    if meta.get("synthetic"):
        p("<b>ADVERTENCIA: corrida con datos SINTETICOS (inyectados).</b> Valida solo la mecanica "
          "del pipeline. NO citar como resultado real de tesis.", styles["Small"])
    if meta.get("pilot_window_since"):
        p(f"<b>Ventana piloto:</b> analisis acotado a telemetria desde {meta['pilot_window_since']}. "
          "Reportar como experimento piloto de corta duracion, no como generalizacion sobre "
          "todo el historico.", styles["Small"])
    seq = meta.get("sequences", {})
    if not meta.get("synthetic") and (seq.get("train_positives") or 0) == 0:
        p("<b>ADVERTENCIA: 0 ejemplos positivos en entrenamiento.</b> El modelo no pudo aprender "
          "el patron precursor de falla; las metricas de test son degeneradas por diseno "
          "(el pipeline las reporta igual, sin ocultar el resultado).", styles["Small"])
    S.append(Spacer(1, 4 * mm))

    # --- 1. architecture ---
    p("1. Arquitectura del modelo", h2)
    p("LSTM apilado con regularizacion por dropout, siguiendo la especificacion metodologica "
      "(Cap. 3.16) del proyecto de tesis.", styles["Small"])
    table([
        ["Capa", "Configuracion"],
        ["Input", f"({cfg.get('window_steps') or meta.get('sequences', {}).get('window_steps', '15')}, {cfg.get('n_features')})"],
        ["LSTM 1", f"{cfg.get('lstm1_units')} unidades, return_sequences=True"],
        ["Dropout", f"{cfg.get('dropout')}"],
        ["LSTM 2", f"{cfg.get('lstm2_units')} unidades, return_sequences=False"],
        ["Dropout", f"{cfg.get('dropout')}"],
        ["Dense", f"{cfg.get('dense_units')} unidades, activacion {cfg.get('dense_activation')}"],
        ["Dropout", f"{cfg.get('dropout')}"],
        ["Dense (salida)", f"1 unidad, activacion {cfg.get('output_activation')}"],
        ["Parametros entrenables", f"{meta.get('model_params'):,}" if meta.get("model_params") else "—"],
        ["Loss / Optimizador", f"{cfg.get('loss')} / Adam(lr={cfg.get('learning_rate')})"],
        ["Batch size / Max epochs", f"{cfg.get('batch_size')} / {cfg.get('max_epochs')}"],
        ["EarlyStopping", f"patience={cfg.get('early_stopping_patience')}, restore_best_weights=True"],
        ["Seed", f"{cfg.get('seed')}"],
    ], [45 * mm, 130 * mm])
    S.append(Spacer(1, 3 * mm))
    p("Definicion exacta (ml/model.py):", styles["Small"])
    S.append(Paragraph(_ARCHITECTURE_CODE.replace("\n", "<br/>").replace(" ", "&nbsp;"), styles["Mono"]))

    S.append(PageBreak())

    # --- 2. pipeline / code ---
    p("2. Pipeline de entrenamiento (paquete ml/)", h2)
    p("Paquete desacoplado de la app de recoleccion (nunca corre en la Raspberry Pi); "
      f"TensorFlow {meta.get('tensorflow_version')}, Python {meta.get('python_version')}, "
      f"{meta.get('platform')}.", styles["Small"])
    rows = [["Modulo", "Rol"]]
    for name, desc in _PIPELINE_MODULES:
        rows.append([name, Paragraph(desc, styles["Cell"])])
    table(rows, [38 * mm, 137 * mm])

    S.append(PageBreak())

    # --- 3. dataset & labeling ---
    p("3. Datos usados en esta corrida", h2)
    period = meta.get("dataset_period", {})
    table([
        ["Periodo de datos", f"{period.get('start')} -> {period.get('end')}"],
        ["Filas totales", f"{period.get('total_rows'):,}" if period.get("total_rows") else "0"],
        ["Fallas candidatas (T_f)", str(len(meta.get("failure_instants", [])))],
        ["Instantes de falla",
         Paragraph("<br/>".join(meta.get("failure_instants", [])) or "—", styles["Cell"])],
    ], [45 * mm, 130 * mm], header=False)
    S.append(Spacer(1, 3 * mm))

    p("Etiquetado (ground truth)", styles["Heading3"])
    lab = meta.get("labeling", {})
    table([
        ["Filas totales", "Positivas", "Negativas", "% positivas", "Fallas distintas"],
        [f"{lab.get('total_rows', 0):,}", f"{lab.get('positive_rows', 0):,}",
         f"{lab.get('negative_rows', 0):,}", _fmt(lab.get("positive_pct")),
         str(lab.get("distinct_failures", 0))],
    ], [35 * mm, 35 * mm, 35 * mm, 35 * mm, 35 * mm])
    S.append(Spacer(1, 3 * mm))

    p("Segmentacion continua y particion temporal (70/15/15, sin aleatorizar)", styles["Heading3"])
    segs = meta.get("segments", {})
    table([
        ["Segmentos continuos", "Filas totales", "Segmento mas largo"],
        [str(segs.get("n_segments", 0)), f"{segs.get('total_rows', 0):,}", f"{segs.get('longest_segment_rows', 0):,}"],
    ], [55 * mm, 55 * mm, 55 * mm])
    S.append(Spacer(1, 2 * mm))
    cutoffs = meta.get("split_cutoffs", {})
    table([
        ["Particion", "n secuencias", "Positivas", "Corte cronologico"],
        ["train", str(seq.get("train", 0)), str(seq.get("train_positives", 0)), "inicio -> " + str(cutoffs.get("train_end"))],
        ["val", str(seq.get("val", 0)), str(seq.get("val_positives", 0)), str(cutoffs.get("train_end")) + " -> " + str(cutoffs.get("val_end"))],
        ["test", str(seq.get("test", 0)), str(seq.get("test_positives", 0)), str(cutoffs.get("val_end")) + " -> fin"],
    ], [25 * mm, 30 * mm, 25 * mm, 90 * mm])
    S.append(Spacer(1, 3 * mm))
    p(f"Scaler (StandardScaler) ajustado <b>solo con TRAIN</b>. "
      f"class_weight (calculado solo con TRAIN, 'balanced'): {meta.get('class_weight')}.", styles["Small"])

    S.append(PageBreak())

    # --- 4. training curve ---
    p("4. Curva de entrenamiento", h2)
    if history.get("loss"):
        p(f"{len(history['loss'])} epocas ejecutadas (max {cfg.get('max_epochs')}, "
          f"EarlyStopping patience={cfg.get('early_stopping_patience')}).", styles["Small"])
        chart = _loss_chart(history)
        if chart is not None:
            S.append(chart)
        last = {k: v[-1] for k, v in history.items() if v}
        first = {k: v[0] for k, v in history.items() if v}
        table([
            ["Metrica (TRAIN)", "Epoca 1", "Ultima epoca"],
            *[[k, _fmt(first.get(k)), _fmt(last.get(k))]
              for k in ("loss", "accuracy", "precision", "recall", "auc") if k in last],
        ], [45 * mm, 65 * mm, 65 * mm])
    else:
        p("Sin historial de entrenamiento disponible para esta corrida.", styles["Small"])

    S.append(PageBreak())

    # --- 5. results ---
    p("5. Resultados (TEST)", h2)
    lstm_test = metrics.get("lstm_test") or {}
    cm = lstm_test.get("confusion_matrix", {})
    if lstm_test:
        table([
            ["", "Prediccion: normal", "Prediccion: falla"],
            ["Real: normal", str(cm.get("tn", "—")), str(cm.get("fp", "—"))],
            ["Real: falla", str(cm.get("fn", "—")), str(cm.get("tp", "—"))],
        ], [45 * mm, 55 * mm, 55 * mm])
        S.append(Spacer(1, 3 * mm))

        rows = [["Modelo", "Accuracy", "Precision", "Recall", "F1", "ROC-AUC", "PR-AUC", "Falsas alarmas/dia"]]
        rows.append(["LSTM", *[_fmt(lstm_test.get(k)) for k in
                                ("accuracy", "precision", "recall", "f1", "roc_auc", "pr_auc", "false_alarms_per_day")]])
        for name, key in (("Regla de umbral", "threshold_rule"), ("Random Forest", "random_forest")):
            b = metrics.get("baselines", {}).get(key)
            if b:
                rows.append([name, *[_fmt(b.get(k)) for k in
                                      ("accuracy", "precision", "recall", "f1", "roc_auc", "pr_auc", "false_alarms_per_day")]])
        table(rows, [30 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 22 * mm])
        S.append(Spacer(1, 3 * mm))
        p(f"Umbral de decision seleccionado (en VAL): <b>{meta.get('selected_threshold')}</b>", styles["Small"])
    else:
        p("Sin metricas de test (particion test vacia o no evaluada).", styles["Small"])

    S.append(Spacer(1, 4 * mm))
    p("Lead time (anticipacion real de alerta)", styles["Heading3"])
    lt = metrics.get("lead_time", {})
    table([
        ["Eventos evaluados", "Detectados", "Perdidos", "Lead time medio", "Min", "Max"],
        [str(lt.get("events_evaluated", 0)), str(lt.get("events_detected", 0)),
         str(lt.get("events_missed", 0)), _fmt(lt.get("mean_lead_time_minutes")),
         _fmt(lt.get("min_lead_time_minutes")), _fmt(lt.get("max_lead_time_minutes"))],
    ], [30 * mm, 25 * mm, 22 * mm, 30 * mm, 20 * mm, 20 * mm])

    doc.build(S, onFirstPage=_footer, onLaterPages=_footer)
    logger.info("model run report written: %s (%d KB)", path.name, path.stat().st_size // 1024)
    return path
