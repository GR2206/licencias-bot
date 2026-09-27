# Auditoría de decisiones

Corréla a mano, cada tanto. No la pongas dentro de la rutina automática.

```bash
python -m polymarket audit
```

Leé `data/polymarket/registro.csv` y auditá tus últimas 30 decisiones. Solo mercados ya resueltos.

1. **CALIBRACIÓN.** Agrupá 50-60%, 60-70%, 70-80%, 80%+. En cada rango, ¿qué proporción se cumplió? Si en el grupo del 70% acertaste el 45%, estás sobreconfiado y quiero el número, no una explicación.
2. **PÉRDIDAS.** Las 5 peores. ¿El error fue la estimación, la lectura de la resolución, la fuente, o el tamaño? Una sola categoría por pérdida.
3. **CATEGORÍAS.** ¿En qué tipo te va sistemáticamente peor? Con menos de 10 resueltas por categoría: "muestra insuficiente".
4. **SESGOS.** ¿Siempre estimás más alto que el mercado? ¿Te entusiasmás con noticias recientes? ¿Una misma fuente explica las peores?

No propongas cambios todavía. Solo números.
