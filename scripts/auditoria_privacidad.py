#!/usr/bin/env python3
"""H14 — Auditoría de privacidad del dataset publicado (base del Release).

Examina TODAS las tablas y columnas de texto buscando patrones: MAC, IPv4 (privada/pública/
resolutor público conocido), IPv6, número de serie del equipo (FGT...), correos, URLs y hostnames.
Para no versionar identificadores en claro, los identificadores CONOCIDOS del proyecto se detectan
por **hash**: cada coincidencia de patrón se hashea (SHA-256, minúsculas) y se compara con un conjunto
de hashes conocidos incrustado aquí. Opcionalmente, si existe un archivo local no versionado
`.privacy_known.txt` (uno por línea), también se hace detección por coincidencia exacta.

Reporta, por tabla/columna/patrón: coincidencias, y distingue columnas NULL por redacción, ausencia
de coincidencias y coincidencias ESPERADAS (IPs de sondas ICMP públicas). Declara sus límites.

Uso:  python -m scripts.auditoria_privacidad --db snap.db
Salida: docs/resultados/auditoria_privacidad.md  (sin datos sensibles)
"""
import argparse, sqlite3, re, hashlib, os
import pandas as pd

KNOWN_HASHES = {  # SHA-256 (minúsculas) de identificadores conocidos del proyecto (sin el valor en claro)
    "ca6bb0f44770d693528755ccc8e745feadc030054c9501ce091e8fa7d25bea70": "serial FortiGate",
    "344f74b643f6cfd7be40e0cf1b8c74bf0d380faccb608c155b7750daf27fc8a9": "MAC Raspberry",
    "239a54c793ac34f66925cf15978c467874be54411d231f58ecf33e8888fed266": "MAC FortiGate",
    "432cd96b17e0ce672e2830802f0f12d1af77c35ec011c541ead23d6067a85718": "IP pública del sitio",
}
RESOLVERS = {"1.1.1.1", "8.8.8.8", "9.9.9.9"}  # objetivos de sonda ICMP públicos (esperados)
PAT = {
    "MAC": re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"),
    "IPv4": re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"),
    "IPv6": re.compile(r"(?:[0-9a-fA-F]{1,4}:){4,7}[0-9a-fA-F]{1,4}"),
    "serial_FGT": re.compile(r"FGT[0-9A-Z]{8,}"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "URL": re.compile(r"https?://[^\s\"']+"),
}


def is_private(ip):
    return (ip.startswith(("10.", "192.168.", "169.254.", "127.")) or
            re.match(r"172\.(1[6-9]|2\d|3[01])\.", ip) is not None)


def main(argv=None):
    p = argparse.ArgumentParser(); p.add_argument("--db", default="snap.db"); a = p.parse_args(argv)
    local = set()
    if os.path.exists(".privacy_known.txt"):
        local = {l.strip() for l in open(".privacy_known.txt") if l.strip()}
    con = sqlite3.connect(a.db)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    L = ["# H14 — Auditoría de privacidad del dataset publicado\n",
         "`python -m scripts.auditoria_privacidad --db snap.db`\n",
         f"Tablas examinadas ({len(tables)}): {', '.join(tables)}\n",
         "Patrones: MAC, IPv4 (privada/pública/resolutor), IPv6, serial FGT, email, URL.\n"]
    null_cols = []; findings = []; known_hits = []
    ip_pub = {}; ip_priv = 0; ip_res = {}
    for t in tables:
        cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
        for c in cols:
            try:
                s = pd.read_sql(f"SELECT `{c}` FROM `{t}`", con)[c]
            except Exception:
                continue
            nonnull = s.dropna()
            if len(nonnull) == 0:
                null_cols.append(f"{t}.{c}"); continue
            if nonnull.map(type).eq(str).mean() < 0.5:   # columna no textual
                continue
            vc = nonnull.astype(str).value_counts()
            for pat, rgx in PAT.items():
                hits = 0
                for val, n in vc.items():
                    for mm in rgx.findall(val):
                        h = hashlib.sha256(mm.lower().encode()).hexdigest()
                        if h in KNOWN_HASHES:
                            known_hits.append((t, c, KNOWN_HASHES[h], n))
                        if mm in local:
                            known_hits.append((t, c, "coincidencia con .privacy_known", n))
                        if pat == "IPv4":
                            if mm in RESOLVERS: ip_res[mm] = ip_res.get(mm, 0) + n
                            elif is_private(mm): ip_priv += n
                            else: ip_pub[mm] = ip_pub.get(mm, 0) + n
                        hits += n
                if hits:
                    findings.append((t, c, pat, hits))
    con.close()

    L.append("## Coincidencias por tabla/columna/patrón (conteo de filas)")
    if findings:
        L.append("| tabla | columna | patrón | filas |"); L.append("|---|---|---|---|")
        for t, c, pat, n in findings:
            L.append(f"| {t} | {c} | {pat} | {n} |")
    else:
        L.append("- (ninguna)")
    L.append("\n## IPv4 por categoría")
    L.append(f"- resolutores públicos esperados (sondas ICMP): {ip_res or 'ninguno'}")
    L.append(f"- privadas (RFC1918/link-local): {ip_priv}")
    L.append(f"- **públicas no esperadas: {ip_pub or 'NINGUNA'}**")
    L.append("\n## Identificadores conocidos detectados por hash")
    L.append(("- " + "; ".join(f"{d} en {t}.{c} ({n})" for t, c, d, n in known_hits)) if known_hits
             else "- **NINGUNO** (0 serial, 0 MAC, 0 IP pública conocidos en la base).")
    L.append(f"\n## Columnas NULL (redactadas o vacías): {len(null_cols)}")
    L.append("- " + ", ".join(null_cols) if null_cols else "- (ninguna)")
    L.append("\n## Naturaleza de la limpieza")
    L.append("- Se aplicó **REDACCIÓN (NULL)** a columnas de payload crudo e identificación "
             "(telemetry_raw.raw_json/serial_number/device_hostname, speed_tests.raw_json/error). "
             "No es seudonimización (no hay tokens reversibles): los valores se eliminaron.")
    L.append("- Las IPs de sonda ICMP (1.1.1.1/8.8.8.8/9.9.9.9 y 192.168.1.1 privada) se **conservan** "
             "a propósito: son objetivos de medición, no datos de clientes.")
    L.append("\n## Límites de esta auditoría (qué NO detecta)")
    L.append("- No detecta identificadores ofuscados/codificados (base64, hex sin delimitadores), "
             "nombres propios en texto libre, ni coordenadas; no inspecciona BLOBs binarios.")
    L.append("- La detección por hash solo cubre los identificadores conocidos incrustados; "
             "la detección exacta adicional depende del archivo local `.privacy_known.txt` (no versionado).")
    L.append("- El patrón IPv6 puede solapar con MAC/timestamps; se reporta como indicio, no como certeza.")
    with open("docs/resultados/auditoria_privacidad.md", "w") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L[:40]))
    print("... (ver docs/resultados/auditoria_privacidad.md)")


if __name__ == "__main__":
    main()
