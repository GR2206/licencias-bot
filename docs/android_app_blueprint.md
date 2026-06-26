# Sniper Pro Android App - Blueprint

## Decisión de arquitectura

La app Android debe funcionar como panel de control y monitoreo. El motor real del bot, las API keys de Binance y la ejecución de órdenes deben vivir en un backend seguro.

Motivos:

- Las API keys no deben guardarse en una APK porque se pueden extraer.
- Android puede cerrar procesos en segundo plano y cortar el bot.
- Si Binance usa IP whitelist, un teléfono usa IP móvil/dinámica. El backend/VPS debe tener IP fija.
- El backend puede registrar logs, estados, errores, métricas y reconectar sin depender del celular.

Arquitectura recomendada:

```text
Android App
  |
  | HTTPS + WebSocket/SSE
  v
Backend Flask/FastAPI seguro
  |
  | Ejecuta/controla
  v
Bot Python: mainmaster.py + cerebro.py + sniper_ai.py + velas.py
  |
  v
Binance Futures API
```

## Stack recomendado

### Android

- Kotlin
- Jetpack Compose
- Material 3
- Coroutines + Flow
- Retrofit/OkHttp para REST
- WebSocket o Server-Sent Events para consola en vivo
- DataStore para preferencias no sensibles

### Backend

El repo actual ya tiene Flask. Puede extenderse con endpoints del bot.

Para producción conviene:

- Guardar Binance API Key/Secret cifradas en backend.
- Usar un token JWT o serial de licencia para autenticar la app.
- Nunca enviar `api_secret` de vuelta al Android.
- Exponer estado del bot, logs, balance y configuración por endpoints.

## Pantallas de la app

### 1. Splash / Login

Objetivo:

- Validar licencia o usuario.
- Mostrar estado del servidor.

Visual:

- Fondo azul noche.
- Logo "Sniper Pro".
- Animación neon tipo pulso.
- Texto: `Conectando con núcleo Sniper...`

Estados:

- Servidor conectado.
- Licencia inválida.
- Backend no disponible.

### 2. Dashboard principal

Widgets:

1. Estado Binance
   - Badge verde: `Binance conectado`
   - Badge rojo: `Binance desconectado`
   - Badge amarillo: `IP no autorizada`

2. Balance general
   - Balance total USDT.
   - PnL abierto.
   - PnL diario.
   - Margen usado.

3. Riesgo por trade
   - Slider moderno:
     - 0.25%
     - 0.50%
     - 1.00%
     - 1.50%
     - 2.00%
   - Botones `-` y `+`.
   - Texto: `Riesgo real si toca SL`.

4. Trades activos
   - Cards por activo.
   - Dirección LONG/SHORT.
   - Entrada, SL, TP, PnL.
   - Barra TP/SL.

### 3. Configuración Binance

Campos:

- API Key
- API Secret
- Leverage
- Porcentaje por trade / margen máximo
- Max trades activos
- Modo:
  - Conservador
  - Balanceado
  - Agresivo

Importante:

- La app envía API Key/Secret solo una vez por HTTPS.
- El backend las guarda cifradas.
- La app nunca vuelve a mostrarlas completas.

UX:

- API Key visible parcial:
  ```text
  xxxxxxxx...AB92
  ```
- Botón: `Probar conexión`
- Botón: `Guardar configuración`
- Botón: `Eliminar keys`

### 4. Gestión de activos

Funcionalidad:

- Lista editable de símbolos:
  - BTCUSDT
  - ETHUSDT
  - SOLUSDT
  - etc.

Acciones:

- Agregar activo.
- Quitar activo.
- Activar/desactivar activo con switch.
- Buscar símbolos.
- Ordenar favoritos.

Visual:

- Chips neon.
- Estado por activo:
  - `Analizando`
  - `En trade`
  - `Bloqueado`
  - `Esperando`

### 5. Consola por activo

Debe mostrar algo como la consola actual, pero más legible:

```text
BTCUSDT
Régimen: ALCISTA FUERTE
Score: 2.35 | Votos: 3
SL distancia: 1.40%
Estado: ESPERAR
Motivo: volumen bajo | falta RSI
```

Diseño:

- Tabs o chips por activo.
- Log en vivo.
- Colores:
  - Verde brillante: trade confirmado / protegido.
  - Rojo vivo: bloqueo / error / SL demasiado grande.
  - Amarillo: advertencia.
  - Cian: análisis informativo.

### 6. Barra TP/SL

Idea visual:

Una barra horizontal con punto central como entrada.

```text
SL <---------------- ENTRADA ----------------> TP
rojo                     0                    verde
```

Regla:

- Centro = precio de entrada.
- Izquierda roja = distancia al SL.
- Derecha verde = distancia al TP.
- El marcador se mueve según el precio actual.

Ejemplo:

```text
[-100% SL] ---- [Entrada 0%] ---- [+100% TP]
```

Datos necesarios por trade:

```json
{
  "symbol": "BTCUSDT",
  "side": "LONG",
  "entry": 63400.5,
  "current": 63800.2,
  "sl": 62500.0,
  "tp": 65200.0,
  "progress_to_sl_pct": 0,
  "progress_to_tp_pct": 22.4
}
```

Para LONG:

```text
progress_to_tp = (current - entry) / (tp - entry) * 100
progress_to_sl = (entry - current) / (entry - sl) * 100
```

Para SHORT:

```text
progress_to_tp = (entry - current) / (entry - tp) * 100
progress_to_sl = (current - entry) / (sl - entry) * 100
```

UI:

- Si va hacia TP: verde brillante.
- Si va hacia SL: rojo brillante.
- Si está cerca de entrada: azul/cian.
- Animación suave del marcador.

### 7. Recuadro de error por IP no reconocida

Si Binance devuelve error de IP no autorizada, mostrar:

```text
IP no autorizada por Binance

Binance rechazó la conexión porque la IP del servidor no está en la whitelist.

IP que debes agregar:
123.45.67.89

Ruta:
Binance -> API Management -> Edit Restrictions -> IP Access Restriction
```

Botones:

- `Copiar IP`
- `Ver guía`
- `Reintentar conexión`

Backend debe devolver:

```json
{
  "status": "ip_not_allowed",
  "server_ip": "123.45.67.89",
  "binance_error": "Invalid API-key, IP, or permissions for action"
}
```

## Endpoints recomendados

### Estado general

`GET /bot/status`

Respuesta:

```json
{
  "running": true,
  "binance_connected": true,
  "server_ip": "123.45.67.89",
  "balance_total": 254.33,
  "pnl_abierto": 4.27,
  "trades_activos": 3,
  "risk_pct": 1.0,
  "leverage": 10
}
```

### Configuración

`GET /bot/config`

```json
{
  "leverage": 10,
  "risk_pct": 1.0,
  "max_trades": 4,
  "assets": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
}
```

`POST /bot/config`

```json
{
  "leverage": 10,
  "risk_pct": 1.0,
  "max_trades": 4,
  "assets": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
}
```

### Guardar Binance keys

`POST /bot/binance-keys`

```json
{
  "api_key": "...",
  "api_secret": "..."
}
```

Respuesta:

```json
{
  "status": "ok",
  "masked_key": "abcd...92ZZ"
}
```

### Probar conexión Binance

`POST /bot/binance-test`

Respuesta OK:

```json
{
  "status": "connected",
  "server_ip": "123.45.67.89"
}
```

Respuesta IP error:

```json
{
  "status": "ip_not_allowed",
  "server_ip": "123.45.67.89",
  "message": "Agrega esta IP a Binance API whitelist"
}
```

### Trades activos

`GET /bot/trades`

```json
[
  {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "entry": 63400.5,
    "current": 63800.2,
    "sl": 62500.0,
    "tp": 65200.0,
    "pnl": 3.12,
    "pnl_pct": 1.24,
    "progress_to_tp_pct": 22.4,
    "progress_to_sl_pct": 0
  }
]
```

### Consola en vivo

Opción REST:

`GET /bot/logs?symbol=BTCUSDT&limit=100`

Opción ideal:

`WS /bot/ws/logs`

Evento:

```json
{
  "symbol": "BTCUSDT",
  "level": "error",
  "message": "SL demasiado grande (9.67% > 2.50%)",
  "timestamp": "2026-06-08T15:20:00Z"
}
```

### Control del bot

```text
POST /bot/start
POST /bot/pause
POST /bot/resume
POST /bot/panic
POST /bot/close-all
```

## Diseño visual

Tema: neon azul noche.

Paleta:

```text
Background principal: #050B1E
Panel cards:          #0B1633
Azul neon:            #00D4FF
Verde trade:          #00FF9C
Rojo riesgo:          #FF2D55
Violeta acento:       #7C4DFF
Texto principal:      #EAF6FF
Texto secundario:     #7F93B5
Amarillo alerta:      #FFD166
```

Efectos:

- Glow suave en cards activas.
- Botones con borde neon.
- Fondo con gradiente radial azul/violeta.
- Animación de pulso en estado conectado.
- Barra TP/SL con brillo verde/rojo.
- Consola con tipografía monoespaciada.

Componentes visuales:

- `NeonCard`
- `ConnectionBadge`
- `RiskSlider`
- `TradeProgressBar`
- `AssetConsole`
- `IpWhitelistErrorCard`
- `BotControlPanel`

## Wireframe textual

```text
┌─────────────────────────────────────┐
│ SNIPER PRO                    ● LIVE │
│ Balance: 254.33 USDT                │
│ PnL abierto: +4.27 USDT             │
├─────────────────────────────────────┤
│ Binance: CONECTADO                  │
│ Server IP: 123.45.67.89             │
├─────────────────────────────────────┤
│ Riesgo por trade                    │
│ [-] 1.00% [+]                       │
│ Max trades: 4 | Leverage: x10       │
├─────────────────────────────────────┤
│ BTCUSDT LONG                        │
│ Entry 63400 | SL 62500 | TP 65200   │
│ RED [-----|----●--------] GREEN     │
│ PnL +3.12 USDT                      │
├─────────────────────────────────────┤
│ Consola BTCUSDT                     │
│ 🧠 Régimen: ALCISTA FUERTE          │
│ ❌ SL demasiado grande              │
│ ⏳ ESPERAR                          │
└─────────────────────────────────────┘
```

## Recomendación de primera versión MVP

La primera versión debería incluir:

1. Pantalla de configuración Binance.
2. Test de conexión Binance + IP del servidor.
3. Dashboard con balance.
4. Configuración de riesgo/leverage/activos.
5. Consola por activo.
6. Trades activos con barra TP/SL.
7. Botones pause/resume/panic.

Después agregaría:

1. Gráficos embebidos.
2. Estadísticas IA por activo.
3. Push notifications.
4. Modo demo/backtest.
5. Tema personalizable.

## Puntos críticos antes de programar Android

- Crear endpoints backend del bot.
- Separar configuración sensible de `config.py`.
- Guardar API keys cifradas.
- Exponer logs por WebSocket/SSE.
- Ejecutar el bot como proceso controlable desde backend.
- Usar IP fija en servidor para Binance whitelist.

