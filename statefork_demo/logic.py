from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import json
from typing import Any

EXPECTED_TOTAL = Decimal("39.98")
FIX_A_EXPECTED_TOTAL = Decimal("49.98")
PROBE_PRODUCT_ID = 1
CACHE_KEY = "cart:1:total"
CONFIG_FILE = "schema_version.json"


def to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def infer_unit(raw_value: Any) -> str:
    value = to_decimal(raw_value)
    if value is None:
        return "missing"
    return "cents" if abs(value) >= Decimal("100") else "dollars"


def display_total(raw_total: Any, config_unit: str) -> Decimal | None:
    value = to_decimal(raw_total)
    if value is None:
        return None
    if config_unit == "cents":
        return value / Decimal("100")
    return value


def money(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${rounded}"


def decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")


def read_config(state_dir: Path) -> str:
    path = state_dir / CONFIG_FILE
    if not path.exists():
        return "missing"
    data = json.loads(path.read_text())
    return str(data.get("price_unit", "missing"))


def write_config(state_dir: Path, unit: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / CONFIG_FILE).write_text(json.dumps({"price_unit": unit}, indent=2) + "\n")


def build_status(
    *,
    arm: str,
    backend: str,
    phase: str,
    expected_unit: str,
    db_raw_total: Any,
    product_raw_price: Any,
    config_unit: str,
    cached_raw: Any,
    product_name: str | None = None,
    indexed_product: dict[str, Any] | None = None,
    expected_total: Any = EXPECTED_TOTAL,
    snapshot_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    db_total = to_decimal(db_raw_total)
    product_price = to_decimal(product_raw_price)
    cached = to_decimal(cached_raw)
    expected = to_decimal(expected_total) or EXPECTED_TOTAL
    db_unit = infer_unit(product_price)
    cache_unit = infer_unit(cached)
    shown = display_total(db_total, config_unit)
    source_price = display_total(product_price, db_unit)
    total_correct = shown is not None and abs(shown - expected) < Decimal("0.005")

    indexed_price = to_decimal((indexed_product or {}).get("price_value"))
    indexed_name = (indexed_product or {}).get("name")
    index_price_matches = (
        indexed_price is not None
        and source_price is not None
        and abs(indexed_price - source_price) < Decimal("0.005")
    )
    index_name_matches = bool(indexed_name and product_name and indexed_name == product_name)
    index_ok = bool(index_price_matches and index_name_matches)

    layers = [
        {
            "name": "Database price unit",
            "value": db_unit,
            "raw": decimal_string(product_price),
            "ok": db_unit == expected_unit,
            "expected": expected_unit,
        },
        {
            "name": "schema_version.json",
            "value": config_unit,
            "raw": config_unit,
            "ok": config_unit == expected_unit,
            "expected": expected_unit,
        },
        {
            "name": "Redis cached total",
            "value": cache_unit,
            "raw": decimal_string(cached),
            "ok": cache_unit == expected_unit,
            "expected": expected_unit,
        },
        {
            "name": "Search index",
            "value": (indexed_product or {}).get("price_display") or "missing",
            "raw": f"{indexed_name or 'missing'} / {(indexed_product or {}).get('price_display') or 'n/a'}",
            "ok": index_ok,
            "expected": f"{product_name or 'missing'} / {money(source_price)}",
        },
    ]
    layers_consistent = all(layer["ok"] for layer in layers)
    passed = bool(layers_consistent and total_correct)
    return {
        "arm": arm,
        "backend": backend,
        "phase": phase,
        "snapshot_id": snapshot_id,
        "expected_unit": expected_unit,
        "db_unit": db_unit,
        "config_unit": config_unit,
        "cache_unit": cache_unit,
        "db_raw_total": decimal_string(db_total),
        "product_raw_price": decimal_string(product_price),
        "cached_raw": decimal_string(cached),
        "display_total": decimal_string(shown),
        "display_total_money": money(shown),
        "expected_total": decimal_string(expected),
        "expected_total_money": money(expected),
        "total_correct": total_correct,
        "layers_consistent": layers_consistent,
        "index_in_sync": index_ok,
        "product": {
            "id": PROBE_PRODUCT_ID,
            "db_name": product_name,
            "db_raw_price": decimal_string(product_price),
            "db_source_unit": db_unit,
            "db_source_price": decimal_string(source_price),
            "db_source_price_money": money(source_price),
            "indexed_name": indexed_name,
            "indexed_price": decimal_string(indexed_price),
            "indexed_price_money": (indexed_product or {}).get("price_display") or "n/a",
            "indexed_unit": (indexed_product or {}).get("price_unit") or "missing",
            "index_in_sync": index_ok,
        },
        "verdict": "PASS" if passed else "FAIL",
        "pass": passed,
        "layers": layers,
        "note": note,
    }
