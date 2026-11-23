# H04 — Resta puntual vs media bootstrap de las diferencias de PR-AUC

`python -m scripts.bootstrap_prauc_puntual_vs_media --db snap.db` · B=3000 · semilla 42 · remuestreo por evento.

| régimen | comparación | PR-AUC a | PR-AUC b | resta puntual | media boot | mediana boot | IC95% | sesgo | descartadas | prev boot / prueba |
|---|---|---|---|---|---|---|---|---|---|---|
| limpio | RF - LSTM | 0.4044 | 0.1973 | +0.2071 | +0.193 | +0.1961 | [+0.0878, +0.2835] | -0.0141 | 0 | 0.1671/0.1659 |
| limpio | LSTM - oraculo | 0.1973 | 0.2534 | -0.0561 | -0.1131 | -0.0749 | [-0.3737, +0.071] | -0.057 | 0 | 0.1671/0.1659 |
| limpio | LSTM - umbral | 0.1973 | 0.1719 | +0.0254 | +0.0414 | +0.0295 | [-0.0273, +0.1657] | +0.016 | 0 | 0.1671/0.1659 |
| aleatorizado | RF - LSTM | 0.4839 | 0.4309 | +0.053 | +0.0527 | +0.0531 | [-0.0021, +0.105] | -0.0003 | 0 | 0.1454/0.1445 |
| aleatorizado | LSTM - oraculo | 0.4309 | 0.2032 | +0.2277 | +0.2197 | +0.2211 | [+0.1305, +0.3016] | -0.008 | 0 | 0.1454/0.1445 |
| aleatorizado | LSTM - umbral | 0.4309 | 0.1455 | +0.2854 | +0.2869 | +0.2873 | [+0.222, +0.3557] | +0.0016 | 0 | 0.1454/0.1445 |

## Diagnóstico del signo del oráculo (régimen limpio, LSTM−oráculo)
- Bootstrap con signo **FIJO** (elegido en la prueba completa): media = **-0.1131**
- Bootstrap con signo **REELEGIDO por réplica**: media = **-0.1131**

**Conclusión.** Ambos dan ≈ −0,113: la reselección del signo **no** explica la diferencia con la
resta puntual (−0,056). La causa es el **sesgo del bootstrap de PR-AUC bajo remuestreo por evento**:
con solo 13 eventos la distribución de las 3.000 diferencias es muy **asimétrica a la izquierda**
(réplicas con composición desfavorable producen PR-AUC muy bajas), lo que arrastra la **media** por
debajo de la resta puntual. La **mediana** (≈ −0,075) queda entre ambas y es un estimador central
más robusto. El **estado de la hipótesis no cambia** (INCONCLUSO: el IC incluye 0 en las tres
formas). En el régimen aleatorizado (74 eventos) el sesgo es mucho menor (ver tabla).
