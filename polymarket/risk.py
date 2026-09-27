"""Reglas de riesgo que el agente no puede reescribir."""

from __future__ import annotations

from polymarket.config import LIMITS, RiskLimits, assert_limits_intact
from polymarket.ledger import as_float, current_bankroll, open_positions, peak_bankroll


def drawdown_halt(
    rows: list[dict],
    initial: float,
    limits: RiskLimits = LIMITS,
) -> tuple[bool, str]:
    assert_limits_intact(limits)
    bankroll = current_bankroll(rows, initial)
    peak = peak_bankroll(rows, initial)
    if peak <= 0:
        return False, ""
    drop = (peak - bankroll) / peak
    if drop >= limits.drawdown_halt:
        return True, (
            f"drawdown {drop:.1%} desde el máximo {peak:.2f}; "
            "se paralizan operaciones nuevas"
        )
    return False, ""


def same_event(a: dict, b: dict) -> bool:
    if a.get("event_id") and a.get("event_id") == b.get("event_id"):
        return True
    if a.get("slug") and a.get("slug") == b.get("slug"):
        return True
    return False


def correlated_open(candidate: dict, rows: list[dict]) -> tuple[bool, str]:
    for opened in open_positions(rows):
        if same_event(candidate, opened):
            return True, (
                f"correlación con posición abierta {opened.get('slug') or opened.get('mercado')}"
            )
    return False, ""


def growing_edge_trap(candidate: dict, rows: list[dict]) -> tuple[bool, str]:
    slug = candidate.get("slug")
    if not slug:
        return False, ""
    previous = [
        row
        for row in rows
        if row.get("slug") == slug
        and row.get("decision") in {"simulado", "candidato", "propuesto"}
        and as_float(row.get("edge_neto")) is not None
    ]
    if len(previous) < 2:
        return False, ""
    last_two = previous[-2:]
    e1 = as_float(last_two[0]["edge_neto"])
    e2 = as_float(last_two[1]["edge_neto"])
    now = as_float(candidate.get("edge_neto"))
    if None in (e1, e2, now):
        return False, ""
    if e2 > e1 and now > e2:
        return True, (
            "el mismo mercado viene con edge creciente en corridas seguidas; "
            "sesgo del modelo, no se acumula"
        )
    return False, ""


def reject_strategy_change(proposal: dict) -> str:
    """Solo se puede endurecer. Relajar es un error, no una mejora."""
    text = " ".join(str(v) for v in proposal.values()).lower()
    if any(word in text for word in ("bajar el 8", "bajar umbral", "bajar el umbral", "menos de 8", "7%", "6 pp")):
        return "no se puede bajar el umbral del 8%"
    if any(word in text for word in ("subir el 6", "techo al 7", "techo al 8", "más del 6")):
        return "no se puede subir el techo del 6%"
    if any(word in text for word in ("kelly completo", "1/2 kelly", "media kelly", "subir kelly")):
        return "no se puede aumentar el multiplicador de Kelly"
    if "operar más" in text or "más operaciones" in text:
        return "si el cambio es operar más, asumí que está mal"
    return ""
