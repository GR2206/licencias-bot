"""Un ciclo: descubrir → filtrar → (opcional) puntuar → registrar. Nunca ejecuta."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from uuid import uuid4

from polymarket.clients import (
    PublicClient,
    event_meta,
    outcome_prices,
    parse_json_list,
    price_from_map,
    published_fee_rate,
    token_ids,
)
from polymarket.config import DEFAULT_LEDGER, LIMITS, RiskLimits, assert_limits_intact
from polymarket.filters import (
    reject_book,
    reject_min_order,
    reject_price,
    reject_resolution,
    reject_spread,
    top_book_depth,
)
from polymarket.ledger import (
    append_row,
    as_float,
    current_bankroll,
    dumps_sources,
    fmt_num,
    load_rows,
    now_utc,
)
from polymarket.risk import correlated_open, drawdown_halt, growing_edge_trap
from polymarket.rules import (
    choose_side,
    days_until,
    edge_bruto_pp,
    edge_neto_pp,
    fee_rate_for,
    infer_category,
    is_fast_market,
    position_fraction,
    round_probability,
    valid_sources,
)


@dataclass
class CycleStats:
    revisados: int = 0
    descartados: int = 0
    candidatos: int = 0
    simulados: int = 0
    motivos: dict = field(default_factory=dict)
    oportunidades: list = field(default_factory=list)

    def bump(self, motivo: str) -> None:
        self.motivos[motivo] = self.motivos.get(motivo, 0) + 1


def _gamma_spread(market: dict) -> Optional[float]:
    if market.get("spread") is not None:
        try:
            return float(market["spread"])
        except (TypeError, ValueError):
            return None
    bid, ask = market.get("bestBid"), market.get("bestAsk")
    try:
        if bid is not None and ask is not None:
            return float(ask) - float(bid)
    except (TypeError, ValueError):
        return None
    return None


def _yes_price(market: dict, price_map: dict) -> Optional[float]:
    ids = token_ids(market)
    if ids:
        priced = price_from_map(price_map, ids[0], "BUY")
        if priced is not None:
            return priced
    if market.get("bestAsk") is not None:
        try:
            return float(market["bestAsk"])
        except (TypeError, ValueError):
            pass
    prices = outcome_prices(market)
    if prices:
        return prices[0]
    return None


def _no_price(market: dict, price_map: dict) -> Optional[float]:
    ids = token_ids(market)
    if len(ids) > 1:
        priced = price_from_map(price_map, ids[1], "BUY")
        if priced is not None:
            return priced
    yes = _yes_price(market, price_map)
    if yes is None:
        return None
    return max(0.0, min(1.0, 1.0 - yes))


def _end_date(market: dict) -> Optional[str]:
    return market.get("endDate") or market.get("endDateIso") or market.get("end_date_iso")


def market_label(market: dict, category: str) -> str:
    slug = market.get("slug") or ""
    question = (market.get("question") or "").replace("\n", " ").strip()
    return f"{slug} | {question} | {category}"


def base_row(market: dict, category: str, event: dict, run_id: str, bankroll: float) -> dict:
    return {
        "timestamp_utc": now_utc(),
        "mercado": market_label(market, category),
        "precio_yes": "",
        "prob_estimada": "",
        "edge_bruto": "",
        "edge_neto": "",
        "fraccion": "",
        "stake_simulado": "",
        "bankroll_pre": fmt_num(bankroll),
        "resultado": "",
        "pnl": "",
        "bankroll_post": fmt_num(bankroll),
        "razonamiento": "",
        "fuentes": "",
        "decision": "descartado",
        "motivo": "",
        "categoria": category,
        "slug": market.get("slug") or "",
        "event_id": event.get("event_id") or "",
        "lado": "",
        "confianza": "",
        "run_id": run_id,
        "condition_id": market.get("conditionId") or "",
    }


def record(row: dict, path: Path, persist: bool) -> dict:
    if persist:
        return append_row(row, path)
    return row


def scan(
    *,
    limit: int = 200,
    ledger_path: Path = DEFAULT_LEDGER,
    persist: bool = True,
    client: Optional[PublicClient] = None,
    limits: RiskLimits = LIMITS,
    bankroll: Optional[float] = None,
    fetch_books: bool = True,
    order: str = "volume24hr",
) -> tuple[CycleStats, list[dict], float]:
    """Descubre mercados, aplica filtros duros y deja candidatos para investigar."""
    assert_limits_intact(limits)
    client = client or PublicClient()
    rows = load_rows(ledger_path) if persist else []
    cash = bankroll if bankroll is not None else current_bankroll(rows, limits.initial_bankroll)
    halted, halt_reason = drawdown_halt(rows, limits.initial_bankroll, limits)
    stats = CycleStats()
    run_id = uuid4().hex[:10]
    written: list[dict] = []

    markets = list(client.iter_open_markets(limit=limit, order=order))
    ids = []
    for market in markets:
        ids.extend(token_ids(market)[:2])
    price_map = client.prices(ids) if ids else {}

    for market in markets:
        stats.revisados += 1
        event = event_meta(market)
        category = infer_category(
            fee_type=market.get("feeType"),
            tags=event.get("tags"),
            slug=market.get("slug"),
            question=market.get("question"),
        )
        row = base_row(market, category, event, run_id, cash)
        yes = _yes_price(market, price_map)
        no = _no_price(market, price_map)
        if yes is not None:
            row["precio_yes"] = fmt_num(yes, 4)

        reason = reject_resolution(days_until(_end_date(market)), limits)
        if reason:
            row["motivo"] = reason
            stats.descartados += 1
            stats.bump("resolucion")
            written.append(record(row, ledger_path, persist))
            continue

        reason = reject_price(yes, limits)
        if reason:
            row["motivo"] = reason
            stats.descartados += 1
            stats.bump("precio")
            written.append(record(row, ledger_path, persist))
            continue

        spread = _gamma_spread(market)
        reason = reject_spread(spread, limits)
        if reason:
            row["motivo"] = reason
            stats.descartados += 1
            stats.bump("spread")
            written.append(record(row, ledger_path, persist))
            continue

        max_stake = limits.position_cap * cash
        proxy_shares = max_stake / yes if yes else 0.0
        depth = None
        min_order = market.get("orderMinSize")
        if fetch_books and token_ids(market):
            try:
                book = client.book(token_ids(market)[0])
            except Exception as exc:  # noqa: BLE001 — un libro fallido no tumba la corrida
                row["motivo"] = f"no se pudo leer el libro: {exc}"
                stats.descartados += 1
                stats.bump("libro")
                written.append(record(row, ledger_path, persist))
                continue
            asks = book.get("asks") or []
            best_ask = yes
            if asks:
                try:
                    best_ask = min(float(level["price"]) for level in asks)
                except (TypeError, ValueError, KeyError):
                    best_ask = yes
            depth = top_book_depth(asks, best_price=best_ask, side="BUY")
            min_order = book.get("min_order_size") or min_order
            try:
                spread = float(client.spread(token_ids(market)[0]))
            except Exception:
                pass
            reason = reject_spread(spread, limits)
            if reason:
                row["motivo"] = reason
                stats.descartados += 1
                stats.bump("spread")
                written.append(record(row, ledger_path, persist))
                continue

        if depth is None:
            try:
                depth = float(market.get("liquidityNum") or market.get("liquidity") or 0) / max(yes, 0.01)
            except (TypeError, ValueError):
                depth = 0.0
        reason = reject_book(depth, proxy_shares, limits)
        if reason:
            row["motivo"] = reason
            stats.descartados += 1
            stats.bump("libro")
            written.append(record(row, ledger_path, persist))
            continue

        if halted:
            row["decision"] = "candidato"
            row["motivo"] = halt_reason
            row["razonamiento"] = "pasó filtros duros, pero el freno de drawdown está activo"
            stats.candidatos += 1
            written.append(record(row, ledger_path, persist))
            continue

        row["decision"] = "candidato"
        row["motivo"] = "pasó filtros duros; falta evidencia y probabilidad propia"
        row["razonamiento"] = (
            "no se estima probabilidad sin evidencia. "
            "usar `propose` después de investigar."
        )
        stats.candidatos += 1
        written.append(record(row, ledger_path, persist))

    return stats, written, cash


def evaluate_estimate(
    estimate: dict,
    market: dict,
    *,
    bankroll: float,
    rows: list[dict],
    price_map: Optional[dict] = None,
    book: Optional[dict] = None,
    spread: Optional[float] = None,
    limits: RiskLimits = LIMITS,
    now=None,
) -> dict:
    """Aplica estimación + edge + Kelly. Devuelve una fila, no manda órdenes."""
    assert_limits_intact(limits)
    price_map = price_map or {}
    event = event_meta(market)
    category = estimate.get("categoria") or infer_category(
        fee_type=market.get("feeType"),
        tags=event.get("tags"),
        slug=market.get("slug"),
        question=market.get("question"),
    )
    row = base_row(market, category, event, estimate.get("run_id") or uuid4().hex[:10], bankroll)
    yes = _yes_price(market, price_map)
    no = _no_price(market, price_map)
    if yes is not None:
        row["precio_yes"] = fmt_num(yes, 4)

    days = days_until(_end_date(market), now=now)
    for reason in (
        reject_resolution(days, limits),
        reject_price(yes, limits),
        reject_spread(spread if spread is not None else _gamma_spread(market), limits),
    ):
        if reason:
            row["motivo"] = reason
            return row

    resolution = (estimate.get("condicion_resolucion") or estimate.get("resolucion") or "").strip()
    if not resolution:
        row["motivo"] = "no está escrita la condición de resolución en una línea"
        return row

    against = estimate.get("en_contra") or []
    favor = estimate.get("a_favor") or []
    if len(against) < 2:
        row["motivo"] = "sin 2 razones en contra no se entendió el mercado"
        return row
    if len(favor) < 2:
        row["motivo"] = "faltan 2 razones a favor"
        return row
    if not (estimate.get("hipotesis_error_mercado") or "").strip():
        row["motivo"] = "sin hipótesis de por qué el mercado está equivocado no hay edge"
        return row

    fast = is_fast_market(category, days)
    sources = valid_sources(estimate.get("fuentes") or [], fast=fast, now=now, limits=limits)
    if len(sources) < limits.min_sources:
        row["motivo"] = (
            "menos de 2 fuentes independientes con fecha"
            + (" (y < 48 h en mercado rápido)" if fast else "")
        )
        row["fuentes"] = dumps_sources(estimate.get("fuentes") or [])
        return row

    try:
        raw_p = float(estimate["probabilidad"])
    except (KeyError, TypeError, ValueError):
        row["motivo"] = "probabilidad ausente o inválida"
        return row
    p = round_probability(raw_p, limits.prob_step)
    confianza = (estimate.get("confianza") or "media").strip().lower()
    row["confianza"] = confianza
    row["prob_estimada"] = fmt_num(p, 2)
    row["fuentes"] = dumps_sources(sources)

    side = (estimate.get("lado") or "").upper() or choose_side(p, yes, no)
    if side == "NO":
        cost = no if no is not None else max(0.0, 1.0 - yes)
        p_side = 1.0 - p
    else:
        side = "YES"
        cost = yes
        p_side = p
    row["lado"] = side

    fee_rate = fee_rate_for(category, published_fee_rate(market))
    used_spread = spread if spread is not None else _gamma_spread(market) or 0.0
    bruto = edge_bruto_pp(p_side, cost)
    neto = edge_neto_pp(p_side, cost, used_spread, fee_rate)
    row["edge_bruto"] = fmt_num(bruto, 2)
    row["edge_neto"] = fmt_num(neto, 2)
    row["razonamiento"] = (estimate.get("razonamiento") or "").strip() or (
        f"{resolution} | a favor: {favor[0]} | contra: {against[0]}"
    )

    if abs(bruto) > limits.alarm_edge_pp and not estimate.get("revise_ok"):
        row["motivo"] = (
            f"desvío {bruto:.1f} pp > {limits.alarm_edge_pp:.0f} pp; "
            "por defecto se asume error del modelo"
        )
        return row

    if confianza == "baja":
        row["decision"] = "registrado"
        row["motivo"] = "confianza baja: se registra, no se opera"
        return row

    if neto < limits.edge_min_pp:
        row["motivo"] = f"edge neto {neto:.2f} pp < {limits.edge_min_pp:.0f} pp"
        return row

    halted, halt_reason = drawdown_halt(rows, limits.initial_bankroll, limits)
    if halted:
        row["motivo"] = halt_reason
        return row

    correlated, why = correlated_open(row, rows)
    if correlated:
        row["motivo"] = why
        return row

    trapped, why = growing_edge_trap(row, rows)
    if trapped:
        row["motivo"] = why
        return row

    frac = position_fraction(p_side, cost, limits)
    stake = frac * bankroll
    if stake <= 0:
        row["motivo"] = "fracción Kelly no positiva"
        return row
    min_order = None
    if book:
        min_order = book.get("min_order_size")
        asks = book.get("asks") or []
        if asks:
            try:
                best = min(float(level["price"]) for level in asks)
            except (TypeError, ValueError, KeyError):
                best = cost
            depth = top_book_depth(asks, best_price=best, side="BUY")
            reason = reject_book(depth, stake / cost, limits)
            if reason:
                row["motivo"] = reason
                return row
    min_order = min_order or market.get("orderMinSize")
    reason = reject_min_order(stake, min_order, cost)
    if reason:
        row["motivo"] = reason
        return row

    row["fraccion"] = fmt_num(frac, 4)
    row["stake_simulado"] = fmt_num(stake, 2)
    row["decision"] = "simulado"
    row["motivo"] = "paper trade: no se envió ninguna orden"
    row["bankroll_post"] = fmt_num(bankroll - stake)
    return row


def paper_pnl(side: str, stake: float, cost: float, won: bool, fee_rate: float) -> float:
    if cost <= 0:
        return 0.0
    shares = stake / cost
    fee = shares * fee_rate * cost * (1.0 - cost)
    if won:
        return shares - stake - fee
    return -stake - fee


def winner_from_market(market: dict) -> Optional[str]:
    if not market.get("closed"):
        return None
    outcomes = [str(x).strip().upper() for x in parse_json_list(market.get("outcomes"))]
    prices = outcome_prices(market)
    if not outcomes or not prices or len(outcomes) != len(prices):
        return None
    if max(prices) <= 0:
        return None
    idx = max(range(len(prices)), key=lambda i: prices[i])
    label = outcomes[idx]
    if label in {"YES", "Y", "SI", "SÍ"}:
        return "YES"
    if label in {"NO", "N"}:
        return "NO"
    return label
