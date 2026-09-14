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

Por eso el bot arranca en `MODO=simulacion` y por eso insisto abajo con el
testnet. No es burocracia: es que todavía no hay evidencia de que esto gane
plata, y las comisiones (0.10% ida y vuelta) se comen buena parte de cada
operación.

Lo que sí funciona bien es la **parte visual**: marcar Order Blocks con reglas
mecánicas y ver la confluencia en el gráfico. Como herramienta para decidir a
mano, sirve. Como piloto automático, todavía no.

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
python probar.py BTCUSDT 1h 1500
python probar.py ETHUSDT 30m 1500
```

**3. Verificá la mecánica.** Prueba de integración sin tocar tu cuenta:
comprueba que se manden entrada + stop + parcial + objetivo, que el stop vaya
después de la entrada, que el tamaño respete el riesgo y que al cobrarse la
parcial el stop se mueva a la entrada.

```bash
python prueba_integracion.py
```

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
| `probar.py` | simulación sobre historia real, con comisiones |
| `prueba_integracion.py` | verifica el camino de órdenes sin tocar la cuenta |
| `config.example.env` | plantilla de configuración |

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
- **Reinicio.** El estado vive en `estado.json`. Si lo borrás con una posición
  abierta, el bot pierde el hilo de la parcial (la posición sigue con su SL y
  TP en Binance, eso no se pierde).
- **La muestra de las pruebas es corta**: 4 meses y 3 activos. No alcanza para
  afirmar nada con confianza.
