from __future__ import annotations

import re
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .logic import decimal_string, display_total, infer_unit, money, to_decimal

INDEX_DB_NAME = "product_search.db"
PROBE_QUERY = "mouse"


def index_path(index_dir: Path) -> Path:
    return Path(index_dir) / INDEX_DB_NAME


def product_source_price(raw_price: Any) -> Decimal | None:
    return display_total(raw_price, infer_unit(raw_price))


def product_payload(product: dict[str, Any]) -> dict[str, Any]:
    raw_price = product.get("price")
    source_price = product_source_price(raw_price)
    unit = infer_unit(raw_price)
    return {
        "id": int(product["id"]),
        "name": str(product["name"]),
        "raw_price": decimal_string(to_decimal(raw_price)),
        "source_unit": unit,
        "price_value": decimal_string(source_price),
        "price_money": money(source_price),
    }


def reindex(index_dir: Path, products: Iterable[dict[str, Any]]) -> None:
    path = index_path(index_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("DROP TABLE IF EXISTS product_search")
        conn.execute(
            """
            CREATE VIRTUAL TABLE product_search USING fts5(
                id UNINDEXED,
                name,
                price_display UNINDEXED,
                price_value UNINDEXED,
                price_unit UNINDEXED
            )
            """
        )
        for product in products:
            payload = product_payload(product)
            conn.execute(
                """
                INSERT INTO product_search(id, name, price_display, price_value, price_unit)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(payload["id"]),
                    payload["name"],
                    payload["price_money"],
                    payload["price_value"],
                    payload["source_unit"],
                ),
            )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint")


def _connect(index_dir: Path) -> sqlite3.Connection:
    path = index_path(index_dir)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "price_display": row["price_display"],
        "price_value": row["price_value"],
        "price_unit": row["price_unit"],
    }


def _match_query(query: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", query.lower())
    return " OR ".join(tokens[:5])


def search(index_dir: Path, query: str = PROBE_QUERY) -> list[dict[str, Any]]:
    path = index_path(index_dir)
    if not path.exists():
        return []
    with _connect(index_dir) as conn:
        match = _match_query(query)
        if match:
            rows = conn.execute(
                """
                SELECT id, name, price_display, price_value, price_unit
                FROM product_search
                WHERE product_search MATCH ?
                ORDER BY rank
                LIMIT 10
                """,
                (match,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, name, price_display, price_value, price_unit
                FROM product_search
                ORDER BY id
                LIMIT 10
                """
            ).fetchall()
    return [_row_payload(row) for row in rows]


def get_indexed_product(index_dir: Path, product_id: int = 1) -> dict[str, Any] | None:
    path = index_path(index_dir)
    if not path.exists():
        return None
    with _connect(index_dir) as conn:
        row = conn.execute(
            """
            SELECT id, name, price_display, price_value, price_unit
            FROM product_search
            WHERE id = ?
            """,
            (str(product_id),),
        ).fetchone()
    return _row_payload(row) if row else None
