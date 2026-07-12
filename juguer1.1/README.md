# Juguer 1.1

Bot de scalping **conservador** para Binance Futures (Python).

## Filosofía

| Objetivo | Cómo |
|----------|------|
| Winrate alto | Solo entra con **4+ confluencias** y tendencia 15m clara |
| Ratio 3:2 | TP = **1.5 × SL** — con 60% WR ya sos rentable |
| Ganancias chicas, seguidas | SL 0.25–0.60%, varios trades/día posibles |
| No arruinarse | 0.75% riesgo/trade, máx 2% pérdida/día, pausa tras 3 SL |

> Ningún bot garantiza 60% WR en todos los mercados. Juguer 1.1 prioriza **selectividad** sobre cantidad. Probá primero en **testnet**.

## Estructura

```
juguer1.1/
├── bot.py              # Loop principal
├── strategy.py         # Señales Pulse Scalp
├── exchange.py         # Binance Futures
├── risk.py             # Tamaño y límites
├── state.py            # Persistencia
├── telegram_ctl.py     # Control desde el celular
├── config.example.py   # → copiar a config.py
├── run_termux.sh       # Android Termux
└── requirements.txt
```

## Instalación rápida (PC / VPS)

```bash
cd juguer1.1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.py config.py
# Editar config.py con API keys y Telegram
python bot.py
```

## Uso en el celular (Android + Termux)

1. Instalá [Termux](https://termux.dev/) desde F-Droid (no Play Store).
2. Cloná o copiá la carpeta `juguer1.1` al teléfono.
3. `pkg install python git` y `pip install -r requirements.txt`
4. Configurá `config.py`
5. `bash run_termux.sh` — activa **wake lock** para que no se corte al bloquear pantalla.
6. En `config.py` usá `TELEGRAM_POLLING = False` (recomendado en Termux).
7. Controlá por consola o, si tu red lo permite, alertas por Telegram.

El bot corre en segundo plano; la pantalla puede estar apagada.

## Telegram

Creá un bot con [@BotFather](https://t.me/BotFather), obtené token y tu `chat_id` (@userinfobot).

**Termux / redes inestables:** muchas redes bloquean o cortan el long-polling de Telegram. Usá:

```python
TELEGRAM_POLLING = False   # solo alertas de entradas/cierres (sin /status)
TELEGRAM_OPTIONAL = True   # arranca aunque Telegram falle
```

Si no conecta en absoluto: `TELEGRAM_DISABLED = True` y operá solo por consola.

Comandos (solo con `TELEGRAM_POLLING = True`):
- `/status` — balance y posición
- `/stats` — winrate y PnL
- `/pause` / `/resume`
- `/close` — cerrar posición manual

## Estrategia (Pulse Scalp)

**15m:** tendencia EMA20 vs EMA50 + ADX ≥ 22  
**5m:** pullback a Bollinger + RSI girando + EMA9/21 + volumen

Solo opera **a favor** de la tendencia 15m. En rango (ADX bajo) no opera.

## Configuración recomendada para empezar

```python
SYMBOLS = ["BTCUSDT"]   # Un solo activo líquido
LEVERAGE = 5
RISK_PER_TRADE = 0.0075 # 0.75%
MAX_TRADES_PER_DAY = 6
TESTNET = True          # Primero paper
```

## Testnet

1. Cuenta en https://testnet.binancefuture.com
2. API keys de testnet
3. `TESTNET = True` en config.py

## VPS (recomendado para 24/7)

En un VPS de $5/mes el bot no depende del teléfono. Usá `screen` o `systemd`:

```bash
screen -S juguer
python bot.py
# Ctrl+A D para desacoplar
```

Controlás igual por Telegram desde el celular.
