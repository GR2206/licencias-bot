"""Registro append-only. La fila se escribe al decidir.

Solo `resultado` y `pnl` se completan cuando el mercado resuelve.
El bankroll de la corrida siguiente sale de rehacer este archivo, no de
un número recordado en el prompt.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from polymarket_agent.api import Market
from polymarket_agent.engine import Decision, Portfolio, binary_pnl
from polymarket_agent.risk import LIMITS

COLUMNS = [
    "timestamp_utc",
    "mercado",
    "precio_yes",
    "prob_estimada",
    "edge_bruto",
    "edge_neto",
    "fraccion",
    "stake_simulado",
    "bankroll_pre",
    "resultado",
    "pnl",
    "bankroll_post",
    "razonamiento",
    "fuentes",
    "decision",
    "motivo",
    "slug",
    "event_id",
    "lado",
    "precio_entrada",
    "spread",
    "fee_rate",
    "fee_exponent",
    "categoria",
    "confianza",
    "ejecutado",
]

MUTABLE_ON_RESOLVE = {"resultado", "pnl"}


@dataclass
class State:
    initial_bankroll: float
    last_edge: dict[str, float] = field(default_factory=dict)


@dataclass
class Snapshot:
    cash: float
    reserved: float
    equity: float
    peak_equity: float
    open_event_ids: frozenset[str]
    open_slugs: frozenset[str]
    halted: bool

    def portfolio(self, last_edge: dict[str, float]) -> Portfolio:
        return Portfolio(
            cash=self.cash,
            equity=self.equity,
            peak_equity=self.peak_equity,
            open_event_ids=self.open_event_ids,
            open_slugs=self.open_slugs,
            last_edge=last_edge,
        )


class Ledger:
    def __init__(self, home: Path, initial_bankroll: float = 1000.0):
        self.home = home
        self.path = home / "registro.csv"
        self.state_path = home / "estado.json"
        self.initial_default = initial_bankroll
        self.home.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> State:
        if not self.state_path.exists():
            state = State(initial_bankroll=self.initial_default)
            self._write_state(state)
            return state
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        return State(
            initial_bankroll=float(raw["initial_bankroll"]),
            last_edge={key: float(value) for key, value in (raw.get("last_edge") or {}).items()},
        )

    def _write_state(self, state: State) -> None:
        payload = {
            "initial_bankroll": state.initial_bankroll,
            "last_edge": state.last_edge,
        }
        self.state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def rows(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def snapshot(self) -> Snapshot:
        state = self.load_state()
        cash = state.initial_bankroll
        reserved = 0.0
        peak = state.initial_bankroll
        open_events: set[str] = set()
        open_slugs: set[str] = set()
        for row in self.rows():
            if row.get("decision") != "simular":
                continue
            stake = _float(row.get("stake_simulado"))
            if row.get("resultado"):
                cash += _float(row.get("pnl"))
                continue
            cash -= stake
            reserved += stake
            open_events.add(row.get("event_id") or row.get("slug") or "")
            open_slugs.add(row.get("slug") or "")
            equity_now = cash + reserved
            peak = max(peak, equity_now)
        open_events.discard("")
        open_slugs.discard("")
        equity = cash + reserved
        peak = max(peak, equity)
        halted = peak > 0 and equity <= peak * (1 - LIMITS.drawdown_halt)
        return Snapshot(cash, reserved, equity, peak, frozenset(open_events), frozenset(open_slugs), halted)

    def append(self, market: Market | None, decision: Decision, when: datetime | None = None) -> dict[str, str]:
        when = when or datetime.now(timezone.utc)
        snap = self.snapshot()
        bankroll_pre = snap.cash
        bankroll_post = bankroll_pre - decision.stake if decision.decision == "simular" else bankroll_pre
        question = market.label() if market else ""
        row = {column: "" for column in COLUMNS}
        row.update(
            {
                "timestamp_utc": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mercado": question,
                "precio_yes": _num(decision.precio_yes if decision.precio_yes is not None else (market.yes_price if market else None)),
                "prob_estimada": _num(decision.prob),
                "edge_bruto": _num(decision.edge_bruto),
                "edge_neto": _num(decision.edge_neto),
                "fraccion": _num(decision.fraccion),
                "stake_simulado": _num(decision.stake),
                "bankroll_pre": _num(bankroll_pre),
                "resultado": "",
                "pnl": "",
                "bankroll_post": _num(bankroll_post),
                "razonamiento": " ".join((decision.razonamiento or decision.motivo).split()),
                "fuentes": decision.fuentes,
                "decision": decision.decision,
                "motivo": decision.motivo,
                "slug": market.slug if market else "",
                "event_id": market.event_id if market else "",
                "lado": decision.lado,
                "precio_entrada": _num(decision.precio_entrada),
                "spread": _num(decision.spread),
                "fee_rate": _num(market.fee_rate if market else None),
                "fee_exponent": _num(market.fee_exponent if market else None),
                "categoria": market.category if market else "",
                "confianza": decision.confianza,
                "ejecutado": "no",
            }
        )
        self._append_row(row)
        return row

    def remember_edges(self, edges: dict[str, float]) -> None:
        if not edges:
            return
        state = self.load_state()
        state.last_edge.update(edges)
        self._write_state(state)

    def settle(self, slug: str, resultado: str) -> dict[str, str] | None:
        if resultado not in {"YES", "NO"}:
            raise ValueError("resultado debe ser YES o NO")
        rows = self.rows()
        target = None
        for row in rows:
            if row.get("slug") == slug and row.get("decision") == "simular" and not row.get("resultado"):
                target = row
                break
        if target is None:
            return None
        before = dict(target)
        won = (resultado == "YES" and target.get("lado") == "YES") or (
            resultado == "NO" and target.get("lado") == "NO"
        )
        entry = _float(target.get("precio_entrada"))
        stake = _float(target.get("stake_simulado"))
        fee_rate = _float(target.get("fee_rate"))
        exponent = _float(target.get("fee_exponent")) or 1.0
        if entry <= 0 or stake <= 0:
            return None
        pnl = binary_pnl(stake, entry, fee_rate, exponent, won)
        target["resultado"] = resultado
        target["pnl"] = _num(pnl)
        for column, value in before.items():
            if column not in MUTABLE_ON_RESOLVE and target.get(column) != value:
                raise RuntimeError(f"la resolución intentó cambiar {column}")
        self._rewrite(rows)
        return target

    def _append_row(self, row: dict[str, str]) -> None:
        new_file = not self.path.exists()
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)

    def _rewrite(self, rows: list[dict[str, str]]) -> None:
        temporary = self.path.with_suffix(".csv.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in COLUMNS})
        temporary.replace(self.path)


def _float(value: str | None) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def _num(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}"
