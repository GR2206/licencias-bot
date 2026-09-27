"""Matemática de la guía: filtros, edge neto, Kelly fraccional y tamaño.

La probabilidad entra ya investigada. Este módulo no inventa una opinión
y no puede relajar los límites de `risk.LIMITS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlparse

from polymarket_agent.api import Book, Market
from polymarket_agent.risk import LIMITS, RiskLimits


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    published: datetime
    kind: str


@dataclass(frozen=True)
class Research:
    resolution_line: str
    resolution_clear: bool
    probability: float
    confidence: str
    reasons_for: tuple[str, str]
    reasons_against: tuple[str, str]
    counterparty_hypothesis: str
    sources: tuple[Source, ...]
    fast_market: bool | None
    reviewed_large_divergence: bool
    # El modelo no puede auto-aprobar una divergencia enorme.
    divergence_attested_by_human: bool = False


@dataclass(frozen=True)
class SideQuote:
    name: str
    code: str
    bid: float
    ask: float
    depth: float
    min_order_size: float


@dataclass
class Decision:
    decision: str
    motivo: str
    precio_yes: float | None = None
    prob: float | None = None
    edge_bruto: float | None = None
    edge_neto: float | None = None
    fraccion: float = 0.0
    stake: float = 0.0
    lado: str = ""
    precio_entrada: float | None = None
    spread: float | None = None
    razonamiento: str = ""
    fuentes: str = ""
    confianza: str = ""


@dataclass(frozen=True)
class Portfolio:
    cash: float
    equity: float
    peak_equity: float
    open_event_ids: frozenset[str]
    open_slugs: frozenset[str]
    last_edge: dict[str, float]


def round_probability(probability: float, limits: RiskLimits = LIMITS) -> float:
    step = Decimal(str(limits.probability_step))
    units = (Decimal(str(probability)) / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(units * step)


def fee_per_share(price: float, fee_rate: float, exponent: float) -> float:
    if price <= 0 or price >= 1 or fee_rate <= 0:
        return 0.0
    return fee_rate * (price * (1 - price)) ** exponent


def edge_pp(probability: float, mid: float, ask: float, spread: float, fee_rate: float, exponent: float) -> tuple[float, float]:
    """Edge en puntos porcentuales.

    `edge_bruto` compara la probabilidad contra el punto medio.
    `edge_neto` resta la comisión del ask y el spread completo, que es
    la fórmula de la guía.
    """
    bruto = (probability - mid) * 100
    fee_pp = fee_per_share(ask, fee_rate, exponent) * 100
    neto = bruto - fee_pp - (spread * 100)
    return bruto, neto


def kelly_fraction(probability: float, price: float, limits: RiskLimits = LIMITS) -> float:
    if price <= 0 or price >= 1 or probability <= price:
        return 0.0
    full = (probability - price) / (1 - price)
    return min(limits.kelly_multiplier * full, limits.max_position_fraction)


def binary_pnl(stake: float, entry: float, fee_rate: float, exponent: float, won: bool) -> float:
    shares = stake / entry
    fee = shares * fee_per_share(entry, fee_rate, exponent)
    if won:
        return shares * (1 - entry) - fee
    return -(stake + fee)


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _counts_as_evidence(source: Source) -> bool:
    host = _domain(source.url)
    if host in {"x.com", "twitter.com", "mobile.twitter.com"}:
        return False
    return bool(host) and source.url.startswith("http")


def is_fast_market(market: Market, research: Research, now: datetime) -> bool:
    if research.fast_market is True:
        return True
    category = market.category.lower()
    if any(token in category for token in ("sport", "crypto")):
        return True
    if market.end is None:
        return False
    return market.end - now <= timedelta(days=LIMITS.fast_market_days)


def _static_reject(market: Market, now: datetime) -> str | None:
    if market.closed or not market.accepting:
        return "mercado_cerrado"
    price = market.yes_price
    if price < LIMITS.min_price or price > LIMITS.max_price:
        return "precio_extremo"
    if market.end is None:
        return "sin_fecha"
    if market.end - now > timedelta(days=LIMITS.max_resolution_days):
        return "horizonte"
    return None


def preliminary_reason(market: Market, now: datetime | None = None) -> str | None:
    """Precio, horizonte y mercado abierto. Todavía no mira el libro."""
    now = now or datetime.now(timezone.utc)
    return _static_reject(market, now)


def screen_market(market: Market, spread: float | None, now: datetime | None = None) -> str | None:
    """Filtros duros que no necesitan investigación. None si sobrevive."""
    now = now or datetime.now(timezone.utc)
    reason = _static_reject(market, now)
    if reason:
        return reason
    if spread is None:
        return "sin_spread"
    if spread > LIMITS.max_spread:
        return "spread"
    if spread < 0:
        return "spread"
    return None


def parse_research(payload: dict, *, human: bool) -> Research:
    def pair(key: str) -> tuple[str, str]:
        values = payload.get(key) or []
        if not isinstance(values, list) or len(values) < 2:
            raise ValueError(key)
        first, second = str(values[0]).strip(), str(values[1]).strip()
        if not first or not second or first == second:
            raise ValueError(key)
        return first, second

    sources: list[Source] = []
    for item in payload.get("sources") or []:
        if not isinstance(item, dict):
            continue
        published = _parse_source_time(str(item.get("date") or ""))
        if published is None:
            continue
        sources.append(
            Source(
                title=str(item.get("title") or "").strip(),
                url=str(item.get("url") or "").strip(),
                published=published,
                kind=str(item.get("kind") or "").strip().lower(),
            )
        )
    try:
        probability = float(payload["probability"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("probability") from exc
    confidence = str(payload.get("confidence") or "").strip().lower()
    if confidence not in {"alta", "media", "baja"}:
        raise ValueError("confidence")
    return Research(
        resolution_line=str(payload.get("resolution_line") or "").strip(),
        resolution_clear=bool(payload.get("resolution_clear")),
        probability=probability,
        confidence=confidence,
        reasons_for=pair("reasons_for"),
        reasons_against=pair("reasons_against"),
        counterparty_hypothesis=str(payload.get("counterparty_hypothesis") or "").strip(),
        sources=tuple(sources),
        fast_market=payload.get("fast_market") if isinstance(payload.get("fast_market"), bool) else None,
        reviewed_large_divergence=bool(payload.get("reviewed_large_divergence")),
        divergence_attested_by_human=human and bool(payload.get("reviewed_large_divergence")),
    )


def _parse_source_time(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if len(text) == 10:
        try:
            day = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return day + timedelta(hours=23, minutes=59, seconds=59)
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _evidence_problem(market: Market, research: Research, now: datetime) -> str | None:
    if not research.resolution_clear or len(research.resolution_line) < 20:
        return "resolucion_ambigua"
    if not research.counterparty_hypothesis:
        return "sin_hipotesis"
    usable = [source for source in research.sources if _counts_as_evidence(source)]
    domains = {_domain(source.url) for source in usable}
    if len(domains) < LIMITS.min_sources:
        return "fuentes_insuficientes"
    if is_fast_market(market, research, now):
        fresh = [
            source
            for source in usable
            if now - source.published <= timedelta(hours=LIMITS.max_source_age_hours)
        ]
        if len({_domain(source.url) for source in fresh}) < LIMITS.min_sources:
            return "fuentes_viejas"
    if not (0 < research.probability < 1):
        return "probabilidad_invalida"
    return None


def _quote(name: str, code: str, book: Book) -> SideQuote | None:
    if book.bid <= 0 or book.ask >= 1 or book.bid >= book.ask:
        return None
    return SideQuote(
        name=name,
        code=code,
        bid=book.bid,
        ask=book.ask,
        depth=book.ask_depth,
        min_order_size=book.min_order_size,
    )


def evaluate(
    market: Market,
    yes_book: Book,
    no_book: Book,
    research: Research,
    portfolio: Portfolio,
    now: datetime | None = None,
) -> Decision:
    now = now or datetime.now(timezone.utc)
    precio_yes = yes_book.mid
    base = Decision(
        decision="descartar",
        motivo="",
        precio_yes=precio_yes,
        confianza=research.confidence,
        fuentes=_format_sources(research),
    )
    structural = _static_reject(market, now)
    if structural:
        base.motivo = structural
        base.razonamiento = structural
        return base
    if yes_book.spread > LIMITS.max_spread or no_book.spread > LIMITS.max_spread:
        base.motivo = "spread"
        base.spread = max(yes_book.spread, no_book.spread)
        base.razonamiento = "spread mayor a 3 centavos"
        return base
    if precio_yes < LIMITS.min_price or precio_yes > LIMITS.max_price:
        base.motivo = "precio_extremo"
        base.razonamiento = "el punto medio quedó fuera de 0,05–0,95"
        return base

    problem = _evidence_problem(market, research, now)
    if problem:
        base.motivo = problem
        base.razonamiento = problem
        return base

    probability = round_probability(research.probability)
    base.prob = probability
    if abs(probability - precio_yes) * 100 > LIMITS.divergence_review_pp and not research.divergence_attested_by_human:
        base.motivo = "divergencia_25"
        base.razonamiento = (
            "la estimación queda a más de 25 puntos del mercado; "
            "se asume error de lectura hasta una revisión humana"
        )
        return base

    yes = _quote(market.outcomes[0], "YES", yes_book)
    no = _quote(market.outcomes[1], "NO", no_book)
    if yes is None or no is None:
        base.motivo = "libro_invalido"
        base.razonamiento = "libro sin puntas utilizables"
        return base

    candidates = []
    for quote, chance in ((yes, probability), (no, 1 - probability)):
        mid = (quote.bid + quote.ask) / 2
        spread = quote.ask - quote.bid
        bruto, neto = edge_pp(chance, mid, quote.ask, spread, market.fee_rate, market.fee_exponent)
        fraction = kelly_fraction(chance, quote.ask)
        candidates.append((neto, bruto, fraction, quote, chance))
    candidates.sort(key=lambda item: item[0], reverse=True)
    neto, bruto, fraction, quote, chance = candidates[0]
    base.edge_bruto = bruto
    base.edge_neto = neto
    base.lado = quote.code
    base.precio_entrada = quote.ask
    base.spread = quote.ask - quote.bid
    base.razonamiento = _one_line(market, research, quote, chance, neto)

    if neto < LIMITS.min_edge_pp:
        base.motivo = "umbral_8"
        return base

    previous = portfolio.last_edge.get(market.slug)
    if previous is not None and neto > previous:
        base.motivo = "sesgo_edge_creciente"
        base.razonamiento = "el edge creció entre corridas; no se acumula"
        return base

    if portfolio.equity <= portfolio.peak_equity * (1 - LIMITS.drawdown_halt):
        base.motivo = "drawdown_25"
        base.razonamiento = "el bankroll simulado cayó 25% desde el máximo"
        return base

    event_key = market.event_id or market.slug
    if event_key in portfolio.open_event_ids or market.slug in portfolio.open_slugs:
        base.motivo = "correlacion"
        base.razonamiento = "ya hay una posición abierta del mismo evento"
        return base

    if research.confidence == "baja":
        base.decision = "solo_registro"
        base.motivo = "confianza_baja"
        base.fraccion = 0.0
        base.stake = 0.0
        return base

    if portfolio.cash <= 0:
        base.motivo = "bankroll_cero"
        return base

    stake = round(fraction * portfolio.cash, 2)
    shares = stake / quote.ask if quote.ask else 0
    min_size = quote.min_order_size or market.min_order_size
    if stake <= 0 or (min_size and shares < min_size):
        base.motivo = "bajo_minimo"
        base.fraccion = fraction
        base.stake = stake
        return base
    if quote.depth < LIMITS.book_depth_multiple * shares:
        base.motivo = "libro_flaco"
        base.fraccion = fraction
        base.stake = stake
        return base

    base.decision = "simular"
    base.motivo = "edge_neto"
    base.fraccion = fraction
    base.stake = stake
    return base


def _format_sources(research: Research) -> str:
    parts = []
    for source in research.sources:
        stamp = source.published.date().isoformat()
        parts.append(f"{source.url} ({stamp})")
    return " | ".join(parts)


def _one_line(market: Market, research: Research, quote: SideQuote, chance: float, neto: float) -> str:
    text = (
        f"{quote.code} {quote.name}: P={chance:.0%} ask={quote.ask:.2f} "
        f"edge_neto={neto:.1f}pp. {research.resolution_line} "
        f"A favor: {research.reasons_for[0]}. En contra: {research.reasons_against[0]}. "
        f"Contraparte: {research.counterparty_hypothesis}"
    )
    return " ".join(text.split())


def worked_example() -> dict[str, float]:
    """El caso de la guía: 53% contra un YES a 0,42, crypto, sin spread.

    53% no es múltiplo de 5. El sistema lo redondea a 55% antes de operar.
    """
    bruto, neto = edge_pp(0.53, 0.42, 0.42, 0.0, 0.07, 1.0)
    full_kelly = (0.53 - 0.42) / (1 - 0.42)
    fraction = kelly_fraction(0.53, 0.42)
    rounded = round_probability(0.53)
    rounded_fraction = kelly_fraction(rounded, 0.42)
    return {
        "probabilidad_ilustrada": 0.53,
        "probabilidad_que_opera": rounded,
        "precio": 0.42,
        "edge_bruto_pp": bruto,
        "comision_pp": fee_per_share(0.42, 0.07, 1.0) * 100,
        "edge_neto_pp": neto,
        "kelly_completo": full_kelly,
        "fraccion": fraction,
        "fraccion_redondeada": rounded_fraction,
        "stake_sobre_1000": round(fraction * 1000, 2),
    }
