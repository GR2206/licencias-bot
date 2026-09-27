"""Interfaz de línea de comandos. No existe un comando para enviar órdenes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from polymarket_agent.api import HttpMarketData
from polymarket_agent.audit import auditar, revisar
from polymarket_agent.cycle import (
    decide_slug,
    load_research_dir,
    load_research_file,
    notify_telegram,
    render_summary,
    run_scan,
    settle_open,
)
from polymarket_agent.engine import worked_example
from polymarket_agent.ledger import Ledger
from polymarket_agent.risk import LIMITS


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", default=os.environ.get("POLYMARKET_HOME", "/workspace/polymarket"))
    common.add_argument("--bankroll", type=float, default=float(os.environ.get("PAPER_BANKROLL", "1000")))
    parser = argparse.ArgumentParser(
        description="Analista de Polymarket en simulación. Lee datos públicos y registra decisiones. No envía órdenes.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ejemplo", help="muestra la cuenta de 53 por ciento contra 0,42", parents=[common])
    sub.add_parser("limites", help="muestra los límites que el proceso no puede cambiar", parents=[common])

    scan = sub.add_parser("scan", help="filtra mercados y deja pendientes los que sobreviven", parents=[common])
    _pages(scan)

    decide = sub.add_parser("decidir", help="aplica un JSON de evidencia a un mercado, en simulación", parents=[common])
    decide.add_argument("archivo")

    run = sub.add_parser("correr", help="escanea y, si hay JSON de evidencia, simula", parents=[common])
    _pages(run)
    run.add_argument("--investigaciones", default="")
    run.add_argument("--llm", action="store_true", help="pide probabilidad a un modelo; el tamaño lo sigue poniendo el código")
    run.add_argument("--max-estimaciones", type=int, default=3)
    run.add_argument("--avisar", action="store_true", help="manda el resumen por Telegram si hay token")

    sub.add_parser("resolver", help="completa resultado y pnl de lo que ya cerró", parents=[common])
    sub.add_parser("auditar", help="calibración de lo ya resuelto, sin proponer cambios", parents=[common])
    sub.add_parser("revisar", help="dice si hay muestra para tocar la estrategia; no la toca", parents=[common])
    sub.add_parser("resumen", help="bankroll simulado y si el freno está activo", parents=[common])
    return parser


def _pages(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--limit", type=int, default=100)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = Path(args.home)
    ledger = Ledger(home, initial_bankroll=args.bankroll)

    if args.cmd == "ejemplo":
        print(json.dumps(worked_example(), indent=2, ensure_ascii=False))
        print("11 puntos brutos en crypto quedan cerca de 9 netos, antes del spread.")
        print("Ese 53% se redondea a 55% cuando el sistema opera. Kelly completo no se usa.")
        return 0

    if args.cmd == "limites":
        for name in (
            "min_edge_pp",
            "kelly_multiplier",
            "max_position_fraction",
            "max_spread",
            "min_price",
            "max_price",
            "max_resolution_days",
            "divergence_review_pp",
            "book_depth_multiple",
            "drawdown_halt",
        ):
            print(f"{name}={getattr(LIMITS, name)}")
        print("ejecucion_real=no")
        return 0

    if args.cmd == "auditar":
        print(json.dumps(auditar(ledger), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "revisar":
        print(json.dumps(revisar(ledger), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "resumen":
        print(render_summary(ledger))
        return 0

    data = HttpMarketData()
    if args.cmd == "resolver":
        print(json.dumps(settle_open(ledger, data), indent=2, ensure_ascii=False))
        print(render_summary(ledger))
        return 0

    if args.cmd == "decidir":
        research = load_research_file(Path(args.archivo), human=True)
        slug = json.loads(Path(args.archivo).read_text(encoding="utf-8")).get("slug")
        if not slug:
            print("el JSON necesita un slug", file=sys.stderr)
            return 2
        decision = decide_slug(ledger, data, research, slug)
        print(f"{decision.decision} {decision.motivo} edge_neto={decision.edge_neto} stake={decision.stake}")
        print(render_summary(ledger))
        return 0

    research = {}
    folder = Path(args.investigaciones) if getattr(args, "investigaciones", "") else home / "investigaciones"
    if args.cmd == "correr":
        research = load_research_dir(folder, human=True)
    estimator = None
    if getattr(args, "llm", False):
        from polymarket_agent.llm import investigate

        estimator = investigate
    report = run_scan(
        ledger,
        data,
        pages=args.pages,
        limit=args.limit,
        research_by_slug=research,
        estimator=estimator,
        max_estimates=getattr(args, "max_estimaciones", 3),
    )
    text = render_summary(ledger, report)
    print(text)
    if getattr(args, "avisar", False):
        print(notify_telegram(text, home))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
