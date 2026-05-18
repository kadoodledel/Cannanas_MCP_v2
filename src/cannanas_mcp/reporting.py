from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from cannanas_mcp.policy import normalize_source_operations


def resolve_period_window(start_date: str | None, end_date: str | None, *, default_days: int = 7) -> tuple[str, str]:
    if end_date is None:
        end = date.today()
    else:
        end = _parse_iso_date(end_date)
    if start_date is None:
        start = end - timedelta(days=default_days - 1)
    else:
        start = _parse_iso_date(start_date)
    if start > end:
        raise ValueError("start_date must be on or before end_date.")
    return start.isoformat(), end.isoformat()


def build_revenue_summary(charges: list[dict[str, Any]], *, period_start: str, period_end: str) -> dict[str, Any]:
    by_status: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "amount_total": 0.0})
    by_payment_method: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "amount_total": 0.0})
    total_amount = 0.0
    paid_amount = 0.0
    extracted_amounts = 0

    for charge in charges:
        amount = _extract_money_amount(charge)
        status = str(_pick_value(charge, ["status"]) or "unknown").lower()
        payment_method = str(_pick_value(charge, ["payment_method", "paymentMethod"]) or "unknown").upper()
        if amount is not None:
            amount = round(amount, 2)
            extracted_amounts += 1
            total_amount += amount
            by_status[status]["amount_total"] += amount
            by_payment_method[payment_method]["amount_total"] += amount
            if status == "paid":
                paid_amount += amount
        by_status[status]["count"] += 1
        by_payment_method[payment_method]["count"] += 1

    return {
        "period_start": period_start,
        "period_end": period_end,
        "totals": {
            "charge_count": len(charges),
            "amount_total": round(total_amount, 2),
            "amount_paid": round(paid_amount, 2),
            "charges_with_extractable_amount": extracted_amounts,
        },
        "comparisons": {},
        "breakdowns": {
            "by_status": _to_sorted_breakdown(by_status),
            "by_payment_method": _to_sorted_breakdown(by_payment_method),
        },
        "notes": [
            "Revenue totals are derived heuristically from charge payloads and should be validated against invoices or finance exports for accounting use.",
        ],
        "source_operations": normalize_source_operations(["getClubCharges"]),
    }


def build_dispensed_amounts(carts: list[dict[str, Any]], *, period_start: str, period_end: str) -> dict[str, Any]:
    totals_by_unit: dict[str, float] = defaultdict(float)
    totals_by_status: dict[str, int] = defaultdict(int)
    strain_totals: dict[str, dict[str, Any]] = defaultdict(lambda: {"quantity_total": 0.0, "item_count": 0})
    item_count = 0

    for cart in carts:
        status = str(_pick_value(cart, ["status"]) or "unknown").lower()
        totals_by_status[status] += 1
        for item in _extract_cart_items(cart):
            quantity = _extract_quantity(item)
            unit = str(_pick_value(item, ["unit", "quantity_unit", "weight_unit"]) or "units").lower()
            strain_name = str(
                _pick_value(
                    item,
                    [
                        "strain_name",
                        "strain.name",
                        "product.strain_name",
                        "product.strain.name",
                        "inventory_item.strain.name",
                    ],
                )
                or "unknown"
            )
            item_count += 1
            if quantity is None:
                continue
            totals_by_unit[unit] += quantity
            strain_totals[strain_name]["quantity_total"] += quantity
            strain_totals[strain_name]["item_count"] += 1

    return {
        "period_start": period_start,
        "period_end": period_end,
        "totals": {
            "cart_count": len(carts),
            "cart_item_count": item_count,
            "total_by_unit": {unit: round(amount, 3) for unit, amount in sorted(totals_by_unit.items())},
        },
        "comparisons": {},
        "breakdowns": {
            "cart_status_counts": [{"key": key, "count": totals_by_status[key]} for key in sorted(totals_by_status)],
            "top_strains": _collapse_named_totals(strain_totals),
        },
        "notes": [
            "Dispensed quantities are aggregated from fulfilled cart item quantities. Mixed units are kept separate rather than converted.",
        ],
        "source_operations": normalize_source_operations(["getClubCarts"]),
    }


def build_strain_performance(
    carts: list[dict[str, Any]],
    products: list[dict[str, Any]],
    strains: list[dict[str, Any]],
    *,
    period_start: str,
    period_end: str,
) -> dict[str, Any]:
    product_lookup = _build_product_lookup(products)
    strain_lookup = _build_strain_lookup(strains)
    totals: dict[str, dict[str, Any]] = defaultdict(lambda: {"quantity_total": 0.0, "cart_count": 0, "unit": None})

    for cart in carts:
        seen_strains_for_cart: set[str] = set()
        for item in _extract_cart_items(cart):
            product_id = _pick_value(item, ["product_id", "product.id", "inventory_item.product_id"])
            explicit_name = _pick_value(
                item,
                ["strain_name", "strain.name", "product.strain_name", "product.strain.name", "inventory_item.strain.name"],
            )
            explicit_id = _pick_value(item, ["strain_id", "strain.id", "product.strain_id", "product.strain.id"])
            lookup_name, lookup_unit = _lookup_strain_context(product_lookup, strain_lookup, product_id, explicit_id)
            strain_name = str(explicit_name or lookup_name or "unknown")
            quantity = _extract_quantity(item)
            unit = str(_pick_value(item, ["unit", "quantity_unit", "weight_unit"]) or lookup_unit or "units").lower()
            if quantity is not None:
                totals[strain_name]["quantity_total"] += quantity
            totals[strain_name]["unit"] = unit
            seen_strains_for_cart.add(strain_name)
        for strain_name in seen_strains_for_cart:
            totals[strain_name]["cart_count"] += 1

    breakdown = []
    for strain_name, values in sorted(totals.items(), key=lambda item: (-item[1]["quantity_total"], item[0])):
        breakdown.append(
            {
                "key": strain_name,
                "quantity_total": round(values["quantity_total"], 3),
                "cart_count": values["cart_count"],
                "unit": values["unit"],
            }
        )

    return {
        "period_start": period_start,
        "period_end": period_end,
        "totals": {
            "strain_count": len(breakdown),
            "tracked_cart_count": len(carts),
        },
        "comparisons": {},
        "breakdowns": {
            "by_strain": breakdown,
        },
        "notes": [
            "Strain names are resolved from cart items first, then enriched from product and strain lookups when available.",
        ],
        "source_operations": normalize_source_operations(["getClubCarts", "getClubProducts", "getClubStrains"]),
    }


def build_category_breakdown(
    carts: list[dict[str, Any]],
    products: list[dict[str, Any]],
    *,
    period_start: str,
    period_end: str,
) -> dict[str, Any]:
    product_lookup = _build_product_lookup(products)
    category_totals: dict[str, dict[str, Any]] = defaultdict(lambda: {"quantity_total": 0.0, "item_count": 0})

    for cart in carts:
        for item in _extract_cart_items(cart):
            product_id = _pick_value(item, ["product_id", "product.id", "inventory_item.product_id"])
            category = _pick_value(item, ["category", "type", "kind", "product.type", "product.category"])
            if category is None and product_id is not None and str(product_id) in product_lookup:
                category = product_lookup[str(product_id)]["category"]
            category_name = str(category or "unknown")
            quantity = _extract_quantity(item)
            if quantity is not None:
                category_totals[category_name]["quantity_total"] += quantity
            category_totals[category_name]["item_count"] += 1

    return {
        "period_start": period_start,
        "period_end": period_end,
        "totals": {
            "category_count": len(category_totals),
        },
        "comparisons": {},
        "breakdowns": {
            "by_category": _collapse_named_totals(category_totals),
        },
        "notes": [
            "Category grouping uses cart item fields first and falls back to the club product catalog when product IDs are available.",
        ],
        "source_operations": normalize_source_operations(["getClubCarts", "getClubProducts"]),
    }


def build_weekly_metrics(
    *,
    period_start: str,
    period_end: str,
    revenue_summary: dict[str, Any],
    dispensed_amounts: dict[str, Any],
    strain_performance: dict[str, Any],
    category_breakdown: dict[str, Any],
    member_statistics: Any,
    member_journals: list[dict[str, Any]],
) -> dict[str, Any]:
    member_stats_summary = summarize_member_statistics(member_statistics)
    return {
        "period_start": period_start,
        "period_end": period_end,
        "totals": {
            "charge_count": revenue_summary["totals"]["charge_count"],
            "revenue_amount_total": revenue_summary["totals"]["amount_total"],
            "fulfilled_cart_count": dispensed_amounts["totals"]["cart_count"],
            "journal_entry_count": len(member_journals),
            "member_statistics": member_stats_summary,
        },
        "comparisons": {},
        "breakdowns": {
            "revenue_by_status": revenue_summary["breakdowns"]["by_status"],
            "dispensed_by_unit": [
                {"key": key, "quantity_total": value}
                for key, value in dispensed_amounts["totals"]["total_by_unit"].items()
            ],
            "top_strains": strain_performance["breakdowns"]["by_strain"][:10],
            "category_breakdown": category_breakdown["breakdowns"]["by_category"],
        },
        "notes": [
            "Weekly metrics combine revenue, fulfilled carts, member activity, and enrichment from products/strains where available.",
            *revenue_summary["notes"],
            *dispensed_amounts["notes"],
        ],
        "source_operations": normalize_source_operations(
            [
                *revenue_summary["source_operations"],
                *dispensed_amounts["source_operations"],
                *strain_performance["source_operations"],
                *category_breakdown["source_operations"],
                "getMemberstatistics",
                "getClubMemberJournals",
            ]
        ),
    }


def summarize_member_statistics(member_statistics: Any) -> dict[str, Any]:
    if isinstance(member_statistics, list):
        return {"raw_list_length": len(member_statistics)}
    if not isinstance(member_statistics, dict):
        return {"raw_type": type(member_statistics).__name__}

    summary: dict[str, Any] = {}
    for key in (
        "total",
        "active",
        "requested",
        "pending",
        "exit_requested",
        "exit_scheduled",
        "suspended",
        "unverified",
    ):
        value = _recursive_find(member_statistics, {key})
        if isinstance(value, (int, float)):
            summary[key] = value
    list_lengths = {key: len(value) for key, value in member_statistics.items() if isinstance(value, list)}
    if list_lengths:
        summary["list_lengths"] = list_lengths
    return summary


def _parse_iso_date(value: str) -> date:
    try:
        return datetime.fromisoformat(value).date()
    except ValueError as exc:
        raise ValueError(f"Invalid ISO date '{value}'. Expected YYYY-MM-DD or a full ISO timestamp.") from exc


def _extract_money_amount(charge: dict[str, Any]) -> float | None:
    value = _pick_value(
        charge,
        [
            "paid_amount",
            "amount_paid",
            "gross_total",
            "net_total",
            "total",
            "amount",
            "price",
            "summary.total",
            "amounts.total",
        ],
    )
    return _coerce_float(value)


def _extract_quantity(item: dict[str, Any]) -> float | None:
    for path in ("fulfilled_quantity", "dispensed_quantity", "quantity", "amount", "weight"):
        value = _pick_value(item, [path])
        if (number := _coerce_float(value)) is not None:
            return number
    return None


def _build_product_lookup(products: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for product in products:
        product_id = _pick_value(product, ["id", "product_id"])
        if product_id is None:
            continue
        lookup[str(product_id)] = {
            "category": _pick_value(product, ["category", "type", "kind"]),
            "strain_name": _pick_value(product, ["strain_name", "strain.name"]),
            "strain_id": _pick_value(product, ["strain_id", "strain.id"]),
            "unit": _pick_value(product, ["unit", "quantity_unit"]),
        }
    return lookup


def _build_strain_lookup(strains: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for strain in strains:
        strain_id = _pick_value(strain, ["id", "strain_id"])
        if strain_id is None:
            continue
        lookup[str(strain_id)] = {
            "name": _pick_value(strain, ["name", "title"]),
        }
    return lookup


def _lookup_strain_context(
    product_lookup: dict[str, dict[str, Any]],
    strain_lookup: dict[str, dict[str, Any]],
    product_id: Any,
    strain_id: Any,
) -> tuple[str | None, Any]:
    if product_id is not None and str(product_id) in product_lookup:
        product = product_lookup[str(product_id)]
        if product.get("strain_id") and str(product["strain_id"]) in strain_lookup:
            return strain_lookup[str(product["strain_id"])] ["name"], product.get("unit")
        return product.get("strain_name"), product.get("unit")
    if strain_id is not None and str(strain_id) in strain_lookup:
        return strain_lookup[str(strain_id)]["name"], None
    return None, None


def _extract_cart_items(cart: dict[str, Any]) -> list[dict[str, Any]]:
    items = cart.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    for value in cart.values():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            sample = value[0]
            if any(key in sample for key in ("quantity", "fulfilled_quantity", "product_id", "strain_id")):
                return value
    return []


def _pick_value(data: Any, paths: list[str]) -> Any:
    for path in paths:
        value = _get_path_value(data, path)
        if value is not None:
            return value
    return None


def _get_path_value(data: Any, path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _recursive_find(data: Any, candidate_keys: set[str]) -> Any:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in candidate_keys:
                return value
            found = _recursive_find(value, candidate_keys)
            if found is not None:
                return found
    if isinstance(data, list):
        for item in data:
            found = _recursive_find(item, candidate_keys)
            if found is not None:
                return found
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _to_sorted_breakdown(values: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "count": value["count"],
            "amount_total": round(value["amount_total"], 2),
        }
        for key, value in sorted(values.items(), key=lambda item: (-item[1]["amount_total"], item[0]))
    ]


def _collapse_named_totals(values: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    collapsed = []
    for key, value in sorted(values.items(), key=lambda item: (-item[1]["quantity_total"], item[0])):
        collapsed.append(
            {
                "key": key,
                "quantity_total": round(value["quantity_total"], 3),
                "item_count": value["item_count"],
            }
        )
    return collapsed
