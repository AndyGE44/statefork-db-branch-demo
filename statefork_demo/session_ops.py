from __future__ import annotations

import argparse
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import redis_wire, search_index
from .logic import CACHE_KEY, PROBE_PRODUCT_ID, read_config, write_config

DEFAULT_STATE_DIR = Path("/demo/session_state")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def index_dir(state_dir: Path) -> Path:
    return state_dir / "index"


def setup(state_dir: Path, port: int) -> dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)
    db_path = state_dir / "checkout.sqlite"
    if db_path.exists():
        db_path.unlink()
    index_path = search_index.index_path(index_dir(state_dir))
    if index_path.exists():
        index_path.unlink()
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                price NUMERIC NOT NULL
            );
            CREATE TABLE carts (id INTEGER PRIMARY KEY);
            CREATE TABLE cart_items (
                cart_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                PRIMARY KEY (cart_id, product_id)
            );
            INSERT INTO products VALUES (1, "Wireless Mouse", 19.99);
            INSERT INTO products VALUES (2, "USB Keyboard", 29.99);
            INSERT INTO products VALUES (3, "Laptop Stand", 34.99);
            INSERT INTO carts VALUES (1);
            INSERT INTO cart_items VALUES (1, 1, 2);
            """
        )
    write_config(state_dir, "dollars")
    redis_wire.wait_until_ready(port)
    redis_wire.flushdb(port)
    reindex(state_dir)
    return raw_state(state_dir, port)


def warm(state_dir: Path, port: int) -> dict[str, Any]:
    write_cache_from_db(state_dir, port)
    return raw_state(state_dir, port)


def fix_a(state_dir: Path, port: int) -> dict[str, Any]:
    with connect(state_dir / "checkout.sqlite") as conn:
        conn.execute("UPDATE products SET price = ROUND(price * 100, 0)")
        conn.execute("UPDATE products SET name = ?, price = ? WHERE id = 1", ("Wireless Mouse Pro", 2499))
    write_config(state_dir, "cents")
    write_cache_from_db(state_dir, port)
    reindex(state_dir)
    return raw_state(state_dir, port)


def all_products(state_dir: Path) -> list[dict[str, Any]]:
    with connect(state_dir / "checkout.sqlite") as conn:
        rows = conn.execute("SELECT id, name, price FROM products ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def reindex(state_dir: Path) -> None:
    search_index.reindex(index_dir(state_dir), all_products(state_dir))


def product_detail(state_dir: Path, product_id: int = PROBE_PRODUCT_ID) -> dict[str, Any]:
    with connect(state_dir / "checkout.sqlite") as conn:
        row = conn.execute("SELECT id, name, price FROM products WHERE id = ?", (int(product_id),)).fetchone()
    if row is None:
        raise RuntimeError(f"Product {product_id} not found in SQLite DB")
    payload = search_index.product_payload(dict(row))
    indexed = search_index.get_indexed_product(index_dir(state_dir), product_id)
    return {"product": payload, "indexed_product": indexed}


def raw_values(state_dir: Path) -> dict[str, Any]:
    with connect(state_dir / "checkout.sqlite") as conn:
        row = conn.execute(
            """
            SELECT SUM(products.price * cart_items.quantity) AS db_raw_total,
                   MAX(products.price) AS product_raw_price,
                   MAX(products.name) AS product_name
            FROM cart_items
            JOIN products ON products.id = cart_items.product_id
            WHERE cart_items.cart_id = 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("cart query returned no rows")
    return {
        "db_raw_total": Decimal(str(row["db_raw_total"])),
        "product_raw_price": Decimal(str(row["product_raw_price"])),
        "product_name": str(row["product_name"]),
    }


def write_cache_from_db(state_dir: Path, port: int) -> None:
    raw = raw_values(state_dir)["db_raw_total"]
    value = str(raw.quantize(Decimal("1"))) if raw == raw.to_integral() else format(raw.normalize(), "f")
    redis_wire.set(port, CACHE_KEY, value)


def raw_state(state_dir: Path, port: int) -> dict[str, Any]:
    values = raw_values(state_dir)
    cached = redis_wire.get(port, CACHE_KEY) if redis_wire.ping(port) else None
    indexed = search_index.get_indexed_product(index_dir(state_dir), PROBE_PRODUCT_ID)
    return {
        "db_raw_total": str(values["db_raw_total"]),
        "product_raw_price": str(values["product_raw_price"]),
        "product_name": values["product_name"],
        "config_unit": read_config(state_dir),
        "cached_raw": cached,
        "indexed_product": indexed,
        "redis_alive": redis_wire.ping(port),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["setup", "warm", "fix_a", "state", "search", "product"])
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--port", type=int, default=6397)
    parser.add_argument("--query", default=search_index.PROBE_QUERY)
    parser.add_argument("--product-id", type=int, default=PROBE_PRODUCT_ID)
    args = parser.parse_args()

    if args.command == "setup":
        payload = setup(args.state_dir, args.port)
    elif args.command == "warm":
        payload = warm(args.state_dir, args.port)
    elif args.command == "fix_a":
        payload = fix_a(args.state_dir, args.port)
    elif args.command == "search":
        payload = {"query": args.query, "hits": search_index.search(index_dir(args.state_dir), args.query)}
    elif args.command == "product":
        payload = product_detail(args.state_dir, args.product_id)
    else:
        payload = raw_state(args.state_dir, args.port)
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
