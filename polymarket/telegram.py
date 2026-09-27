"""Avisos opcionales. El token nunca vive en el prompt."""

from __future__ import annotations

import os
from pathlib import Path

import requests

from polymarket.config import DEFAULT_SECRETS


def load_telegram_secrets(path: Path = DEFAULT_SECRETS) -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "TELEGRAM_BOT_TOKEN" and not token:
                token = value.strip().strip('"')
            if key.strip() == "TELEGRAM_CHAT_ID" and not chat_id:
                chat_id = value.strip().strip('"')
    return token, chat_id


def format_summary(stats, bankroll: float, peak: float, rows: list[dict]) -> str:
    oportunidades = [row for row in rows if row.get("decision") in {"simulado", "candidato"}]
    lines = [
        f"{len([r for r in oportunidades if r.get('decision') == 'simulado'])} oportunidades simuladas · "
        f"{stats.revisados} mercados revisados",
        f"Candidatos (sin estimar): {stats.candidatos}",
        f"Descartes: {stats.descartados} · filtros {stats.motivos}",
        "",
    ]
    shown = 0
    for row in oportunidades:
        if row.get("decision") != "simulado":
            continue
        shown += 1
        lines.append(row.get("mercado") or row.get("slug") or "")
        lines.append(
            f"Precio: {row.get('precio_yes')}  Estimación: {row.get('prob_estimada')}  "
            f"Edge neto: {row.get('edge_neto')} pp"
        )
        lines.append(
            f"Riesgo propuesto: {row.get('fraccion')} del bankroll (${row.get('stake_simulado')})"
        )
        lines.append(f"Razón: {row.get('razonamiento')}")
        lines.append("")
    lines.append(f"Bankroll simulado: ${bankroll:.2f} (máximo: ${peak:.2f})")
    lines.append("No se ejecutó ninguna operación sin aprobación.")
    return "\n".join(lines)


def send_message(text: str, path: Path = DEFAULT_SECRETS) -> bool:
    token, chat_id = load_telegram_secrets(path)
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={"chat_id": chat_id, "text": text[:3900]},
        timeout=20,
    )
    response.raise_for_status()
    return True
