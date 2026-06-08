from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import json
from typing import Any

EXPECTED_TOTAL = Decimal("39.98")
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
    snapshot_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    db_total = to_decimal(db_raw_total)
    product_price = to_decimal(product_raw_price)
    cached = to_decimal(cached_raw)
    db_unit = infer_unit(product_price)
    cache_unit = infer_unit(cached)
    shown = display_total(db_total, config_unit)
    total_correct = shown is not None and abs(shown - EXPECTED_TOTAL) < Decimal("0.005")

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
        "expected_total": decimal_string(EXPECTED_TOTAL),
        "expected_total_money": money(EXPECTED_TOTAL),
        "total_correct": total_correct,
        "layers_consistent": layers_consistent,
        "verdict": "PASS" if passed else "FAIL",
        "pass": passed,
        "layers": layers,
        "note": note,
    }
