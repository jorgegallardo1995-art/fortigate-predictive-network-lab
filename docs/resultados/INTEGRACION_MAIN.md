# Integración en `main` de los scripts y evidencia de reproducibilidad

Objetivo: que los scripts y resultados citados por la tesis (§4.9 y Apéndice F) sean
alcanzables desde la rama principal, además de en sus etiquetas. Se hizo con *merges*
normales `--no-ff`, sin reescribir historial y sin mover ninguna etiqueta.

## Hashes

| Punto | Commit |
|---|---|
| `main` antes de integrar | `f7e186f86d547ce039db620eacceadc7a707b199` |
| Merge 1 — `reproducibilidad-bootstrap` (cita 6f7962d, tag `tesis-reproducibilidad-v1`) | `6306b3506e815a027fc7abc966fa4154332d6031` |
| Merge 2 — `cierre-evidencia-v70` (cita 770d89c, tag `tesis-cierre-evidencia-v1`) | `d3846baab6b6e27fc2be7013cbabe962dd33c34c` |
| HEAD de la rama `integrar-reproducibilidad` | `d3846baab6b6e27fc2be7013cbabe962dd33c34c` |

Estructura previa (los dos commits de rama ya estaban encadenados; `cierre` contenía a `repro`):

```
3ef4980 ──── f7e186f  (main)   ← DECLARACION_DATOS.md con hashes v2
   └── 6f7962d  (reproducibilidad-bootstrap)
         └── 770d89c  (cierre-evidencia-v70)
```

## Merges y conflictos

- **Merge 1** (`reproducibilidad-bootstrap`): estrategia `ort`, **sin conflictos**.
- **Merge 2** (`cierre-evidencia-v70`): estrategia `ort`, **sin conflictos**.
- Verificación previa de solapamiento: `main` solo había modificado `docs/DECLARACION_DATOS.md`
  desde el ancestro común `3ef4980`; las ramas no tocaron ese archivo (solo añadieron archivos
  nuevos y 28 líneas al `README.md`, que `main` no había tocado). No hubo intersección de cambios.

## Archivos añadidos a `main` (21 nuevos + 1 modificado)

Scripts (6): `bootstrap_prauc_diferencias.py`, `bootstrap_prauc_puntual_vs_media.py`,
`auditoria_ausencia_datos.py`, `auditoria_privacidad.py`, `falsas_alarmas_validacion_prueba.py`,
`figura10_desde_registro.py`.

Entorno: `requirements-cap4.txt`.

Resultados en `docs/resultados/`: `bootstrap_prauc.{json,csv}`, `bootstrap_prauc_tabla.md`,
`bootstrap_puntual_vs_media.{json,csv,md}`, `auditoria_ausencia_datos.md`,
`auditoria_privacidad.md`, `falsas_alarmas_validacion_prueba.md`, `cronologia_configuraciones.md`,
`RESUMEN_CIERRE_EVIDENCIA.md`, `registro_experimentos_20261108.csv` (+ su README).

Gráfico: `docs/graficos/figura10_registro_eventos.png`.

Modificado: `README.md` (sección «Reproducir resultados del Capítulo 4», +28 líneas de las ramas).

## Verificaciones (Tarea 3)

| # | Verificación | Resultado |
|---|---|---|
| T3.1 | Presencia de los 6 scripts + `requirements-cap4.txt` + todos los `docs/resultados/` | **CUMPLE** (todos presentes) |
| T3.2 | Scripts/resultados **idénticos** a los commits citados (`git diff` vacío vs 6f7962d y 770d89c) | **CUMPLE** (idénticos) |
| T3.3 | `ml/ app/ config/` sin cambios vs `tesis-cap4-freeze-v3` (9b8302e) | **CUMPLE** (sin cambios) |
| T3.4 | Las 5 etiquetas apuntan a los mismos commits | **CUMPLE** (ver abajo) |
| — | `DECLARACION_DATOS.md` conserva los hashes v2 (`0880dac2…2c8f4a4d`, `93ee8a62…b3af6492`) y no reintroduce los v1 | **CUMPLE** (2 coincidencias v2, 0 v1) |
| T3.5 | `README.md` incluye la sección Cap. 4 y sus rutas existen tras el merge | **CUMPLE** (todas las rutas existen) |
| T3.6 | Reproducir bootstrap con `snap.db` dentro de ±0,005 de `bootstrap_prauc_tabla.md` | **CUMPLE** (|Δmedia| ≤ 0,003) |

Etiquetas (sin cambios, no se movió ni creó ninguna):

```
tesis-cap4-freeze          -> deref 9b778a3
tesis-cap4-freeze-v2       -> deref 2b92201
tesis-cap4-freeze-v3       -> deref 9b8302e
tesis-reproducibilidad-v1  -> deref 6f7962d
tesis-cierre-evidencia-v1  -> deref 770d89c
```

T3.6 — bootstrap reproducido (B=3000, semilla=42, remuestreo por evento):

| régimen | comparación | reproducido | tesis | \|Δmedia\| |
|---|---|---|---|---|
| limpio (13 ev) | LSTM−oráculo | −0,113 | −0,113 | 0,000 |
| limpio (13 ev) | LSTM−umbral | +0,041 | +0,040 | 0,001 |
| limpio (13 ev) | RF−LSTM | +0,191 | +0,194 | 0,003 |
| aleatorizado (74 ev) | LSTM−oráculo | +0,220 | +0,220 | 0,000 |
| aleatorizado (74 ev) | LSTM−umbral | +0,288 | +0,287 | 0,001 |
| aleatorizado (74 ev) | RF−LSTM | +0,053 | +0,053 | 0,000 |

(Los archivos de salida regenerados al reproducir se restauraron a su versión versionada; la rama
queda byte a byte idéntica a los commits citados.)

## Puntos NO VERIFICADO

- Ninguno. Todas las verificaciones de la Tarea 3 se pudieron ejecutar (la base `snap.db` estaba
  disponible localmente, por lo que T3.6 se verificó en vez de quedar pendiente).

## Pendiente (acción del autor)

- `push` de la rama `integrar-reproducibilidad` y apertura/fusión del PR hacia `main` (ver comandos
  en el resumen de la sesión). No se creó ninguna etiqueta nueva.
