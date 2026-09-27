"""Una corrida: filtrar, y solo entonces investigar lo que sobrevivió."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from polymarket_agent.api import Book, HttpMarketData, Market, MarketDataError, infer_resolution
from polymarket_agent.engine import Decision, Research, evaluate, parse_research, preliminary_reason, screen_market
from polymarket_agent.ledger import Ledger


@dataclass
class Report:
    reviewed: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    opportunities: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    executed: bool = False

    def add(self, motivo: str) -> None:
        self.counts[motivo] = self.counts.get(motivo, 0) + 1


def load_research_file(path: Path, *, human: bool) -> Research:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return parse_research(payload, human=human)


def run_scan(
    ledger: Ledger,
    data,
    pages: int = 1,
    limit: int = 100,
    research_by_slug: dict[str, Research] | None = None,
    now: datetime | None = None,
    estimator=None,
    max_estimates: int = 3,
) -> Report:
    now = now or datetime.now(timezone.utc)
    research_by_slug = research_by_slug or {}
    report = Report()
    markets = data.list_open(limit=limit, pages=pages)
    report.reviewed = len(markets)
    survivors: list[Market] = []
    needs_spread: list[Market] = []
    for market in markets:
        reason = preliminary_reason(market, now)
        if reason:
            _log_discard(ledger, market, reason, now)
            report.add(reason)
            continue
        needs_spread.append(market)

    spreads = data.spreads([market.token_ids[0] for market in needs_spread]) if needs_spread else {}
    edges: dict[str, float] = {}
    for market in needs_spread:
        spread = spreads.get(market.token_ids[0])
        reason = screen_market(market, spread, now=now)
        if reason:
            decision = Decision(decision="descartar", motivo=reason, precio_yes=market.yes_price, spread=spread, razonamiento=reason)
            ledger.append(market, decision, now)
            report.add(reason)
            continue
        survivors.append(market)

    snap = ledger.snapshot()
    portfolio = snap.portfolio(ledger.load_state().last_edge)
    estimates = 0
    for market in survivors:
        research = research_by_slug.get(market.slug)
        if research is None and estimator is not None and estimates < max_estimates:
            estimates += 1
            try:
                research = estimator(market)
            except Exception as exc:
                decision = Decision(
                    decision="descartar",
                    motivo="llm_error",
                    precio_yes=market.yes_price,
                    razonamiento=str(exc)[:240],
                )
                ledger.append(market, decision, now)
                report.add("llm_error")
                report.errors.append(f"{market.slug}: {exc}")
                continue
        if research is None:
            decision = Decision(
                decision="pendiente",
                motivo="pendiente_investigacion",
                precio_yes=market.yes_price,
                spread=spreads.get(market.token_ids[0]),
                razonamiento="pasó los filtros duros; falta evidencia antes de estimar",
            )
            ledger.append(market, decision, now)
            report.add("pendiente_investigacion")
            continue
        decision = _decide_market(data, market, research, portfolio, now, report)
        row = ledger.append(market, decision, now)
        report.add(decision.motivo)
        if decision.edge_neto is not None:
            edges[market.slug] = decision.edge_neto
        if decision.decision == "simular":
            portfolio = ledger.snapshot().portfolio(portfolio.last_edge)
            report.opportunities.append(_opportunity(market, row))
    ledger.remember_edges(edges)
    return report


def decide_slug(ledger: Ledger, data: HttpMarketData, research: Research, slug: str) -> Decision:
    market = data.by_slug(slug)
    now = datetime.now(timezone.utc)
    reason = screen_market(market, spread=None, now=now)
    if reason and reason != "sin_spread":
        decision = Decision(decision="descartar", motivo=reason, precio_yes=market.yes_price, razonamiento=reason)
        ledger.append(market, decision, now)
        return decision
    portfolio = ledger.snapshot().portfolio(ledger.load_state().last_edge)
    report = Report()
    decision = _decide_market(data, market, research, portfolio, now, report)
    ledger.append(market, decision, now)
    if decision.edge_neto is not None:
        ledger.remember_edges({market.slug: decision.edge_neto})
    return decision


def _decide_market(data, market: Market, research: Research, portfolio, now: datetime, report: Report) -> Decision:
    try:
        yes_book = data.book(market.token_ids[0])
        no_book = data.book(market.token_ids[1])
    except MarketDataError as exc:
        report.errors.append(f"{market.slug}: {exc}")
        return Decision(decision="descartar", motivo="sin_libro", precio_yes=market.yes_price, razonamiento=str(exc))
    return evaluate(market, yes_book, no_book, research, portfolio, now)


def settle_open(ledger: Ledger, data: HttpMarketData) -> list[dict[str, str]]:
    settled = []
    seen = set()
    for row in ledger.rows():
        slug = row.get("slug")
        if row.get("decision") != "simular" or row.get("resultado") or not slug or slug in seen:
            continue
        seen.add(slug)
        try:
            market = data.by_slug(slug)
        except MarketDataError as exc:
            settled.append({"slug": slug, "estado": f"error: {exc}"})
            continue
        result = infer_resolution(market)
        if result is None:
            settled.append({"slug": slug, "estado": "sigue_abierto"})
            continue
        updated = ledger.settle(slug, result)
        settled.append({"slug": slug, "estado": "resuelto", "resultado": result, "pnl": updated.get("pnl") if updated else None})
    return settled


def _log_discard(ledger: Ledger, market: Market, motivo: str, now: datetime) -> None:
    ledger.append(
        market,
        Decision(decision="descartar", motivo=motivo, precio_yes=market.yes_price, razonamiento=motivo),
        now,
    )


def _opportunity(market: Market, row: dict[str, str]) -> dict:
    return {
        "mercado": market.question,
        "slug": market.slug,
        "precio": row.get("precio_yes"),
        "estimacion": row.get("prob_estimada"),
        "edge_neto": row.get("edge_neto"),
        "fraccion": row.get("fraccion"),
        "stake": row.get("stake_simulado"),
        "razon": row.get("razonamiento"),
    }


def render_summary(ledger: Ledger, report: Report | None = None) -> str:
    snap = ledger.snapshot()
    lines = []
    if report is not None:
        passed = report.counts.get("edge_neto", 0)
        lines.append(f"{passed} oportunidades simuladas · {report.reviewed} mercados revisados")
        for item in report.opportunities:
            lines.append(item["mercado"])
            lines.append(
                f"Precio: {item['precio']} Estimación: {item['estimacion']} Edge neto: {item['edge_neto']} pp"
            )
            lines.append(f"Riesgo propuesto: fracción {item['fraccion']} (stake simulado {item['stake']})")
            lines.append(f"Razón: {item['razon']}")
        interesting = {key: value for key, value in sorted(report.counts.items()) if key != "edge_neto"}
        if interesting:
            lines.append("Descartes y pendientes: " + ", ".join(f"{key}={value}" for key, value in interesting.items()))
    lines.append(
        f"Bankroll simulado (equity): {snap.equity:.2f} (efectivo {snap.cash:.2f}, comprometido {snap.reserved:.2f}, máximo {snap.peak_equity:.2f})"
    )
    if snap.halted:
        lines.append("Freno activo: el equity cayó 25% desde el máximo. No hay operaciones nuevas.")
    lines.append("No se ejecutó ninguna operación.")
    return "\n".join(lines)


def load_research_dir(directory: Path, *, human: bool) -> dict[str, Research]:
    found: dict[str, Research] = {}
    if not directory.exists():
        return found
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        slug = str(payload.get("slug") or path.stem)
        found[slug] = parse_research(payload, human=human)
    return found


def notify_telegram(text: str, home: Path) -> str:
    import os

    import requests

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    secret_file = home / "telegram.env"
    if secret_file.exists():
        for line in secret_file.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.strip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "TELEGRAM_BOT_TOKEN" and not token:
                token = value.strip()
            if key.strip() == "TELEGRAM_CHAT_ID" and not chat_id:
                chat_id = value.strip()
    if not token or not chat_id:
        return "sin telegram configurado"
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text[:4000]},
        timeout=20,
    )
    if response.status_code != 200:
        return f"telegram respondió {response.status_code}"
    return "telegram enviado"


class ScriptedData:
    """Doble de pruebas. No habla con la red."""

    def __init__(self, markets: list[Market], spreads: dict[str, float], books: dict[str, Book]):
        self._markets = markets
        self._spreads = spreads
        self._books = books

    def list_open(self, limit: int = 100, pages: int = 1) -> list[Market]:
        return list(self._markets)

    def by_slug(self, slug: str) -> Market:
        for market in self._markets:
            if market.slug == slug:
                return market
        raise MarketDataError(slug)

    def spreads(self, token_ids: list[str]) -> dict[str, float]:
        return {token_id: self._spreads[token_id] for token_id in token_ids if token_id in self._spreads}

    def book(self, token_id: str) -> Book:
        if token_id not in self._books:
            raise MarketDataError(token_id)
        return self._books[token_id]
