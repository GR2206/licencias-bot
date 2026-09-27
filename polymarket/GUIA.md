# Agente Polymarket en simulación

Réplica de la guía NodeStudio: un sistema que mira mercados públicos, se arma una probabilidad **después** de juntar evidencia, compara contra el precio, y solo anota una oportunidad si el desvío neto supera 8 puntos. **No envía órdenes. No pide wallet. No toca plata real.**

El número de $50 → $5.273 en 48 horas es una afirmación sin verificar. Esto no intenta repetirlo. Replica la arquitectura: criterio explícito, límites que el agente no puede cambiar, y un registro de cada decisión.

## Qué hace, en una frase

Descartar casi todo. El acierto no sale de predecir más; sale de operar menos, con evidencia, y medirse cuando el mercado ya cerró.

```
información pública
        ↓
analiza mercados (Gamma + CLOB, sin login)
        ↓
filtros duros (spread, libro, precio, fecha)
        ↓
investiga solo a los que sobrevivieron
        ↓
estima probabilidad (después de la evidencia)
        ↓
edge neto = (p − precio) − comisión − spread
        ↓
si edge neto < 8 pp → se tira
        ↓
tamaño = min(1/4 Kelly, 6% del bankroll de hoy)
        ↓
escribe el registro (simulado)
        ↓
cuando el mercado resuelve, completa pnl
        ↓
audita calibración
```

## Cómo se usa

Desde la raíz del repo:

```bash
# 1. El ejemplo numérico de la guía (sin red)
python -m polymarket example

# 2. Una corrida en vivo: lee mercados abiertos y aplica filtros duros
python -m polymarket scan --limit 150 --skip-books
# por defecto ordena por volume24hr: primero los que se están operando

# 3. Después de investigar a mano (o con la skill), pegá las estimaciones
python -m polymarket propose --file data/polymarket/ejemplo_estimacion.json

# 4. Cuando un mercado ya cerró, se completa resultado y pnl
python -m polymarket resolve

# 5. Calibración: de todo lo que dijo "70%", ¿pasó cerca del 70%?
python -m polymarket audit
```

`--skip-books` evita pedir el libro de cada sobreviviente (más rápido para una primera pasada). Sin ese flag, la corrida confirma profundidad real en CLOB.

El registro vive en `data/polymarket/registro.csv`. Cada fila se escribe **cuando se decide**. No se reescribe. Solo se completan `resultado`, `pnl` y `bankroll_post` al resolver.

## Cómo lograr vitalidad de acierto

No es un prompt mágico ni un umbral secreto. Es una disciplina de cinco capas:

1. **Descartar primero.** Spread > 3¢, libro flaco, precio < 5¢ o > 95¢, resolución a más de 90 días: se tiran sin investigar. De 1.000 mercados, el 95% muere acá. Investigar basura fabrica sobreconfianza.

2. **Evidencia → después el número.** Dos fuentes independientes con fecha. Dos razones a favor y dos en contra. Una hipótesis de *por qué* el mercado está mal. Si no podés armar el contra, no entendiste el mercado. Redondeá a múltiplos de 5%. Un 53,7% es precisión falsa.

3. **Edge neto, no el número lindo.** La comisión de taker es `shares × feeRate × p × (1 − p)` y es máxima cerca del 50%. Un edge de 11 pp en crypto puede ser ~9 pp reales, y eso antes del spread. El umbral de 8 pp se mide **después** de fee y spread. No se negocia.

4. **Kelly fraccional + techo.** Kelly completo asume que tu `p` es la probabilidad verdadera. Un modelo leyendo noticias está sobreconfiado. Se usa 1/4 Kelly y un techo del 6% del bankroll **de hoy**. Después de una pérdida no se aumenta el tamaño. Dos apuestas al mismo evento son una apuesta del doble.

5. **Medirse en resueltos, no en abiertos.** Hasta que el mercado cierra, no sabés nada. La calibración por rango (50-60, 60-70, 70-80, 80%+) es la vitalidad: si el grupo del 70% acertó el 45%, estás sobreconfiado y el número manda, no la explicación. Con menos de 20 resueltas la respuesta correcta es *no cambiar nada*. El único cambio posible, si alguna vez hay muestra, es hacia el lado conservador.

Si el bankroll simulado cae 25% desde su máximo, se paran las entradas nuevas.

## Qué no hace este código (a propósito)

- No loguea en Polymarket.
- No firma órdenes.
- No guarda claves privadas.
- No estima probabilidad solo. `scan` deja **candidatos**; `propose` exige el trabajo de investigación.
- No se audita ni se “mejora” solo adentro de la rutina. Eso deriva a operar más.

## Telegram (opcional)

1. Creá un bot con `@BotFather`.
2. Escribile y sacá el `chat_id` de `https://api.telegram.org/bot<TOKEN>/getUpdates`.
3. Copiá `data/polymarket/telegram.env.example` a `telegram.env` (ese archivo no se commitea).
4. `python -m polymarket scan --notify`

El token queda en el disco. No lo pongas en el prompt.

## Skills para el agente

- `skills/polymarket/analisis-de-mercados.md` — prompt principal (páginas 9-10).
- `skills/polymarket/auditoria.md` — auditoría a mano.
- `skills/polymarket/revision-estrategia.md` — un solo cambio, y solo más conservador.

## Legal y geo

Polymarket bloquea varios países. Usar VPN para saltear el bloqueo va contra sus términos. Esta pieza es educativa, sobre cómo se construye un agente, no asesoramiento financiero.
