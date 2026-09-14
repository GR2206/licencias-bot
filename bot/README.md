# Bot de confluencia — Tendencia + Order Block

Bot que replica en Python la lógica de
[`tradingview/confluence_engine.pine`](../tradingview/confluence_engine.pine) y
ejecuta en **Binance Futures USDT-M**. Pensado para correr en un celular con
Termux, en H1 y M30.

**No usa webhooks ni depende de TradingView.** Baja las velas de Binance y
calcula todo él mismo. Eso es a propósito: un webhook de TradingView necesita
una IP pública, y un celular no la tiene. Si el gráfico y el bot calculan lo
mismo, no hace falta el puente.

> **Si venís por el scalping de 5 minutos**, andá directo a
> [Scalping de 5 minutos: por qué Binance no puede y dónde sí se puede](#scalping-de-5-minutos-por-qué-binance-no-puede-y-dónde-sí-se-puede).
> Ahí está la cuenta que decide el asunto, y el [panel](#el-panel), que es lo
> más útil de todo el proyecto: mide cualquier idea contra entrar al azar.

## Lo primero: los números reales

Antes de la instalación, lo que corresponde saber. Probé la estrategia sobre
**historia real de OKX/Binance: BTC, ETH y SOL en 1h y 30m, unas 3000 velas de
cada uno (mayo a septiembre 2026), 124 operaciones**:

| Configuración | Operaciones | Aciertos | Resultado con comisiones |
| --- | --- | --- | --- |
| RR 1:3, sin parcial | 126 | 26.2% | −7.1R |
| RR 1:3 + stop ATR + parcial en 1R | 124 | 52.4% | **+1.4R** |
| RR 1:2 + stop ATR | 124 | 37.1% | +1.4R |
| RR 1:3 con filtro EMA 200 | 95 | 20.2% | −18R |

Traducido: **la mejor configuración queda en el borde del break-even**. +1.4R
en 124 operaciones es ruido estadístico, no una ventaja. Con RR 1:3 hace falta
acertar más del 25% para no perder, y el sistema anda justo ahí.

Después estiré la muestra a **2.3 años sobre CHZ** (231 operaciones) y ahí la
respuesta dejó de ser ambigua: da negativo, y de 432 combinaciones de parámetros
sólo el 22% queda positivo en las dos temporalidades, cuando el azar puro daría
~25%. Los números están en la sección de CHZ, más abajo.

Y después lo llevé a **25 activos y un año de historia** de Binance, eligiendo
las configuraciones en 8 activos y verificándolas en los otros 17: **10 de 384
combinaciones (3%) dan positivo** en los activos que no se usaron para elegir.
**No hay ventaja demostrable**, y no es cuestión de encontrar los parámetros
justos.

La segunda estrategia, la de la **línea gris** (martillo en la EMA 200 y ruptura),
está medida con el mismo método en su propia sección más abajo. Ahí sí aparece un
número positivo fuera de muestra, pero depende de que el mercado siga cayendo:
está explicado con las tres pruebas que lo muestran.

Por eso el bot arranca en `MODO=simulacion` y por eso insisto abajo con el
testnet. No es burocracia: es que la evidencia dice que esto no gana plata, y las
comisiones (0.10% ida y vuelta) se llevan otra parte de cada operación.

Lo que sí funciona bien es la **parte visual**: marcar Order Blocks con reglas
mecánicas y ver la confluencia en el gráfico. Como herramienta para decidir a
mano, sirve. Como piloto automático, todavía no.

## CHZUSDT: lo que dan los datos

Hay un archivo listo para este caso: [`config.chzusdt.env`](config.chzusdt.env).

```bash
cp config.chzusdt.env config.env
nano config.env        # completá las dos claves y listo
```

Viene con `ENTORNO=real` y `MODO=simulacion`: lee el mercado y tu saldo reales,
pero no manda ninguna orden. La única línea que lo hace operar de verdad es
`MODO=real`. Antes de tocarla, leé lo que sigue.

Probado sobre CHZ real de OKX (mismo precio que Binance), **2.3 años en H1 y 1.1
en M30**, con comisiones del 0.10% ida y vuelta:

| | Período | Operaciones | Aciertos | Resultado | Peor caída |
| --- | --- | --- | --- | --- | --- |
| **CHZ H1** | jun 2024 – sep 2026 | 117 | 50.4% | **−3.0R** | −22.8R |
| **CHZ M30** | jul 2025 – sep 2026 | 114 | 49.1% | **−6.6R** | −13.3R |

Con 231 operaciones ya no es ruido: **sobre CHZ esta lógica no tiene ventaja.**
Queda apenas por debajo de cero, y la peor racha en H1 fue de −22.8R (con
`RIESGO_PCT=0.5` eso es −11% de la cuenta; con 1%, −23%).

Y no es cuestión de encontrar los parámetros justos. Probé 432 combinaciones de
pivote, FVG, confirmación, EMA, RR, sensibilidad y parcial, midiendo cada una en
H1 y en M30:

- **93 de 432 (22%) dan positivo en las dos temporalidades.** Si no hubiera
  ninguna ventaja y el ruido fuera simétrico, saldría ~25%. O sea: la familia
  entera rinde como una moneda, o un poco peor.
- Los parámetros que más "mejoran" se contradicen entre temporalidades. Quitar
  el filtro FVG da +0.101R por operación en H1 y −0.012R en M30. La EMA 200 da
  +0.059R en M30 y −0.015R en H1. Elegir el que quedó lindo en un lado es
  ajustar a los datos, no encontrar una ventaja.
- El **único** parámetro con dirección consistente es el objetivo: RR 1:2 da
  −0.032R por operación, 1:3 da −0.014R y 1:4 da +0.030R, positivo en las dos
  temporalidades. Coincide con lo que se ve midiendo el recorrido: en H1, 31% de
  las señales llegan a 3R y 25% pasan de 4R (una llegó a 13.8R). Las pocas que
  corren mucho son las que pagan todo. Si vas a tocar algo, probá `RR=4`.

Lo que sí conviene saber para usarlo:

**Da muy pocas señales.** Una operación cada 7 días. Si lo dejás corriendo y no
pasa nada por una semana, no está roto: es así. Con los valores por defecto daba
la mitad todavía, porque CHZ es volátil y sus stops quedan a 2–4% del precio,
así que el tope de 3% descartaba casi todo. En este archivo `RIESGO_MAX_PCT=5`.
Eso **no** aumenta el riesgo de la cuenta (ese lo fija `RIESGO_PCT`): un stop
más ancho da una posición más chica, nada más.

**La parcial baja la varianza, no sube la ganancia.** Cerrar la mitad en 1R sube
el porcentaje de aciertos al 50% y hace la curva más suave, pero le corta la
mitad a las operaciones que se van 4R o más, que son justamente las que pagan
todo. En el promedio de las 432 combinaciones el efecto se cancela.
`PARCIAL_1R` es un interruptor en el env.

**Por qué la muestra larga importa.** Con las primeras 3000 velas (4 meses) H1
daba +2.9R en 16 operaciones y parecía que funcionaba. Estirando a 2.3 años, esas
mismas reglas dan −3.0R en 117. Los 16 primeros eran suerte. Si te alcanza para
16 operaciones, no te alcanza para decidir nada.

## Recorrido por 25 activos, stops cortos y 3-5 operaciones por día

Tres pedidos concretos: acortar los stops, recorrer los activos más operados de
Binance y llegar a 3-5 operaciones por día. Los tres se pueden cumplir, y hay un
preset que lo hace: [`config.multi.env`](config.multi.env). Lo que sigue es qué
dieron las mediciones, porque cambia lo que conviene hacer con eso.

Los datos: **un año de historia real de Binance de los 25 perpetuos USDT con más
volumen sostenido** (mediana de 90 días, no el pico de un día), en M15 y M30.
Unas 2.900 operaciones por configuración. Comisiones incluidas: 0.05% taker por
lado, 0.02% maker en el objetivo.

### Los stops largos: se arreglan filtrando, no moviendo el stop

Probé todas las formas de acortarlo. Las que consisten en **mover el stop más
cerca empeoran el resultado**, y hay una razón matemática, no de azar: la
comisión es un porcentaje fijo del nocional, así que en unidades de R crece
cuando el stop se acorta.

| Cómo se acorta | Stop promedio | Comisión en R | R por operación |
| --- | --- | --- | --- |
| Actual (borde + 1.5 ATR) | 1.24% | 0.08R | +0.027 |
| Sin piso ATR, colchón 0.05 | 1.15% | 0.09R | −0.025 |
| Bloque por cuerpo, borde, colchón 0.1 | 0.97% | 0.10R | −0.130 |
| Entrada por límite al 50% de la zona | 0.40% | 0.25R | −0.080 |
| Entrada por límite al 80% de la zona | 0.28% | 0.36R | −0.261 |

La entrada por orden límite dentro del bloque es la idea que más promete y la que
peor sale: consigue un stop del 0.28%, y ahí la comisión sola se lleva 0.36R por
operación. Por debajo del 1% de stop, la operación arranca debiendo demasiado.

Lo que **sí** funciona es descartar los setups cuyo stop nace largo, con
`RIESGO_MAX_PCT`. Con el tope en 1.8% el stop promedio baja a **1.13%** y el
resultado por operación no se mueve. Es la diferencia entre elegir mejor y
apretar más.

### Las 3-5 operaciones por día: alcanzables

Con la configuración de stop corto, medido sobre el año:

| Canasta | Operaciones por día |
| --- | --- |
| 1 activo en H1 | 0.1 |
| 1 activo en M30 | 0.20 |
| 1 activo en M15 | 0.47 |
| 8 activos en M15 (el preset) | **3.96** |
| 12 activos en M15 + M30 | 8.0 |

Así que sí: M15 con 8 activos da el ritmo que buscás. Para eso el bot ahora
revisa una sola vez por vela cerrada y lee todas las posiciones en un pedido
único; un barrido de 8 símbolos pesa 16 contra el límite de 2400 por minuto de
Binance.

### Qué activo recomiendo: ninguno, y esto es lo que lo demuestra

Partí el año en dos mitades y comparé el rendimiento de cada activo:

| Activo | R/op 1ª mitad | R/op 2ª mitad |
| --- | --- | --- |
| PAXGUSDT | +0.638 | +0.123 |
| AVAXUSDT | +0.236 | −0.043 |
| SOLUSDT | +0.207 | +0.031 |
| BTCUSDT | +0.047 | −0.120 |
| … | | |
| LINKUSDT | −0.246 | +0.074 |
| AAVEUSDT | −0.283 | −0.130 |

**Los 12 mejores de la primera mitad rindieron −0.008R en la segunda; los 13
peores, −0.111R.** Hay un rastro de señal, pero los "mejores" siguen dando cero.
Elegir el activo por su historial no identifica al que va a andar bien. Por eso
el preset elige por **liquidez**, que sí predice algo real: menos spread y menos
slippage.

De 25 activos, los únicos positivos en las dos mitades fueron PAXG (oro) y XLM.
Con 25 pruebas, que 2 pasen es exactamente lo que da el azar.

### Los patrones y las rachas que buscabas

Todo medido eligiendo en la primera mitad del año y comprobando en la segunda:

- **Horarios.** Las 15 horas del día que rindieron positivo en la primera mitad
  dieron −0.068R en la segunda, peor que las horas descartadas (−0.050R). No hay
  franja horaria que se sostenga.
- **Días de la semana.** Jueves y viernes dieron positivo en las dos mitades
  (+0.10/+0.12 y +0.50/+0.08), pero con 7 días probados que uno o dos parezcan
  consistentes es lo esperable por azar, y el viernes se desinfló de +0.50 a
  +0.08.
- **Rachas.** La operación siguiente a una ganadora rindió −0.064R con 20.7% de
  acierto; la siguiente a una perdedora, −0.004R con 22.0%. Ganar no hace más
  probable volver a ganar: si algo, un poco menos. **No hay rachas que seguir**,
  cada operación es independiente de la anterior.
- **Salidas dinámicas.** Probé 8 modelos de salida, incluido trailing de 1.5 a 3
  ATR y trailing que arranca en 1R o 2R. El trailing sí captura los movimientos
  largos (hubo una operación de +39.7R) y sube el acierto del 21% al 47%, pero
  **los 8 dan negativo** en los activos de validación, entre −0.03R y −0.09R.

### El número que resume todo

Probé **384 combinaciones** de tope de riesgo, confirmación, FVG, pivote,
sensibilidad, RR y parcial. Las elegí en 8 activos y las verifiqué en los otros
17, que nunca se usaron para elegir:

**10 de 384 (3%) dan positivo en los activos de validación.**

Si la estrategia no tuviera ventaja pero tampoco desventaja, saldría cerca del
50%. Un 3% significa que la familia entera es negativa, y que los parámetros
lindos son ruido. La mejor combinación llega a +0.007R por operación, que es
cero, con una caída máxima de −68R.

Para la cartera completa de 25 activos: peor racha perdedora **41 operaciones
seguidas**, peor caída acumulada **−281R** (con 0.25% de riesgo por operación,
−70% de la cuenta).

### Conclusión

La máquina es más rápida que el ojo, es cierto, pero la velocidad multiplica lo
que ya tenés. Con una esperanza de −0.046R por operación, pasar de 1 a 4
operaciones por día no mejora la efectividad: acelera la pérdida. A 4 por día son
~1.460 al año, unas −68R.

El preset de 8 activos en M15 está armado y funciona: da el ritmo que pediste, con
stops de 1.13% y todos los límites de Binance respetados. Está en
`MODO=simulacion` y mi recomendación es que lo dejes ahí, mires el log unas
semanas y compares con lo que ves en el gráfico. Los indicadores para operar a
mano son la parte de todo esto que sí quedó buena.

Podés repetir el recorrido cuando quieras, con tus propios parámetros:

```bash
python explorar.py --tf 15m --dias 180
python explorar.py --simbolos BTCUSDT,ETHUSDT,SOLUSDT --tf 30m --dias 365
```

## La línea gris: la regla del martillo y la ruptura

La línea gris del gráfico es la **EMA 200** (la naranja es la EMA 50). La idea de
usarla como nivel es buena y se puede escribir como dos reglas exactas:

- **LONG**: el precio viene por arriba, toca o perfora la línea y deja una **vela
  martillo** que vuelve a cerrar arriba. Rechazó el nivel.
- **SHORT**: el cierre **pasa de largo** al otro lado de la línea. Dejó de
  sostener.

Está implementada en dos lugares que calculan lo mismo:
[`tradingview/linea_gris.pine`](../tradingview/linea_gris.pine) para ver los
carteles en el gráfico, y `linea_gris.py` para que la corra el bot con
`ESTRATEGIA=linea_gris`.

### El hallazgo que sí tiene sustento: el piso del stop

El martillo pone el stop debajo de su mecha, así que sale corto solo. Eso parecía
la solución al problema de los stops largos, pero medido resultó lo contrario:
**los stops muy cortos pierden por aritmética**. La comisión es un porcentaje del
nocional, no de tu riesgo. Sobre un stop del 0.2% la ida y vuelta se lleva 0.35R;
sobre uno del 1.5%, apenas 0.05R.

Descartar las señales con el stop demasiado pegado (`RIESGO_MIN_PCT`) es el
ajuste que más movió el resultado en todo el proyecto:

| Stop mínimo | R/op en los 8 activos de elección | R/op en los 17 de control |
| --- | --- | --- |
| 0.15% | −0.035 | −0.072 |
| 0.40% | +0.039 | −0.022 |
| 0.70% | +0.119 | +0.029 |
| **1.00%** | **+0.168** | **+0.074** |
| 1.50% | +0.172 | +0.092 |

Es monótono en las dos columnas a la vez y tiene una explicación mecánica, que es
lo que distingue un hallazgo de una casualidad. Vale también para la estrategia
de Order Blocks, aunque ahí sólo la lleva de −0.10R a cero.

### Qué dio cada mitad de tu idea

Barrí **1296 combinaciones** (EMA, RR, exigencia del martillo, tolerancia,
pendiente, color) sobre 25 activos en M15+M30+H1 con un año de historia real,
eligiendo en 8 activos y verificando en los otros 17:

| Regla | Operaciones | R/op en los activos de control |
| --- | --- | --- |
| SHORT cuando pasa de largo | 3586 | **+0.084** |
| LONG con martillo en la línea | 194 | −0.114 |

O sea: **la parte del short funciona y la del martillo no.** Con el piso de stop
en 1%, la canasta de 8 activos en M15+H1 da 3.0 operaciones por día, +0.29R por
operación y 27% de acierto, con una peor racha de 21 pérdidas seguidas. Ese
+0.29R está inflado porque esos 8 activos se eligieron mirando este mismo año; el
número limpio es el +0.084R de los 17 de control.

### Por qué igual arranca en simulación

Tres mediciones que hay que mirar antes de creerle al short:

1. **Fue un año bajista.** 23 de los 25 activos cayeron, mediana −56%. Apliqué la
   *misma* regla de ruptura hacia arriba: **−0.182R**. Y en ZEC, el único que
   subió fuerte (+1951%), se da vuelta: el largo gana +0.139R y el corto casi no
   rinde. Probé también filtrar por régimen (operar sólo a favor de una EMA mucho
   más lenta) y los largos siguieron en −0.22R, así que no es "ir a favor de la
   tendencia": es que en este año pagaron las bajadas.
2. **Toda la ventaja cabe dentro de los costos.** +0.074R como está medido,
   +0.047R con 0.02% de slippage por lado, +0.006R con 0.05%, y **negativo** si
   pagás taker en todo. Depende de que el objetivo entre como maker y de que el
   deslizamiento sea mínimo.
3. **Se apaga.** Dentro de los propios activos de control: +0.149R en la primera
   mitad del año y −0.008R en la segunda.

Un resultado que sólo existe en una dirección, sólo con costos favorables y sólo
en la primera mitad de la muestra es un resultado del mercado, no de la regla.

```bash
# Ver la canasta con tus parámetros
python explorar.py --estrategia linea_gris --tf 15m --dias 365

# Correrla en simulación
cp config.linea_gris.env config.env
python bot.py
```

### La misma regla en oro y forex

Está también en MQL5 para MetaTrader 5, en
[`metatrader/`](../metatrader/README.md). Medida sobre 2.4 años de oro y 2.8 de
los majors de forex confirma el diagnóstico de arriba: en ese período **todo
subió**, y ahí gana el lado largo (+0.213R) mientras el corto pierde (−0.156R),
justo al revés que en cripto. La versión simétrica, que toma los dos lados y es
la única operable sin adivinar, da +0.011R en oro/forex y −0.088R en cripto: cero
en los dos.

Lo que sí cambia a favor en MetaTrader es el **costo**: la ida y vuelta en oro es
~0.005% del precio contra ~0.10% en Binance futuros. Eso baja el piso del stop de
1% a 0.30%, así que los stops cortos que buscabas recién son viables ahí.

## Scalping de 5 minutos: por qué Binance no puede y dónde sí se puede

Pediste un bot que entre y salga todo el tiempo, que no tenga la operación
abierta más de 5 minutos, que un acierto recupere 2 o 3 pérdidas, y que el
tamaño siga al capital para que sea compuesto. Cada pieza es razonable por
separado. El problema es que dos de ellas —"5 minutos" y "en Binance"— se
contradicen entre sí, y se puede demostrar con una sola cuenta.

### La cuenta que lo decide

Una operación de 5 minutos captura, como máximo, lo que el activo se mueve en 5
minutos. Eso no es opinión, se mide. Mediana del recorrido de 5 velas de M1
sobre 20 días reales:

| activo | se mueve en 5 min | comisión Binance | stop razonable | la comisión vale | acierto para empatar (1:2) |
|---|---|---|---|---|---|
| BTCUSDT | 0.099% | 0.10% | 0.049% | **2.02 R** | **100.7%** |
| ETHUSDT | 0.136% | 0.10% | 0.068% | 1.48 R | 82.5% |
| SOLUSDT | 0.196% | 0.10% | 0.098% | 1.02 R | 67.4% |
| DOGEUSDT | 0.209% | 0.10% | 0.104% | 0.96 R | 65.2% |
| XRPUSDT | 0.213% | 0.10% | 0.106% | 0.94 R | 64.6% |
| ADAUSDT | 0.273% | 0.10% | 0.137% | 0.73 R | 57.7% |

En Bitcoin la comisión de ida y vuelta es **igual a todo lo que el precio se
mueve en 5 minutos**. Con un stop de la mitad de ese recorrido pagás dos veces
tu riesgo por el derecho a jugar, y necesitarías acertar más del 100% de las
veces: no es difícil, es imposible.

### El techo real no es 85%, es 33%

Acá está la parte que cambia la forma de mirar todo esto. Entrando **al azar**,
sin ningún indicador, con objetivo 1:2, el acierto que da es exactamente
1/(1+RR) = 33.3%. Es la fórmula de la ruina del jugador: la probabilidad de
tocar +2X antes de −X en un paseo aleatorio es un tercio.

Medido sobre 71 días de oro en M5 entrando en momentos al azar:

| stop | RR | acierto medido | acierto teórico | R/op bruto |
|---|---|---|---|---|
| 0.05% | 1:2 | 33.5% | 33.3% | +0.004 |
| 0.05% | 1:3 | 24.9% | 25.0% | −0.006 |
| 0.20% | 1:2 | 33.3% | 33.3% | −0.001 |
| 0.20% | 1:3 | 25.0% | 25.0% | −0.001 |

Coincide con la teoría en dos decimales, y la esperanza bruta es **cero** en
cualquier combinación de stop y objetivo. A 5 minutos de plazo, el mercado es
indistinguible de una moneda para este fin.

Eso deja el criterio de evaluación reducido a una sola línea:

```
resultado = (cuánto le gana tu estrategia al azar) − (lo que cuesta operar)
```

Y reencuadra el 85% del que hablabas: **no hace falta 85%, hace falta ganarle 4
o 5 puntos a 33%.** Suena mucho más fácil. Es lo que no logró ninguna de las
reglas que probamos.

### Qué pasa cuando se respeta el costo

El scalper tiene un piso de stop derivado de la comisión: con 0.10% de ida y
vuelta y aceptando regalar como máximo 0.15 R, el stop no puede bajar de 0.667%
del precio. Eso resuelve el problema del costo y crea otro, porque un stop de
0.667% con objetivo de 1.33% no se resuelve en 5 minutos. Medido en 60-90 días
de M5:

| activo | ops | op/día | stop | acierto | azar | vs azar | margen de error | R/op | llegaron al objetivo |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 8 | 0.09 | 0.788% | 25.0% | 28.0% | −3.0 pp | ±16.6 | −0.515 | 0 de 8 |
| ETHUSDT | 11 | 0.12 | 0.769% | 45.5% | 32.6% | +12.9 pp | ±14.2 | −0.109 | 0 de 11 |
| SOLUSDT | 19 | 0.21 | 0.793% | 42.1% | 35.2% | +6.9 pp | ±10.8 | −0.047 | 1 de 19 |
| DOGEUSDT | 20 | 0.22 | 0.823% | 45.0% | 35.8% | +9.2 pp | ±10.5 | −0.190 | 1 de 20 |
| XRPUSDT | 14 | 0.16 | 0.911% | 28.6% | 35.2% | −6.6 pp | ±12.6 | −0.189 | 1 de 14 |
| ADAUSDT | 39 | 0.43 | 0.824% | 30.8% | 40.3% | −9.5 pp | ±7.5 | −0.321 | 1 de 39 |

Es la salida del botón **medir** del panel, así que lo podés reproducir vos
mismo. Puse la columna del margen de error a propósito, porque sin ella la tabla
miente: el +12.9 de ETH parece un hallazgo y está dentro de ±14.2, o sea que es
ruido. **Con menos de 30 operaciones no se puede afirmar nada**, y el panel se
niega a dar veredicto por debajo de ese número en lugar de inventar uno.

Si lo corrés vos, la columna *vs azar* te va a dar unos puntos distinta a esta
tabla, y ADAUSDT puede pasar de "pierde" a "empata" según el día. No es que la
medición esté mal: es exactamente lo que significa un margen de ±7 puntos sobre
40 operaciones. **Ninguna conclusión por activo se sostiene acá**, y esa es la
conclusión. Lo que sí se repite en todas las corridas es que el objetivo casi
nunca se toca.

Lo que sí se sostiene son los dos hechos que no dependen del tamaño de la
muestra: **de 111 operaciones, 4 llegaron al objetivo**, y salieron entre 0.09 y
0.43 operaciones por día cuando querías entre 3 y 5. El bot no puede "entrar y
salir continuamente" porque casi nunca se dan las condiciones que dejan un stop
lo bastante ancho para pagar la comisión.

### El límite de tiempo no es gratis: cuesta 0.34 R

Lo más contraintuitivo de todo. La misma estrategia en ADAUSDT M5, con y sin la
regla de cerrar a los 30 minutos:

| | acierto | R por operación |
|---|---|---|
| sin límite de tiempo | 33.3% | −0.123 |
| cerrando a los 30 minutos | 23.8% | **−0.460** |

Cerrar por tiempo te cuesta 0.34 R por operación. El motivo es simple: en esas
salidas pagás la comisión completa y cobrás un movimiento aleatorio de media
cero. El límite de tiempo se siente como control del riesgo, pero es un impuesto.

### La parte compuesta de tu idea está bien, y por eso hay que cuidarla

Arriesgando 1% del capital por operación, 4 operaciones por día durante 6 meses
(500 operaciones), partiendo de 1000, mediana de 400 corridas:

| acierto | esperanza | capital final |
|---|---|---|
| 28.0% | −0.160 | 429 |
| 33.3% (el azar) | 0.000 | 961 |
| 36.0% | +0.080 | 1.417 |
| 40.0% | +0.200 | 2.574 |
| 45.0% | +0.350 | 5.593 |

Tenías razón en que el compuesto es potente: 12 puntos de acierto separan
perder la mitad de multiplicar por cinco. Pero el compuesto no crea la ventaja,
la **amplifica**, y amplifica el signo que ya tengas. La comisión te empuja por
debajo de 33.3%, y desde ahí lo único que compone es la pérdida.

### Dónde sí da la cuenta

El mismo cálculo en otros mercados, con el costo real de cada uno:

| mercado y activo | mueve en 5 min | costo | en R | acierto para empatar (1:3) |
|---|---|---|---|---|
| **Plata en Exness** | 0.164% | 0.015% | 0.18 R | **29.6%** |
| **Oro en Exness** | 0.100% | 0.015% | 0.30 R | **32.5%** |
| S&P 500 | 0.053% | 0.015% | 0.57 R | 39.3% |
| USDJPY | 0.047% | 0.015% | 0.64 R | 41.0% |
| ADAUSDT en Binance | 0.273% | 0.100% | 0.73 R | 43.3% |
| BTCUSDT en Binance | 0.099% | 0.100% | 2.02 R | 75.5% |
| EURUSD | 0.012% | 0.015% | 2.58 R | 89.5% |

Oro y plata son los únicos donde el scalping de 5 minutos es **aritméticamente
posible**: hace falta 29-33% de acierto y el azar ya da 25-33%. La distancia a
cubrir es de puntos, no de decenas de puntos.

Y fijate en **EURUSD, que es lo peor de toda la tabla**, al revés de lo que
supone casi todo el mundo cuando piensa en scalping. Se mueve 1.4 pips en 5
minutos y el spread es 1 pip: el spread es el 80% del movimiento disponible.
(Los datos de forex de Yahoo vienen redondeados a 1 pip, así que ese número es
orden de magnitud, no precisión; la conclusión no cambia.)

### Lo que no encontré, y lo busqué

Posible no es lo mismo que rentable. La estrategia de impulso + FVG en oro M5,
en 96 combinaciones de umbral de impulso, exigencia de hueco, punto de toque,
colchón y RR:

- **90 de 96 aciertan MENOS que entrar al azar** con el mismo stop. Varias hasta
  22 puntos menos.
- La mejor le gana al azar por 1.6 puntos en 59 operaciones, que es ruido.

Y no es mala suerte, es estructural: entrar en el retroceso a un hueco significa
entrar justo donde el mercado acaba de mostrar que hay órdenes en contra, con el
stop apenas del otro lado de un nivel que todos ven.

### Sobre Deriv

Los **índices sintéticos** de Deriv (Volatility 75, Boom, Crash, Step) no son
mercados: los genera un generador de números aleatorios de Deriv con volatilidad
fija y publicada. Eso significa que la esperanza cero que medí en oro por
casualidad, ahí es cero **por construcción**, y no hay chartismo, FVG ni orden
institucional que encontrar porque no hay flujo de órdenes: no hay nadie del otro
lado. Con cualquier spread la esperanza es estrictamente negativa. Es el peor
lugar posible para esta idea, aunque sea el que tiene la API más cómoda.

Deriv también ofrece forex y metales reales por CFD y su propio MetaTrader 5.
Si querés usar Deriv, usá eso, no los sintéticos.

### Entonces qué

Ordenado por lo que dicen los números:

1. **Binance para scalping de 5 minutos: no.** No es cuestión de afinar
   parámetros, la comisión es igual al movimiento disponible. Binance sigue
   sirviendo para las temporalidades de H1 y M30 del resto del proyecto, donde
   el movimiento es 20 veces más grande que el costo.
2. **Oro o plata en Exness, con MetaTrader 5:** es el único lugar donde el
   costo deja espacio. Está armado en [`metatrader/`](../metatrader/README.md) y
   arranca en modo visual, sin operar.
3. **Deriv sintéticos: no,** por la razón de arriba.
4. **Antes de cualquiera de las tres, usá el panel.** Es lo que más te va a
   servir de todo esto.

### El panel

```bash
cd bot
cp config.scalper.env config.env
ESTRATEGIA=scalper python panel.py
```

Abrilo en el navegador del celular en `http://localhost:8777`. Muestra, por
símbolo, la tendencia mayor, el chequeo de confluencias en vivo (las "3 o 4
características" que te gustaban del bot anterior) y la señal si hay. No manda
órdenes.

Lo importante es el botón **medir**: corre el backtest de la configuración que
tengas puesta y la compara contra entrar al azar en ese mismo activo con ese
mismo stop. Un tablero con cinco luces verdes no dice nada; lo que decide es esa
comparación. Es la herramienta que hubiera ahorrado la mitad del trabajo de este
proyecto, y sirve para cualquier idea que se te ocurra de acá en adelante,
incluidas las que yo no probé.

Trae el margen de error al lado de cada resultado y **no da veredicto con menos de
30 operaciones**. Cerca de un tercio de acierto, el error típico de la medición es
de unos 47/√n puntos: con 30 operaciones son 8.6 puntos y con 100 son 4.7. Como la
ventaja que buscamos es de 4 o 5 puntos, cualquier medición chica es indistinguible
del azar por construcción. Es la trampa en la que caen casi todos los backtests
que se ven en internet: 20 operaciones, 60% de acierto y una conclusión.

## Instalación en Termux

```bash
pkg update && pkg upgrade -y
pkg install python git -y
pip install requests

git clone https://github.com/GR2206/licencias-bot.git
cd licencias-bot/bot
cp config.example.env config.env
```

No hace falta pandas ni numpy: todo está en Python puro justamente para no
pelear con la compilación en Android.

Editá `config.env` con `nano config.env`. Como mínimo:

```
ENTORNO=testnet
MODO=simulacion
SIMBOLOS=BTCUSDT,ETHUSDT
TEMPORALIDADES=1h,30m
RIESGO_PCT=1
```

### Que Android no lo mate

Android suspende los procesos en segundo plano. Dos cosas hacen falta:

```bash
pkg install termux-services -y
termux-wake-lock                 # evita que el celular lo duerma
```

Y en los ajustes de Android: Batería → Termux → sin restricciones.

Para que siga corriendo al cerrar la terminal, usá `tmux`:

```bash
pkg install tmux -y
tmux new -s bot
python bot.py
# Ctrl+B y después D para salir dejándolo corriendo
tmux attach -t bot               # para volver a verlo
```

## Orden de trabajo (no te lo saltees)

**1. Simulá sin claves.** Funciona sin API key: asume una cuenta de 1000 USDT
y registra qué haría.

```bash
python bot.py
```

**2. Mirá el historial.** Cuántas señales da y cómo habrían salido:

```bash
python probar.py CHZUSDT 1h 1500
python probar.py CHZUSDT 30m 1500
```

**3. Verificá la mecánica.** Prueba de integración sin tocar tu cuenta: usa los
filtros reales del símbolo (tick, paso de lote) y comprueba que se manden
entrada + stop + parcial + objetivo, que el stop vaya después de la entrada, que
el tamaño respete el riesgo y que al cobrarse la parcial el stop se mueva a la
entrada.

```bash
python prueba_integracion.py CHZUSDT 1h
python prueba_integracion.py CHZUSDT 30m
```

CHZ cotiza a ~0.015 y se opera en tokens enteros, así que las cantidades son
grandes: con 1000 USDT y `RIESGO_PCT=0.5` una posición son ~13.500 CHZ (200 USDT
de nocional, 40 de margen con x5). Con una cuenta de 50 USDT también entra: el
mínimo de Binance es 5 USDT de nocional.

**4. Testnet con claves de juguete.** Sacá claves gratis en
[testnet.binancefuture.com](https://testnet.binancefuture.com), ponelas en
`config.env` con `ENTORNO=testnet` y `MODO=real`. Dejalo varios días.

**5. Real, si y solo si los pasos anteriores dieron bien.** `ENTORNO=real` y
claves de tu cuenta con permiso de **Futuros solamente**. Nunca habilites
retiros. Empezá con `RIESGO_PCT=0.5`.

## Cómo decide

```
1. Estructura : pivotes de máximo/mínimo (5 velas a cada lado).
2. Ruptura    : el precio CIERRA más allá del último pivote (BOS).
3. Order Block: la última vela contraria antes del impulso. Se exige que el
                impulso desplace más de 1.5 ATR y que la pierna deje un
                imbalance (FVG).
4. Alejamiento: el precio tiene que haberse ido al menos 1 ATR del bloque.
5. Retroceso  : el precio vuelve a entrar en el bloque.
6. Reacción   : la vela que entra debe cerrar a favor del trade.
7. Tendencia  : el SuperTrend tiene que seguir apuntando al mismo lado.
8. Entrada    : a mercado, al cierre de esa vela.
   SL         : borde lejano del bloque + colchón, o 1.5 ATR (el más lejano).
   TP1        : 1R, cierra la mitad y el stop pasa a la entrada.
   TP2        : 3R.
9. Cada bloque se usa una sola vez, y hay 5 velas de espera entre entradas.
```

## Riesgo y tamaño

El tamaño sale del stop, no del apalancamiento:

```
cantidad = (saldo × RIESGO_PCT / 100) / |entrada − stop|
```

Con 1000 USDT, `RIESGO_PCT=1` y un stop a 1.4% del precio, la posición es de
~7100 USDT de nocional y si salta el stop se pierden 10 USDT. **El
apalancamiento no cambia el riesgo**, solo el margen que Binance retiene. Con
x5 alcanza; ponerlo en x50 no te hace ganar más, solo te liquida antes de que
el stop llegue a actuar.

## Archivos

| Archivo | Qué hace |
| --- | --- |
| `bot.py` | loop principal: mira, decide, ejecuta y gestiona la parcial |
| `estrategia.py` | la lógica de confluencia (Order Blocks), espejo del script de Pine |
| `linea_gris.py` | la regla del martillo en la EMA 200 y la ruptura |
| `scalper.py` | impulso e imbalance (FVG) con salida por tiempo, y el contador de confluencias |
| `referencia.py` | **qué da entrar al azar**: el patrón que toda estrategia tiene que superar |
| `panel.py` | panel web para el celular, con el botón de medir contra el azar |
| `indicadores.py` | ATR, EMA, RSI, SuperTrend, pivotes y niveles diarios |
| `binance_api.py` | cliente REST firmado (real o testnet) |
| `probar.py` | simulación de un activo sobre historia real, con comisiones |
| `explorar.py` | recorrido por muchos activos, con prueba de persistencia |
| `prueba_integracion.py` | verifica el camino de órdenes sin tocar la cuenta |
| `config.example.env` | plantilla de configuración, con todo explicado |
| `config.chzusdt.env` | plantilla ya ajustada para CHZUSDT en H1 y M30 |
| `config.multi.env` | canasta de 8 activos en M15, ~4 operaciones por día |
| `config.linea_gris.env` | la regla de la línea gris, 8 activos en M15+H1, 3 por día |
| `config.scalper.env` | scalping en M5, con la cuenta de por qué no cierra en Binance |

Se elige la estrategia con `ESTRATEGIA=order_blocks` (por defecto),
`ESTRATEGIA=linea_gris` o `ESTRATEGIA=scalper`. Las tres comparten el mismo motor
de ejecución, gestión de riesgo y estado, así que todo lo que dice este README
sobre órdenes, tamaño de posición y límites de Binance vale para las tres.

Todos los parámetros de la estrategia se pueden cambiar desde el `.env` en
MAYÚSCULAS (`RR`, `PIVOTE`, `RIESGO_MAX_PCT`, `EXIGIR_FVG`, `PARCIAL_1R`…). No
hace falta editar código para afinar un activo.

`config.env`, `estado.json` y `bot.log` están en `.gitignore`: tus claves no se
suben a ningún lado.

## Límites conocidos

- **Modo one-way.** Si tu cuenta está en modo hedge, las órdenes necesitan
  `positionSide` y van a fallar.
- **Órdenes a mercado.** Se paga comisión taker (0.05% por lado) y hay
  slippage. Entrar con órdenes límite en el 50% del bloque mejoraría el precio,
  pero exige manejar fills parciales.
- **Sin reintentos finos.** Si Binance rechaza una orden, el bot lo registra y
  sigue. Revisá el log.
- **La ventana de velas importa.** El bot solo ve las últimas `VELAS` velas. Si
  un Order Block quedó fuera de esa ventana, para el bot no existe, y entonces
  opera distinto a lo que probaste. Medido en CHZ M30: con `VELAS=500` y bloques
  sin caducidad se perdía 1 de cada 8 señales. Por eso `VELAS=1500` (el máximo de
  Binance) y `EDAD_MAX_BLOQUE=250`; con eso el vivo y el backtest coinciden al
  100%. El bot avisa al arrancar si la combinación no cierra.
- **Reinicio.** El estado vive en `estado.json`. Si lo borrás con una posición
  abierta, el bot pierde el hilo de la parcial (la posición sigue con su SL y
  TP en Binance, eso no se pierde).
- **Lo probado es CHZ (2.3 años) más BTC/ETH/SOL (4 meses).** En CHZ, que es la
  muestra larga, el resultado es negativo. No hay razón para suponer que en otro
  activo va a ser distinto sin medirlo antes con la misma cantidad de historia.
