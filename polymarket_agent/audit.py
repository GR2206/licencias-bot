"""Auditoría de calibración. No propone operar más y no toca los límites."""

from __future__ import annotations

from polymarket_agent.ledger import Ledger
from polymarket_agent.risk import LIMITS

BUCKETS = ((0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 1.01))


def _resolved(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    picked = []
    for row in rows:
        if row.get("resultado") in {"YES", "NO"} and row.get("prob_estimada"):
            picked.append(row)
    return picked


def auditar(ledger: Ledger, limit: int = 30) -> dict:
    resolved = _resolved(ledger.rows())[-limit:]
    buckets = []
    for low, high in BUCKETS:
        group = []
        for row in resolved:
            probability = float(row["prob_estimada"])
            if low <= probability < high:
                group.append(row)
        hits = sum(1 for row in group if row["resultado"] == "YES")
        buckets.append(
            {
                "rango": f"{int(low * 100)}-{int(min(high, 1) * 100)}%",
                "n": len(group),
                "se_cumplio": hits,
                "frecuencia": (hits / len(group)) if group else None,
            }
        )

    simulated = [row for row in resolved if row.get("decision") == "simular"]
    worst = sorted(simulated, key=lambda row: float(row.get("pnl") or 0))[:5]
    losses = [
        {
            "slug": row.get("slug"),
            "pnl": float(row.get("pnl") or 0),
            "prob_estimada": float(row["prob_estimada"]),
            "precio_yes": float(row["precio_yes"] or 0),
            "categoria": row.get("categoria"),
            "razonamiento": row.get("razonamiento"),
            "fuentes": row.get("fuentes"),
            "clasificacion": "la tiene que poner una persona: estimacion, resolucion, fuente o tamano",
        }
        for row in worst
        if float(row.get("pnl") or 0) < 0
    ]

    by_category: dict[str, list[dict[str, str]]] = {}
    for row in simulated:
        by_category.setdefault(row.get("categoria") or "sin_categoria", []).append(row)
    categories = []
    for name, group in sorted(by_category.items()):
        pnl = sum(float(row.get("pnl") or 0) for row in group)
        entry = {"categoria": name, "resueltas": len(group), "pnl": pnl}
        if len(group) < 10:
            entry["lectura"] = "muestra insuficiente"
        categories.append(entry)

    gaps = []
    for row in simulated:
        gaps.append(float(row["prob_estimada"]) - float(row["precio_yes"] or 0))
    bias = {
        "operaciones_resueltas": len(simulated),
        "estimacion_media_menos_precio": (sum(gaps) / len(gaps)) if gaps else None,
        "fraccion_por_encima_del_precio": (sum(1 for gap in gaps if gap > 0) / len(gaps)) if gaps else None,
    }
    return {
        "decisiones_resueltas_revisadas": len(resolved),
        "calibracion": buckets,
        "peores_perdidas": losses,
        "categorias": categories,
        "sesgo": bias,
        "nota": "Sin propuesta de cambios. Los números van primero.",
    }


def revisar(ledger: Ledger) -> dict:
    resolved_trades = [
        row
        for row in ledger.rows()
        if row.get("decision") == "simular" and row.get("resultado") in {"YES", "NO"}
    ]
    if len(resolved_trades) < 20:
        return {
            "cambio": "no hay evidencia suficiente para cambiar nada",
            "operaciones_resueltas": len(resolved_trades),
            "aplicado": False,
        }
    return {
        "cambio": "no hay evidencia suficiente para cambiar nada",
        "operaciones_resueltas": len(resolved_trades),
        "aplicado": False,
        "direccion_permitida": "solo más conservadora, y solo si una persona edita risk.py y lo aprueba",
        "prohibido": {
            "bajar_umbral": LIMITS.min_edge_pp,
            "subir_techo": LIMITS.max_position_fraction,
            "subir_kelly": LIMITS.kelly_multiplier,
        },
        "por_que_operar_mas_esta_mal": (
            "con una muestra chica, pedir mejoras empuja a operar más. "
            "Ese cambio se descarta aunque la racha se vea bien."
        ),
    }
