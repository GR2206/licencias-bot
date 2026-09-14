# Bot de confluencia — Tendencia + Order Block

Bot que replica en Python la lógica de
[`tradingview/confluence_engine.pine`](../tradingview/confluence_engine.pine) y
ejecuta en **Binance Futures USDT-M**. Pensado para correr en un celular con
Termux, en H1 y M30.

**No usa webhooks ni depende de TradingView.** Baja las velas de Binance y
calcula todo él mismo. Eso es a propósito: un webhook de TradingView necesita
una IP pública, y un celular no la tiene. Si el gráfico y el bot calculan lo
mismo, no hace falta el puente.

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
| `estrategia.py` | la lógica de confluencia, espejo del script de Pine |
| `indicadores.py` | ATR, EMA, RSI, SuperTrend, pivotes y niveles diarios |
| `binance_api.py` | cliente REST firmado (real o testnet) |
| `probar.py` | simulación de un activo sobre historia real, con comisiones |
| `explorar.py` | recorrido por muchos activos, con prueba de persistencia |
| `prueba_integracion.py` | verifica el camino de órdenes sin tocar la cuenta |
| `config.example.env` | plantilla de configuración, con todo explicado |
| `config.chzusdt.env` | plantilla ya ajustada para CHZUSDT en H1 y M30 |
| `config.multi.env` | canasta de 8 activos en M15, ~4 operaciones por día |

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
