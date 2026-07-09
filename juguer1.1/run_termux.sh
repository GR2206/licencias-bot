#!/data/data/com.termux/files/usr/bin/bash
# Juguer 1.1 — arranque en Android (Termux) sin cortar al bloquear pantalla
#
# Instalación Termux:
#   pkg install python git termux-api
#   pip install -r requirements.txt
#   cp config.example.py config.py  # editar credenciales
#   bash run_termux.sh

cd "$(dirname "$0")"

# Evita que Android mate el proceso al apagar pantalla
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock
  echo "Wake lock activado"
fi

# Reinicio automático si falla
while true; do
  echo "=== Juguer 1.1 $(date) ==="
  python bot.py
  echo "Bot salió. Reinicio en 15s..."
  sleep 15
done
