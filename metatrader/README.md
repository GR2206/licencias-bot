# La línea gris en MetaTrader 5 (Exness)

Expert Advisor que aplica la regla de la línea gris (martillo en la EMA 200 y
ruptura) a **oro, forex, índices y CFDs de cripto** en MetaTrader 5. Es el mismo
cálculo que [`tradingview/linea_gris.pine`](../tradingview/linea_gris.pine) y que
[`bot/linea_gris.py`](../bot/linea_gris.py).

**Arranca con `SoloVisual = true`: dibuja las señales y no manda ninguna orden.**

## Antes que nada: qué dio al medirlo

Bajé **2.4 años de oro y plata en H1** y **2.8 años de 9 pares de forex**, más el
S&P 500, y apliqué la regla con los parámetros ya fijados en las criptos (o sea,
sin re-optimizar nada: es un test fuera de muestra en otra clase de activo). Con
costos de Exness:

| | movimiento del período | short al romper (la regla pedida) | long al romper (el espejo) |
| --- | --- | --- | --- |
| Oro | +82% | 67 ops, **−0.352R** | 72 ops, **+0.084R** |
| Plata | +129% | 79 ops, −0.259R | 92 ops, +0.068R |
| EURUSD | +5.5% | 24 ops, −0.201R | 25 ops, +0.165R |
| GBPUSD | +6.9% | 26 ops, +0.119R | 20 ops, +0.466R |
| USDJPY | +4.1% | 44 ops, +0.102R | 26 ops, +0.313R |
| EURJPY | +9.8% | 37 ops, −0.358R | 32 ops, +0.524R |
| S&P 500 | +75% | 28 ops, +0.045R | 34 ops, +0.586R |
| **Todos juntos** | | **602 ops, −0.156R** | **596 ops, +0.213R** |

En este período **todo subió**, y gana el lado largo. En las 25 criptos de
Binance, donde 23 de 25 bajaron un 56% de mediana, pasa exactamente lo contrario:
ahí ganaba el lado corto (+0.030R) y perdía el largo (−0.166R).

La prueba que lo cierra es la **versión simétrica**, que toma la ruptura para los
dos lados y es la única que se puede operar sin adivinar el futuro:

| Mercado | Operaciones | R por operación |
| --- | --- | --- |
| Oro, plata, forex e índices (todos subieron) | 638 | +0.011 |
| 25 criptos de Binance (casi todas bajaron) | 553 | −0.088 |

Cero en los dos casos. **La regla no detecta nada: apuesta a la dirección, y la
dirección la puso el mercado.** El rebote con martillo, tu idea original para el
largo, da −0.194R en 60 operaciones acá, igual que en cripto.

Los datos son de Yahoo Finance (indicativos, sin spread real). Sirven para
decidir si la estrategia respira; para los centavos, usá el Probador de
Estrategias de MT5 sobre tu propia cuenta, que es lo que explico abajo.

## Lo que sí es una ventaja real de venir a MetaTrader

**El costo, y por lejos.** No es un detalle: es lo que venía arruinando todo en
Binance.

| Dónde | Costo de ida y vuelta | Sobre un stop de… | El costo se lleva |
| --- | --- | --- | --- |
| Binance futuros | ~0.10% del nocional + slippage | 0.2% | 0.50R |
| Binance futuros | ~0.10% | 1.5% | 0.07R |
| Oro en Exness (spread 0.20 sobre 4300) | ~0.005% | 0.2% | 0.03R |
| EURUSD (spread 1 pip) | ~0.009% | 0.2% | 0.05R |

Entre **10 y 20 veces más barato** en términos relativos. Acordate del problema
que veníamos arrastrando: los stops cortos eran inoperables en Binance porque la
comisión se los comía, y por eso hubo que poner un piso del 1% al stop. Acá ese
piso baja a 0.30% y en cuenta Raw a 0.16%. **Los stops cortos que querías desde
el principio recién son viables acá.**

El EA calcula ese piso solo, a partir de dos parámetros: `CostoIdaVueltaPct` (tu
costo real, medilo en la pestaña de especificación del símbolo) y `CostoMaximoR`
(cuánto de 1R aceptás regalar, 0.05 = 5%). Si querés fijarlo a mano, poné
`StopMinimoPct`.

Ojo con el otro lado de la moneda: en oro y forex en H1 la regla dispara **0.03 a
0.10 veces por día**. Las 3-5 operaciones diarias que buscabas no salen de un
símbolo en H1; necesitás varios símbolos o bajar a M15, y en M15 los stops se
achican y el costo relativo vuelve a subir.

## MT5 o MT4: usá MT5

| | MT4 | MT5 |
| --- | --- | --- |
| Lenguaje | MQL4, congelado | MQL5, mantenido |
| Probador | sólo por precios de cierre o modelado aproximado | **ticks reales, con tu spread histórico** |
| Probar varios símbolos a la vez | no | sí |
| Órdenes y posiciones | modelo viejo, cada orden es una posición | netting o hedging, más parecido a la realidad |
| Exness | soportado | soportado |
| Futuro | MetaQuotes no le agrega nada nuevo | es lo que se desarrolla |

La razón que decide es el **Probador con ticks reales**. Sin eso no podés medir
una estrategia de stops cortos, porque el resultado depende del spread en cada
momento y del orden en que se tocan el stop y el objetivo dentro de la vela. El
EA está escrito en MQL5 y compilado sin errores ni advertencias.

## ¿PC o Android? Esto es importante

**Los Expert Advisors no corren en la app de Android.** No es una limitación del
código: las terminales móviles de MT4 y MT5 no ejecutan EAs ni indicadores
personalizados, sólo sirven para mirar y operar a mano. No hay forma de dar
vuelta eso.

Tenés tres caminos reales:

1. **VPS de MQL5 (lo que yo haría).** Está integrado en MT5: click derecho sobre
   la cuenta en el Navegador → *Registrarse en un servicio de hospedaje virtual*.
   Cuesta desde unos 10 USD por mes, migra el EA y su configuración con un click,
   corre 24/5 aunque apagues todo, y elige el servidor con menor latencia a tu
   broker. Desde el celular entrás con la app de MT5 a **mirar** las operaciones
   que el VPS va haciendo.
2. **VPS de Exness.** Exness ofrece VPS gratis si tu cuenta cumple el mínimo de
   depósito o volumen que piden en ese momento. Verificalo en tu Área Personal,
   porque las condiciones cambian.
3. **Una PC con Windows prendida.** Funciona, pero si se reinicia, se corta
   internet o Windows actualiza a la noche, el EA deja de operar y las posiciones
   quedan abiertas con su SL y TP en el broker. Es la opción más frágil.

Comparado con el bot de Python en Termux: ese sí corre en el celular, porque
Binance tiene API REST pública y no necesita MetaTrader. Exness no expone una API
así para cuentas de retail, y la librería de Python de MetaTrader5 sólo funciona
contra el terminal de Windows. Por eso, para Exness, el camino es EA + VPS.

## Instalación

1. En MT5: **Archivo → Abrir carpeta de datos**.
2. Copiá `LineaGris.mq5` en `MQL5\Experts\`.
3. Abrí **MetaEditor** (F4), abrí el archivo y compilá con **F7**. Tiene que
   decir *0 errores, 0 advertencias*.
4. Volvé al terminal, **Ver → Navegador** (Ctrl+N), y arrastrá `LineaGris` sobre
   el gráfico del símbolo y la temporalidad que quieras.
5. En la pestaña **Común**, tildá *Permitir el trading algorítmico*. En
   **Parámetros de entrada** dejá `SoloVisual = true` hasta que lo hayas probado.
6. El botón **Algo Trading** de la barra tiene que estar en verde.

### Nombres de los símbolos en Exness

Según el tipo de cuenta, Exness le pone sufijo a los símbolos: `XAUUSDm`,
`EURUSDm` en las cuentas Standard, y sin sufijo o con otro en las demás. Usá el
que aparece en tu Observación de Mercado; el EA toma el símbolo del gráfico, así
que no hay nada que configurar.

## Probarlo bien, que es lo único que importa

En el Probador de Estrategias (Ctrl+R):

- **Modelado: "Cada tick basado en ticks reales"**. Cualquier otra opción te va a
  mentir con una estrategia de stops cortos.
- **Período: al menos 2 años.** Con menos, cualquier resultado es ruido.
- **Depósito y apalancamiento iguales a los de tu cuenta real.**
- Fijate que en el informe aparezcan las comisiones y el swap. Si el swap sale en
  cero, revisá que el símbolo los tenga cargados.

Y después, la parte incómoda: probá también el `Direccion = SOLO_LONG` y el
`SOLO_SHORT` por separado, y sobre dos períodos distintos. Si uno gana y el otro
pierde, lo que estás midiendo es la dirección que tomó el mercado en ese tramo,
no la estrategia. Eso es exactamente lo que me pasó a mí con estos datos, y es la
razón por la que el EA arranca en modo visual.

## Parámetros

| Grupo | Parámetro | Para qué |
| --- | --- | --- |
| ① Línea | `EmaPeriodo` | 200 es la línea gris. En H1 son ~8 días de rueda |
| | `ExigirPendiente` | el rebote sólo cuenta si la línea va a favor |
| ② Martillo | `UsarMartillo` | el rebote largo. Medido: −0.19R. Está para verlo |
| | `MechaFraccion` / `MechaMinimo` | qué tan exigente es la definición de martillo |
| ③ Ruptura | `Direccion` | `AMBOS_LADOS` es la única honesta. Los otros dos son para el experimento de arriba |
| | `RupturaAtr` | cuánto tiene que pasarse el cierre, para no contar roces |
| ④ Riesgo | `RiesgoPorcentaje` | % del balance por operación. Redondea el lote **para abajo** |
| | `CostoIdaVueltaPct` | tu costo real. De acá sale el piso del stop |
| | `CostoMaximoR` | cuánto de 1R puede comerse el costo (0.05 = 5%) |
| ⑤ Seguridad | `SoloVisual` | el candado. `true` = no manda órdenes |
| | `SpreadMaximoPuntos` | no operar cuando el spread se abre (noticias, apertura) |
| | `CerrarViernes` | cerrar antes del fin de semana y evitar el gap del lunes |

El EA respeta el `SYMBOL_TRADE_STOPS_LEVEL` del broker, el lote mínimo, el paso
de lote y el modo de llenado, y si el lote mínimo obligaría a arriesgar más de lo
configurado **saltea la señal en vez de operar más grande**. Eso importa en oro:
0.01 lotes son 1 onza, y con un stop de 8 USD el riesgo mínimo por operación
queda en 8 USD, que sobre una cuenta chica ya es más del 0.5%.

## Entonces, qué operar y dónde

- **Cripto**: en Binance con el bot de Python. Los CFDs de cripto de Exness
  tienen spread mucho más ancho y swap, así que son peores para lo mismo.
- **Oro y forex**: acá, en MT5, por el costo relativo bajísimo. Pero **medido, la
  regla no tiene ventaja en ninguno de los dos**, y en el oro puntualmente es la
  peor del grupo, porque el oro estuvo subiendo y la regla que pediste vende.
- **¿Activos menos volátiles ayudan?** Por sí misma la volatilidad no cambia
  nada: la R ya normaliza el tamaño del movimiento. Lo que cambia es la relación
  costo/stop, y ahí forex y oro ganan por mucho. Pero menos volatilidad también
  es menos señales y esperas más largas.

Mi recomendación concreta: usá el EA en modo visual sobre el oro y un par de
forex, mirá si los carteles caen donde vos los pondrías a mano, y corré el
Probador con ticks reales sobre tu cuenta. Si en tu propio probador la versión
`AMBOS_LADOS` no da claramente positiva en dos períodos distintos, no le sueltes
la mano al `SoloVisual`.
