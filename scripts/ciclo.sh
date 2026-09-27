#!/bin/bash
# Una pasada en simulación. No envía órdenes.
cd "$(dirname "$0")/.."
python3 -m polymarket_agent correr --pages 1 --limit 100
