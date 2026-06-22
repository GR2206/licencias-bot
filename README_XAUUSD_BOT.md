# XAUUSD scalping bot

Bot para scalping de oro en velas M1 con objetivo de operaciones cortas. Por
seguridad arranca en `PAPER_MODE=True` y el modo MT5 real requiere doble
confirmacion:

1. `REAL_TRADING_ENABLED=True` en `config.py`
2. `ALLOW_REAL_TRADING=1` en el entorno

## Evaluacion del codigo original

- La primera version usaba un volumen fijo enorme para una cuenta chica. Eso
  podia crear ganancias simuladas grandes, pero con riesgo de liquidacion real.
- La fuente Yahoo fallaba con errores de CSV y dejaba al bot inestable.
- La version con Bybit y sizing dinamico mejoro mucho, pero seguia entrando en
  cruces de EMA durante ruido lateral.
- El historial subido muestra rachas de perdidas antes de recuperarse. Para un
  sistema compuesto, esa racha importa mas que la cantidad de trades.
- Faltaba una regla dura de salida por tiempo, aunque la intencion era no estar
  mas de 1 o 2 minutos.

## Cambios principales

- Position sizing por riesgo: `RISK_PER_TRADE` limita cuanto se arriesga por
  operacion y `CAPITAL_USAGE` limita el notional maximo.
- Filtro de tendencia: el bot solo compra sobre EMA50 con pendiente positiva y
  solo vende bajo EMA50 con pendiente negativa.
- Filtro ATR: evita mercados muertos y tambien volatilidad extrema.
- Filtro anti-chase: evita perseguir precio cuando ya se alejo demasiado de la
  tendencia.
- Salida por tiempo: `MAX_HOLD_MINUTES=2`.
- Paper mode mas realista: simula spread, slippage y comision.
- Politica conservadora si TP y SL se tocan en la misma vela: cuenta SL.
- MT5 engine opcional con magic number, limite de spread y cierre temporal.

## Uso paper

```bash
python3 -m pip install -r requirements.txt
python3 bot.py
```

## Uso MT5

Recomendado solo en demo al principio.

```bash
export ALLOW_REAL_TRADING=1
export MT5_LOGIN="..."
export MT5_PASSWORD="..."
export MT5_SERVER="..."
# opcional si tu instalacion lo requiere:
export MT5_PATH="/ruta/a/terminal64.exe"
```

Luego poner en `config.py`:

```python
PAPER_MODE = False
REAL_TRADING_ENABLED = True
```

## Punto profesional importante

No hay garantia de win rate. Para buscar "muchas ganancias pequenas" el bot
debe medir expectativa neta despues de spread, slippage y comision. Si el
spread real de tu broker sube, el sistema debe operar menos o apagarse.
