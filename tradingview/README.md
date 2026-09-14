# Indicadores para TradingView

Tres scripts que se usan juntos:

| Script | Para qué |
| --- | --- |
| [`ai_trend_signals.pine`](./ai_trend_signals.pine) | el padre: carteles BUY/SELL y bandas de tendencia |
| [`order_blocks.pine`](./order_blocks.pine) | Order Blocks en rectángulos + máximos/mínimos diarios punteados |
| [`confluence_engine.pine`](./confluence_engine.pine) | une los dos y marca la entrada con SL/TP 1:3 y alerta JSON |

El tercero es el que le da las órdenes al [bot](../bot/README.md). Los dos
primeros son para mirar.

Lo que sigue documenta el primero; los otros dos tienen su explicación en la
cabecera de cada archivo.

---

# AI Trend Signals — indicador para TradingView

Indicador en Pine Script v5 que reproduce el tipo de visual que venden como
"señales de IA": **carteles BUY / SELL** sobre el gráfico, **bandas de color**
verde/rojo que siguen la tendencia, niveles **Entry / SL / TP** y alertas.

Archivo: [`ai_trend_signals.pine`](./ai_trend_signals.pine)

## Qué hace por dentro

No hay ninguna red neuronal detrás (tampoco la hay en los indicadores que se
venden como "AI"; TradingView no permite ejecutar modelos dentro de Pine, solo
matemática sobre las velas). El motor es una confluencia clásica:

| Bloque | Función |
| --- | --- |
| SuperTrend (ATR) | define el sesgo alcista/bajista y dibuja la banda de color |
| EMA 200 | filtro de tendencia: solo BUY sobre la EMA, solo SELL debajo |
| RSI 14 | filtro de momentum contra el nivel neutro (50 por defecto) |
| Volumen (opcional) | exige volumen por encima de su media |
| Multi-timeframe (opcional) | exige que el SuperTrend del TF superior apunte igual |
| ATR × 1.4 | distancia del stop; los TP son múltiplos de ese riesgo (1R / 2R / 3R) |

Los valores por defecto son los mismos que usa el bot de este repo (EMA 50/200,
RSI 14, ATR 14), así el gráfico y el bot cuentan la misma historia. La excepción
es el multiplicador del stop: el bot lo usa como **piso** combinado con el borde
del Order Block (`sl_atr = 1.5`), que es otro papel, así que el barrido de acá
abajo no se le aplica directamente y lo dejé como estaba.

**El stop pasó de 2.2 a 1.4 × ATR**, y no por gusto. Barrido sobre 8 activos y 2
años de H1 con comisión incluida: el 1.4 deja el stop en 1.34% del precio en vez
de 2.14% (un 37% más corto) y midió mejor que el 2.2 en **las dos mitades del
período y en 6 de los 8 activos**. Es lo único de todo el repo que pasó una
prueba de persistencia. Por debajo de 1.4 se cae rápido, porque el ruido de la
vela toca el stop antes de que el movimiento arranque: con 1.0 da −0.125 y con
0.6 da −0.208, contra −0.077 del 1.4.

Dos advertencias: **en M30 es al revés** (ahí apretar empeora, dejalo en 2.2 o
subilo a 2.6), y toda la grilla sigue siendo negativa, así que el 1.4 es el menos
malo y no un ganador. Los detalles están en la cabecera del `.pine` y en
[`bot/README.md`](../bot/README.md).

Se emite **una sola señal por tramo de tendencia**: se "arma" en el giro del
SuperTrend y se dispara en la primera vela en que todos los filtros dan luz
verde.

## Instalación

1. Abrí TradingView y en el panel inferior entrá a **Pine Editor**.
2. **Open → New indicator** y borrá el contenido de ejemplo.
3. Pegá todo el contenido de `ai_trend_signals.pine`.
4. **Save** (ponele un nombre) y después **Add to chart**.
5. Ajustá los parámetros con el engranaje del indicador.

Funciona con cuenta gratuita de TradingView.

## Alertas

- **BUY**, **SELL** o **BUY o SELL**: elegilas desde el diálogo de alerta en
  *Condition → AI Trend Signals*.
- **Any alert() function call**: manda un mensaje ya armado con Entry, SL y los
  tres TP. Es la opción ideal para reenviar a Discord, Telegram o a un webhook
  del bot.

Con "Confirmar señal al cierre de vela" activado (por defecto) las alertas se
disparan al cierre: menos señales, pero sin repintado.

## Ajustes recomendados como punto de partida

| Estilo | Timeframe | Sensibilidad (factor ATR) | Filtros |
| --- | --- | --- | --- |
| Scalping | 1m – 5m | 1.8 – 2.2 | EMA + RSI, MTF en 15m |
| Intradía | 15m – 1h | 2.6 – 3.2 | EMA + RSI, MTF en 4h |
| Swing | 4h – 1D | 3.5 – 5.0 | solo EMA |

Subir el factor ATR = menos señales y más tendencia. Bajarlo = más señales y
más ruido.

## Advertencia

Ningún indicador de este tipo "adivina" el mercado. Los videos promocionales que
muestran una racha perfecta de aciertos están mostrando el resultado ya conocido
sobre velas cerradas, que es exactamente lo que cualquier indicador de tendencia
hace parecer fácil en pasado. Probalo en replay y en demo antes de arriesgar
dinero, y usá siempre el stop.
