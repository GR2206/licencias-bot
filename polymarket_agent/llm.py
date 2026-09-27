"""Estimación opcional con un modelo. Apagada salvo que se pida.

El modelo escribe evidencia y una probabilidad. No escribe el stake:
el tamaño lo calcula el motor con los límites fijos. Una divergencia
de más de 25 puntos que declare el modelo no cuenta como revisión humana.
"""

from __future__ import annotations

import json
import os
import re

import requests

from polymarket_agent.api import Market
from polymarket_agent.engine import Research, parse_research

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "investigacion.md")


class LLMError(RuntimeError):
    pass


def credentials() -> tuple[str, str, str]:
    api_key = os.environ.get("POLYMARKET_LLM_API_KEY") or os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if os.environ.get("POLYMARKET_LLM_BASE_URL"):
        base = os.environ["POLYMARKET_LLM_BASE_URL"].rstrip("/")
    elif os.environ.get("XAI_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        base = "https://api.x.ai/v1"
    else:
        base = "https://api.openai.com/v1"
    model = os.environ.get("POLYMARKET_LLM_MODEL") or ""
    if not api_key or not model:
        raise LLMError(
            "faltan POLYMARKET_LLM_API_KEY y POLYMARKET_LLM_MODEL "
            "(o XAI_API_KEY / OPENAI_API_KEY más el nombre del modelo)"
        )
    return api_key, base, model


def investigate(market: Market, session: requests.Session | None = None) -> Research:
    api_key, base, model = credentials()
    system = open(os.path.normpath(PROMPT_PATH), encoding="utf-8").read()
    user = (
        f"Mercado: {market.question}\n"
        f"Slug: {market.slug}\n"
        f"Categoría: {market.category}\n"
        f"Cierra: {market.end.isoformat() if market.end else 'sin fecha'}\n"
        f"Outcomes: {market.outcomes[0]} / {market.outcomes[1]}\n"
        f"Precio que muestra Gamma para el primero: {market.yes_price}\n\n"
        f"Condición de resolución publicada:\n{market.description}\n"
    )
    http = session or requests.Session()
    response = http.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=90,
    )
    if response.status_code != 200:
        raise LLMError(f"el modelo respondió {response.status_code}")
    content = response.json()["choices"][0]["message"]["content"]
    return parse_model_json(content)


def parse_model_json(content: str) -> Research:
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if not match:
        raise LLMError("el modelo no devolvió JSON")
    payload = json.loads(match.group(0))
    payload.pop("stake", None)
    payload.pop("edge", None)
    payload["slug"] = payload.get("slug")
    return parse_research(payload, human=False)
