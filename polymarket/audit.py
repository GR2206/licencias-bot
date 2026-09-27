"""Auditoría de calibración. Números, no relatos."""

from __future__ import annotations

from collections import defaultdict

from polymarket.ledger import as_float


BUCKETS = (
    (0.50, 0.60, "50-60%"),
    (0.60, 0.70, "60-70%"),
    (0.70, 0.80, "70-80%"),
    (0.80, 1.01, "80%+"),
)


def resolved_trades(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        if row.get("decision") != "simulado":
            continue
        if row.get("resultado") not in {"YES", "NO"}:
            continue
        p = as_float(row.get("prob_estimada"))
        if p is None:
            continue
        out.append(row)
    return out


def brier_score(rows: list[dict]) -> float | None:
    trades = resolved_trades(rows)
    if not trades:
        return None
    total = 0.0
    for row in trades:
        p = as_float(row["prob_estimada"])
        won_yes = 1.0 if row["resultado"] == "YES" else 0.0
        # Si apostó NO, la p guardada sigue siendo P(YES).
        total += (p - won_yes) ** 2
    return total / len(trades)


def calibration(rows: list[dict]) -> list[dict]:
    trades = resolved_trades(rows)
    grouped = {label: [] for _, _, label in BUCKETS}
    for row in trades:
        p = as_float(row["prob_estimada"])
        for low, high, label in BUCKETS:
            if low <= p < high:
                grouped[label].append(row)
                break
    report = []
    for _, _, label in BUCKETS:
        bucket = grouped[label]
        if not bucket:
            report.append(
                {
                    "rango": label,
                    "n": 0,
                    "aciertos_yes": None,
                    "sobreconfianza": None,
                    "nota": "muestra insuficiente",
                }
            )
            continue
        hits = sum(1 for row in bucket if row["resultado"] == "YES")
        rate = hits / len(bucket)
        mid = {
            "50-60%": 0.55,
            "60-70%": 0.65,
            "70-80%": 0.75,
            "80%+": 0.85,
        }[label]
        report.append(
            {
                "rango": label,
                "n": len(bucket),
                "aciertos_yes": round(rate, 3),
                "esperado": mid,
                "sobreconfianza": round(mid - rate, 3),
                "nota": "muestra insuficiente" if len(bucket) < 10 else "ok",
            }
        )
    return report


def worst_losses(rows: list[dict], n: int = 5) -> list[dict]:
    trades = [row for row in resolved_trades(rows) if as_float(row.get("pnl"), 0) < 0]
    trades.sort(key=lambda row: as_float(row.get("pnl"), 0))
    return trades[:n]


def by_category(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in resolved_trades(rows):
        groups[row.get("categoria") or "other"].append(row)
    report = []
    for category, bucket in sorted(groups.items()):
        pnls = [as_float(row.get("pnl"), 0.0) for row in bucket]
        hits = sum(1 for row in bucket if as_float(row.get("pnl"), 0) > 0)
        report.append(
            {
                "categoria": category,
                "n": len(bucket),
                "winrate": round(hits / len(bucket), 3) if bucket else None,
                "pnl": round(sum(pnls), 4),
                "nota": "muestra insuficiente" if len(bucket) < 10 else "ok",
            }
        )
    return report


def bias_notes(rows: list[dict]) -> list[str]:
    trades = resolved_trades(rows)
    notes = []
    if not trades:
        return ["no hay operaciones resueltas"]
    diffs = []
    for row in trades:
        p = as_float(row["prob_estimada"])
        c = as_float(row["precio_yes"])
        if p is not None and c is not None:
            diffs.append(p - c)
    if diffs and sum(1 for d in diffs if d > 0) / len(diffs) >= 0.8:
        notes.append("sesgo: el agente estima por encima del mercado en casi todos los casos")
    if diffs and sum(1 for d in diffs if d < 0) / len(diffs) >= 0.8:
        notes.append("sesgo: el agente estima por debajo del mercado en casi todos los casos")
    return notes or ["sin patrón obvio en la muestra"]


def can_propose_change(resolved_count: int) -> str:
    if resolved_count < 20:
        return "no hay evidencia suficiente para cambiar nada"
    return ""


def render_audit(rows: list[dict]) -> str:
    trades = resolved_trades(rows)
    lines = [
        "AUDITORÍA DE DECISIONES",
        f"operaciones resueltas: {len(trades)}",
        f"brier: {brier_score(rows)}",
        "",
        "1. CALIBRACIÓN",
    ]
    for bucket in calibration(rows):
        lines.append(
            f"  {bucket['rango']}: n={bucket['n']} "
            f"real={bucket['aciertos_yes']} "
            f"sobreconfianza={bucket['sobreconfianza']} "
            f"[{bucket['nota']}]"
        )
    lines.append("")
    lines.append("2. PÉRDIDAS")
    losses = worst_losses(rows)
    if not losses:
        lines.append("  ninguna pérdida resuelta")
    for row in losses:
        lines.append(
            f"  {row.get('slug')} pnl={row.get('pnl')} "
            f"p={row.get('prob_estimada')} c={row.get('precio_yes')} "
            f"| {row.get('razonamiento')}"
        )
    lines.append("")
    lines.append("3. CATEGORÍAS")
    cats = by_category(rows)
    if not cats:
        lines.append("  muestra insuficiente")
    for row in cats:
        lines.append(
            f"  {row['categoria']}: n={row['n']} winrate={row['winrate']} "
            f"pnl={row['pnl']} [{row['nota']}]"
        )
    lines.append("")
    lines.append("4. SESGOS")
    for note in bias_notes(rows):
        lines.append(f"  {note}")
    lines.append("")
    gate = can_propose_change(len(trades))
    lines.append("REVISIÓN DE ESTRATEGIA")
    lines.append(f"  {gate or 'hay ≥20 resueltas: se puede proponer UN cambio, solo más conservador'}")
    return "\n".join(lines)
