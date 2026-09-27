"""Registro CSV write-once. La fila se escribe al decidir y no se reescribe."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from uuid import uuid4

from polymarket.config import DEFAULT_LEDGER, LEDGER_COLUMNS, MUTABLE_LEDGER_FIELDS


def empty_row() -> dict:
    return {column: "" for column in LEDGER_COLUMNS}


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_rows(path: Path = DEFAULT_LEDGER) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for raw in reader:
            row = empty_row()
            row.update({k: (v if v is not None else "") for k, v in raw.items() if k in row})
            rows.append(row)
        return rows


def write_rows(rows: Iterable[dict], path: Path = DEFAULT_LEDGER) -> None:
    ensure_parent(path)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = empty_row()
            out.update({k: row.get(k, "") for k in LEDGER_COLUMNS})
            writer.writerow(out)


def append_row(row: dict, path: Path = DEFAULT_LEDGER) -> dict:
    rows = load_rows(path)
    complete = empty_row()
    complete.update({k: row.get(k, "") for k in LEDGER_COLUMNS})
    if not complete.get("timestamp_utc"):
        complete["timestamp_utc"] = now_utc()
    if not complete.get("run_id"):
        complete["run_id"] = uuid4().hex[:10]
    rows.append(complete)
    write_rows(rows, path)
    return complete


def complete_resolution(
    index: int,
    *,
    resultado: str,
    pnl: float,
    bankroll_post: float,
    path: Path = DEFAULT_LEDGER,
) -> dict:
    """Solo completa resultado/pnl/bankroll_post si todavía están vacíos."""
    rows = load_rows(path)
    if index < 0 or index >= len(rows):
        raise IndexError("fila inexistente")
    row = rows[index]
    if row.get("resultado"):
        raise PermissionError("el resultado ya está escrito; el registro no se toca")
    if row.get("pnl") not in ("", None):
        raise PermissionError("el pnl ya está escrito; el registro no se toca")
    row["resultado"] = resultado
    row["pnl"] = f"{float(pnl):.4f}"
    row["bankroll_post"] = f"{float(bankroll_post):.4f}"
    write_rows(rows, path)
    return row


def fmt_num(value, digits: int = 4) -> str:
    if value in ("", None):
        return ""
    return f"{float(value):.{digits}f}"


def as_float(value, default: Optional[float] = None) -> Optional[float]:
    if value in ("", None):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def current_bankroll(rows: list[dict], initial: float) -> float:
    for row in reversed(rows):
        post = as_float(row.get("bankroll_post"))
        if post is not None:
            return post
        pre = as_float(row.get("bankroll_pre"))
        if pre is not None:
            return pre
    return initial


def peak_bankroll(rows: list[dict], initial: float) -> float:
    peak = initial
    for row in rows:
        for key in ("bankroll_pre", "bankroll_post"):
            value = as_float(row.get(key))
            if value is not None:
                peak = max(peak, value)
    return peak


def open_positions(rows: list[dict]) -> list[dict]:
    opened = []
    for row in rows:
        if row.get("decision") != "simulado":
            continue
        if row.get("resultado") in ("", None):
            opened.append(row)
    return opened


def dumps_sources(sources) -> str:
    if not sources:
        return ""
    if isinstance(sources, str):
        return sources
    return json.dumps(sources, ensure_ascii=False)
