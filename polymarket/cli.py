"""Interfaz de línea de comandos del agente en simulación."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from polymarket.audit import render_audit
from polymarket.clients import PublicClient, token_ids
from polymarket.config import DEFAULT_LEDGER, LIMITS
from polymarket.engine import evaluate_estimate, paper_pnl, scan, winner_from_market
from polymarket.ledger import (
    as_float,
    complete_resolution,
    current_bankroll,
    load_rows,
    open_positions,
    peak_bankroll,
)
from polymarket.rules import edge_bruto_pp, edge_neto_pp, fee_rate_for, kelly_full, position_fraction
from polymarket.telegram import format_summary, send_message


def cmd_scan(args: argparse.Namespace) -> int:
    stats, rows, bankroll = scan(
        limit=args.limit,
        ledger_path=Path(args.ledger),
        persist=not args.dry_run,
        fetch_books=not args.skip_books,
        order=args.order,
    )
    saved = load_rows(Path(args.ledger)) if not args.dry_run else rows
    cash = current_bankroll(saved, LIMITS.initial_bankroll) if saved else bankroll
    peak = peak_bankroll(saved, LIMITS.initial_bankroll) if saved else bankroll
    text = format_summary(stats, cash, peak, rows)
    print(text)
    if args.notify:
        sent = send_message(text)
        print("telegram: enviado" if sent else "telegram: sin token/chat_id, no se envió")
    return 0


def _load_estimates(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("estimaciones") or data.get("estimates") or [data]
    if not isinstance(data, list):
        raise ValueError("el archivo de estimaciones tiene que ser una lista")
    return data


def cmd_propose(args: argparse.Namespace) -> int:
    from polymarket.ledger import append_row

    estimates = _load_estimates(Path(args.file))
    client = PublicClient()
    path = Path(args.ledger)
    rows = load_rows(path)
    cash = current_bankroll(rows, LIMITS.initial_bankroll)
    accepted = 0
    for estimate in estimates:
        slug = estimate.get("slug")
        if not slug:
            print("estimación sin slug, se salta")
            continue
        market = client.market_by_slug(slug)
        if not market:
            print(f"{slug}: mercado no encontrado")
            continue
        ids = token_ids(market)
        price_map = client.prices(ids) if ids else {}
        book = client.book(ids[0]) if ids else None
        try:
            spread = client.spread(ids[0]) if ids else None
        except Exception:
            spread = None
        row = evaluate_estimate(
            estimate,
            market,
            bankroll=cash,
            rows=rows,
            price_map=price_map,
            book=book,
            spread=spread,
        )
        if not args.dry_run:
            row = append_row(row, path)
            rows.append(row)
        if row.get("decision") == "simulado":
            accepted += 1
            cash = as_float(row.get("bankroll_post"), cash)
        print(
            f"{slug}: {row.get('decision')} | {row.get('motivo')} | "
            f"p={row.get('prob_estimada')} c={row.get('precio_yes')} "
            f"edge={row.get('edge_neto')} stake={row.get('stake_simulado')}"
        )
    print(f"simuladas en esta corrida: {accepted}")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    path = Path(args.ledger)
    rows = load_rows(path)
    client = PublicClient()
    resolved = 0
    for index, row in enumerate(rows):
        if row.get("decision") != "simulado" or row.get("resultado"):
            continue
        slug = row.get("slug")
        if not slug:
            continue
        market = client.market_by_slug(slug)
        if not market:
            continue
        winner = winner_from_market(market)
        if not winner:
            continue
        side = (row.get("lado") or "YES").upper()
        stake = as_float(row.get("stake_simulado"), 0.0)
        cost = as_float(row.get("precio_yes"), 0.0)
        if side == "NO":
            cost = 1.0 - cost if cost is not None else 0.0
        from polymarket.rules import fee_rate_for

        fee_rate = fee_rate_for(row.get("categoria") or "other")
        won = (side == "YES" and winner == "YES") or (side == "NO" and winner == "NO")
        pnl = paper_pnl(side, stake, cost, won, fee_rate)
        locked = as_float(row.get("bankroll_post"), as_float(row.get("bankroll_pre"), 0.0))
        # Al abrir se descontó el stake; al resolver vuelve el stake ± pnl.
        bankroll_post = locked + stake + pnl
        complete_resolution(
            index,
            resultado=winner,
            pnl=pnl,
            bankroll_post=bankroll_post,
            path=path,
        )
        resolved += 1
        print(f"{slug}: {winner} pnl={pnl:.2f} bankroll={bankroll_post:.2f}")
    print(f"resueltas: {resolved}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    rows = load_rows(Path(args.ledger))
    print(render_audit(rows[-args.last :] if args.last else rows))
    return 0


def cmd_example(_: argparse.Namespace) -> int:
    p, c = 0.53, 0.42
    fee = fee_rate_for("politics")
    spread = 0.01
    print("EJEMPLO DE LA GUÍA (p=0,53 · c=0,42 · política)")
    print(f"  edge bruto     = {edge_bruto_pp(p, c):.2f} pp  (pasa el filtro de 8)")
    print(f"  comisión       ≈ {fee * c * (1 - c) * 100:.2f} pp  (feeRate={fee})")
    print(f"  edge neto      = {edge_neto_pp(p, c, spread, fee):.2f} pp  (bruto − fee − spread)")
    print(f"  Kelly completo = {kelly_full(p, c) * 100:.1f}%  ← nadie usa esto")
    print(f"  1/4 Kelly      = {kelly_full(p, c) * 0.25 * 100:.1f}%")
    print(f"  fracción final = {position_fraction(p, c) * 100:.2f}%  (min de 1/4 Kelly y 6%)")
    print()
    print("Si el agente estimara 48% contra 42%:")
    print(f"  edge bruto = {edge_bruto_pp(0.48, c):.2f} pp  → se descarta, no se discute")
    print()
    print("Vitalidad de acierto ≠ operar más.")
    print("Sale de descartar el 95%, estimar DESPUÉS de la evidencia,")
    print("medir calibración en mercados YA resueltos, y no tocar los límites.")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    rows = open_positions(load_rows(Path(args.ledger)))
    if not rows:
        print("no hay posiciones simuladas abiertas")
        return 0
    for row in rows:
        print(
            f"{row.get('timestamp_utc')} {row.get('slug')} "
            f"lado={row.get('lado')} stake={row.get('stake_simulado')} "
            f"p={row.get('prob_estimada')} c={row.get('precio_yes')}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m polymarket",
        description="Agente de Polymarket en simulación. No envía órdenes reales.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_ledger(target):
        target.add_argument(
            "--ledger",
            default=str(DEFAULT_LEDGER),
            help="ruta del registro CSV",
        )

    scan_p = sub.add_parser("scan", help="revisar mercados abiertos y aplicar filtros duros")
    add_ledger(scan_p)
    scan_p.add_argument("--limit", type=int, default=150)
    scan_p.add_argument("--dry-run", action="store_true")
    scan_p.add_argument("--skip-books", action="store_true")
    scan_p.add_argument("--notify", action="store_true")
    scan_p.add_argument(
        "--order",
        default="volume24hr",
        help="campo Gamma para ordenar (default: volume24hr, los que se mueven)",
    )
    scan_p.set_defaults(func=cmd_scan)

    prop = sub.add_parser("propose", help="aplicar una estimación investigada (sigue en paper)")
    add_ledger(prop)
    prop.add_argument("--file", required=True, help="JSON con estimaciones")
    prop.add_argument("--dry-run", action="store_true")
    prop.set_defaults(func=cmd_propose)

    res = sub.add_parser("resolve", help="completar pnl cuando un mercado ya cerró")
    add_ledger(res)
    res.set_defaults(func=cmd_resolve)

    aud = sub.add_parser("audit", help="calibración y sesgos sobre el registro")
    add_ledger(aud)
    aud.add_argument("--last", type=int, default=0, help="usar solo las últimas N filas")
    aud.set_defaults(func=cmd_audit)

    op = sub.add_parser("open", help="listar paper trades sin resolver")
    add_ledger(op)
    op.set_defaults(func=cmd_open)

    ex = sub.add_parser("example", help="reproducir el ejemplo numérico de la guía")
    ex.set_defaults(func=cmd_example)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
