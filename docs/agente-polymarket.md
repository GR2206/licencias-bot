# Agente de Polymarket en simulación

Replica el sistema de la guía: mira mercados públicos, compara una probabilidad propia contra el precio y registra la decisión. Arranca en paper trading. No envía órdenes, no pide una wallet y no guarda claves privadas.

El número de “50 dólares a miles en 48 horas” es una afirmación sin historial público. No es un resultado que este programa intente reproducir. Lo que sí reproduce es el sistema: filtros duros, edge neto de comisión y spread, Kelly a un cuarto con techo del 6%, y un registro que no se reescribe.

## Qué hace una corrida

1. Lee mercados abiertos en Gamma (`/markets/keyset`), sin autenticación.
2. Tira los que ya no sirven: precio fuera de 0,05–0,95, resolución a más de 90 días, spread mayor a 3 centavos.
3. Solo sobre los que quedan pide evidencia. Sin evidencia, la fila queda `pendiente`. No inventa una probabilidad.
4. Redondea la probabilidad a múltiplos de 5%. Si queda a más de 25 puntos del precio, asume un error de lectura, salvo que una persona marque que revisó la regla y las fechas.
5. Calcula `edge_neto = (probabilidad − precio medio) − comisión − spread`, en puntos porcentuales. Menos de 8: se descarta.
6. Tamaño: `min(0,25 × Kelly, 0,06)` del efectivo de hoy. Mira profundidad (10 veces el tamaño en los primeros niveles) y el mínimo del mercado.
7. Si pasa, escribe una fila simulada. `ejecutado` queda en `no`.
8. Cuando el mercado cierra, completa únicamente `resultado` y `pnl`.

Una corrida con cero simulaciones es una corrida exitosa. Descartar es el trabajo.

## De dónde sale el acierto

No sale de correr cada 10 minutos ni de que el texto suene seguro. Sale de medir poco y de no dejar que el modelo se suba el tamaño.

- Primero la evidencia, después el número. Al revés, el número solo justifica la primera intuición.
- Dos fuentes con fecha y con dominios distintos. Un posteo de X no cuenta como evidencia.
- En mercados rápidos (deportes, crypto, o los que cierran en 7 días) las fuentes tienen que tener menos de 48 horas.
- La comisión se come edge justo cerca del 50%. En el ejemplo de la guía, 53% contra un YES a 0,42 en crypto son 11 puntos brutos y cerca de 9 netos, antes del spread. Por eso el umbral se mira neto.
- 53% se redondea a 55%. Un decimal de más es precisión falsa.
- Kelly completo supone que tu probabilidad es la verdadera. Acá la estimó un modelo. Se usa un cuarto, y además se corta al 6% del bankroll.
- No se aumenta el tamaño después de perder: el stake sale del efectivo que queda.
- No se abre otra posición del mismo evento.
- Si el mismo mercado muestra un edge más grande en la corrida siguiente, no se acumula: es sesgo, no una oportunidad que mejoró.
- Si el equity simulado cae 25% desde su máximo, no hay operaciones nuevas.
- El acierto se juzga con mercados ya resueltos, por rangos de probabilidad. Hasta que no cierra, no se sabe nada. Con menos de 20 resueltas, no se cambia la estrategia. Con menos de 10 en una categoría, la lectura es “muestra insuficiente”.
- Los límites viven en `polymarket_agent/risk.py`. El proceso no tiene cómo modificarlos. Un cambio, si alguna vez hace falta, es un diff y solo puede volverlos más estrictos.

El modelo, si lo usás con `--llm`, solo puede proponer evidencia y una probabilidad. El stake lo calcula el código. Si el modelo dice que revisó una divergencia de 25 puntos, esa marca se ignora.

## Cómo usarlo

Desde la raíz del repo:

```bash
python -m polymarket_agent ejemplo
python -m polymarket_agent limites
python -m polymarket_agent scan --pages 1 --limit 100
python -m polymarket_agent resumen
```

El registro queda en `/workspace/polymarket/registro.csv`. El bankroll simulado inicial es 1000. Se puede cambiar la primera vez con `--bankroll` o `PAPER_BANKROLL`. Después queda guardado en `estado.json` y no se pisa.

Para simular una decisión, copiá `examples/investigacion.ejemplo.json`, poné el `slug` real, la regla de resolución en una frase, dos razones a favor, dos en contra, la hipótesis de la contraparte y dos fuentes con fecha. `confidence` en `baja` solo registra. `media` o `alta` puede simular si el código deja pasar el resto.

```bash
python -m polymarket_agent decidir investigaciones/mi-mercado.json
python -m polymarket_agent correr --pages 1
```

`correr` aplica los JSON de `/workspace/polymarket/investigaciones/`. Si el archivo sigue ahí y ya hay posición de ese evento, la siguiente corrida la descarta por correlación. Sacalo cuando ya quedó registrado.

Cuando el mercado cerró:

```bash
python -m polymarket_agent resolver
python -m polymarket_agent auditar
python -m polymarket_agent revisar
```

`auditar` muestra la calibración. La causa de cada pérdida (estimación, resolución, fuente o tamaño) la ponés vos: el programa no la inventa. `revisar` no edita los límites.

Telegram es opcional. En `/workspace/polymarket/telegram.env` (no se commitea):

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

```bash
python -m polymarket_agent correr --pages 1 --avisar
```

El token queda en esa carpeta. No pongas ahí una clave de wallet: no hace falta para esta fase y esta computadora puede compartir archivos con otros procesos.

La estimación automática es opt-in:

```bash
POLYMARKET_LLM_API_KEY=... POLYMARKET_LLM_MODEL=... \
  python -m polymarket_agent correr --pages 1 --llm --max-estimaciones 3
```

También acepta `XAI_API_KEY` o `OPENAI_API_KEY`, y `POLYMARKET_LLM_BASE_URL` si el endpoint no es el de siempre. Dejala apagada hasta que el registro resuelto muestre calibración. Un modelo que investiga solo es la parte que más fácil se sobreconfía.

Para repetir la corrida, una vez por hora alcanza al principio. Cada 10 minutos gasta cuota y, en mercados que cierran en semanas, no cambia el precio. Un ejemplo de cron, con la máquina despierta:

```bash
0 * * * * cd /workspace && python -m polymarket_agent correr --pages 1
```

Polymarket no está habilitado en todos los países. Esta herramienta solo lee datos públicos y simula. No salta bloqueos geográficos y no opera con dinero real. Si más adelante hubiera plata de por medio, el capital en la wallet es el techo de pérdida, y cada orden tendría que seguir siendo una decisión tuya. Ese camino no está implementado.
