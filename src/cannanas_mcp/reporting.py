from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from cannanas_mcp.policy import normalize_source_operations

PERSON_ENTITY_KEYS = {
    "member", "members", "user", "users", "customer", "customers", "patient", "patients",
    "person", "people", "applicant", "applicants", "contact", "contacts",
}
SENSITIVE_KEY_FRAGMENTS = {
    "address", "street", "house_number", "housenumber", "postal", "postcode", "zip", "email",
    "phone", "mobile", "telephone", "birth", "birthday", "dob", "date_of_birth", "identity",
    "passport", "id_card", "idcard", "document", "iban", "bic", "bank", "tax_id", "taxid",
    "social_security", "ssn", "health", "diagnosis", "prescription", "medical", "journal", "note", "comment",
}
PERSON_NAME_KEYS = {"name", "first_name", "firstname", "last_name", "lastname", "full_name", "fullname"}
SAFE_NAME_CONTEXTS = {"product", "products", "strain", "strains", "category", "categories", "club", "clubs", "inventory"}

MONEY_FIELD_PRIORITY = (
    "paid_amount", "amount_paid", "paidAmount", "amountPaid", "gross_total", "grossTotal",
    "net_total", "netTotal", "total_amount", "totalAmount", "total_price", "totalPrice",
    "grand_total", "grandTotal", "final_total", "finalTotal", "cart_total", "cartTotal",
    "checkout_total", "checkoutTotal", "summary.total", "summary.amount", "summary.gross_total",
    "summary.net_total", "amounts.total", "amounts.gross", "amounts.net", "payment.amount",
    "payment.total", "price_total", "priceTotal", "total", "amount", "price",
)
LINE_ITEM_MONEY_FIELDS = (
    "line_total", "lineTotal", "total", "total_price", "totalPrice", "amount", "price", "unit_price", "unitPrice",
)


def resolve_period_window(start_date: str | None, end_date: str | None, *, default_days: int = 7) -> tuple[str, str]:
    end = date.today() if end_date is None else _parse_iso_date(end_date)
    start = end - timedelta(days=default_days - 1) if start_date is None else _parse_iso_date(start_date)
    if start > end:
        raise ValueError("start_date must be on or before end_date.")
    return start.isoformat(), end.isoformat()


def build_revenue_summary(charges: list[dict[str, Any]], *, period_start: str, period_end: str) -> dict[str, Any]:
    by_status: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "amount_total": 0.0})
    by_payment_method: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "amount_total": 0.0})
    by_amount_source: dict[str, int] = defaultdict(int)
    total_amount = 0.0
    paid_amount = 0.0
    extracted_amounts = 0

    for charge in charges:
        amount, source = _extract_money_amount_with_source(charge)
        status = str(_pick_value(charge, ["status"]) or "unknown").lower()
        payment_method = str(_pick_value(charge, ["payment_method", "paymentMethod", "payment.method"]) or "unknown").upper()
        if amount is not None:
            amount = round(amount, 2)
            extracted_amounts += 1
            total_amount += amount
            by_status[status]["amount_total"] += amount
            by_payment_method[payment_method]["amount_total"] += amount
            by_amount_source[source or "unknown"] += 1
            if status in {"paid", "fulfilled", "completed", "closed", "settled"}:
                paid_amount += amount
        by_status[status]["count"] += 1
        by_payment_method[payment_method]["count"] += 1

    notes = ["Revenue totals are derived heuristically from Cannanas payloads and should be validated against finance exports for accounting use."]
    if charges and extracted_amounts == 0:
        notes.append("Records were found, but no supported money field was extractable. Run diagnose_reporting_sources and inspect candidate_money_fields.")

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
            "by_amount_source": [{"key": key, "count": value} for key, value in sorted(by_amount_source.items())],
        },
        "notes": notes,
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
            strain_name = str(_pick_value(item, ["strain_name", "strain.name", "product.strain_name", "product.strain.name", "inventory_item.strain.name"]) or "unknown")
            item_count += 1
            if quantity is None:
                continue
            totals_by_unit[unit] += quantity
            strain_totals[strain_name]["quantity_total"] += quantity
            strain_totals[strain_name]["item_count"] += 1
    return {
        "period_start": period_start,
        "period_end": period_end,
        "totals": {"cart_count": len(carts), "cart_item_count": item_count, "total_by_unit": {unit: round(amount, 3) for unit, amount in sorted(totals_by_unit.items())}},
        "comparisons": {},
        "breakdowns": {"cart_status_counts": [{"key": key, "count": totals_by_status[key]} for key in sorted(totals_by_status)], "top_strains": _collapse_named_totals(strain_totals)},
        "notes": ["Dispensed quantities are aggregated from fulfilled cart item quantities. Mixed units are kept separate rather than converted."],
        "source_operations": normalize_source_operations(["getClubCarts"]),
    }


def build_strain_performance(carts: list[dict[str, Any]], products: list[dict[str, Any]], strains: list[dict[str, Any]], *, period_start: str, period_end: str) -> dict[str, Any]:
    product_lookup = _build_product_lookup(products)
    strain_lookup = _build_strain_lookup(strains)
    totals: dict[str, dict[str, Any]] = defaultdict(lambda: {"quantity_total": 0.0, "cart_count": 0, "unit": None})
    for cart in carts:
        seen_strains_for_cart: set[str] = set()
        for item in _extract_cart_items(cart):
            product_id = _pick_value(item, ["product_id", "product.id", "inventory_item.product_id"])
            explicit_name = _pick_value(item, ["strain_name", "strain.name", "product.strain_name", "product.strain.name", "inventory_item.strain.name"])
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
    breakdown = [{"key": name, "quantity_total": round(v["quantity_total"], 3), "cart_count": v["cart_count"], "unit": v["unit"]} for name, v in sorted(totals.items(), key=lambda item: (-item[1]["quantity_total"], item[0]))]
    return {"period_start": period_start, "period_end": period_end, "totals": {"strain_count": len(breakdown), "tracked_cart_count": len(carts)}, "comparisons": {}, "breakdowns": {"by_strain": breakdown}, "notes": ["Strain names are resolved from cart items first, then enriched from product and strain lookups when available."], "source_operations": normalize_source_operations(["getClubCarts", "getClubProducts", "getClubStrains"])}


def build_category_breakdown(carts: list[dict[str, Any]], products: list[dict[str, Any]], *, period_start: str, period_end: str) -> dict[str, Any]:
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
    return {"period_start": period_start, "period_end": period_end, "totals": {"category_count": len(category_totals)}, "comparisons": {}, "breakdowns": {"by_category": _collapse_named_totals(category_totals)}, "notes": ["Category grouping uses cart item fields first and falls back to the club product catalog when product IDs are available."], "source_operations": normalize_source_operations(["getClubCarts", "getClubProducts"])}


def build_weekly_metrics(*, period_start: str, period_end: str, revenue_summary: dict[str, Any], dispensed_amounts: dict[str, Any], strain_performance: dict[str, Any], category_breakdown: dict[str, Any], member_statistics: Any, member_journals: list[dict[str, Any]]) -> dict[str, Any]:
    member_stats_summary = summarize_member_statistics(member_statistics)
    return {
        "period_start": period_start,
        "period_end": period_end,
        "totals": {"charge_count": revenue_summary["totals"]["charge_count"], "revenue_amount_total": revenue_summary["totals"]["amount_total"], "fulfilled_cart_count": dispensed_amounts["totals"]["cart_count"], "journal_entry_count": len(member_journals), "member_statistics": member_stats_summary},
        "comparisons": {},
        "breakdowns": {"revenue_by_status": revenue_summary["breakdowns"]["by_status"], "dispensed_by_unit": [{"key": key, "quantity_total": value} for key, value in dispensed_amounts["totals"]["total_by_unit"].items()], "top_strains": strain_performance["breakdowns"]["by_strain"][:10], "category_breakdown": category_breakdown["breakdowns"]["by_category"]},
        "notes": ["Weekly metrics combine revenue, fulfilled carts, member activity, and enrichment from products/strains where available.", *revenue_summary["notes"], *dispensed_amounts["notes"]],
        "source_operations": normalize_source_operations([*revenue_summary["source_operations"], *dispensed_amounts["source_operations"], *strain_performance["source_operations"], *category_breakdown["source_operations"], "getMemberstatistics", "getClubMemberJournals"]),
    }


def build_reporting_source_diagnostics(*, period_start: str, period_end: str, source_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    recommendations: list[str] = []
    for operation_id, response in source_results.items():
        if not response.get("ok"):
            diagnostics[operation_id] = {"ok": False, "status_code": response.get("status_code"), "error_type": response.get("error_type"), "error": response.get("error")}
            continue
        diagnostics[operation_id] = summarize_items_for_diagnostics(operation_id, response.get("items") or [])
    charges = diagnostics.get("getClubCharges", {})
    carts = diagnostics.get("getClubCarts", {})
    if charges.get("item_count") == 0 and carts.get("item_count", 0) > 0:
        recommendations.append("Club charges are empty while carts exist. Revenue should probably be derived from fulfilled carts, ledger/TSE transactions, or another finance endpoint rather than getClubCharges.")
    if charges.get("item_count", 0) > 0 and charges.get("items_with_extractable_money", 0) == 0:
        recommendations.append("Club charges exist but no amount is extractable with current parsers. Use candidate_money_fields to add the correct amount path.")
    if carts.get("item_count") == 0:
        recommendations.append("No carts were found for the selected period. Verify club_id, date field semantics, and date range.")
    if not recommendations:
        recommendations.append("Use the source with non-zero records and extractable money fields as the first candidate for sales reporting.")
    return {"period_start": period_start, "period_end": period_end, "sources": diagnostics, "recommendations": recommendations, "privacy": {"raw_records_returned": False, "sample_values_redacted": True}, "source_operations": normalize_source_operations(source_results.keys())}


def build_data_quality_report(*, period_start: str, period_end: str, diagnostics: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    sources = diagnostics.get("sources", {})
    charges = sources.get("getClubCharges", {})
    carts = sources.get("getClubCarts", {})
    products = sources.get("getClubProducts", {})
    strains = sources.get("getClubStrains", {})
    if charges.get("ok") is False:
        issues.append({"severity": "high", "source": "getClubCharges", "issue": "charges_source_error", "detail": charges.get("error")})
    if carts.get("ok") is False:
        issues.append({"severity": "high", "source": "getClubCarts", "issue": "carts_source_error", "detail": carts.get("error")})
    if charges.get("item_count") == 0 and carts.get("item_count", 0) > 0:
        issues.append({"severity": "high", "source": "getClubCharges/getClubCarts", "issue": "revenue_source_mismatch", "detail": "Charges are empty but carts exist, so charge-based revenue can report zero incorrectly."})
    if charges.get("item_count", 0) > 0 and charges.get("items_with_extractable_money", 0) == 0:
        issues.append({"severity": "high", "source": "getClubCharges", "issue": "charge_money_not_extractable", "detail": "Charges exist but the current parser cannot find a monetary total field."})
    if carts.get("item_count", 0) > 0 and carts.get("items_with_extractable_money", 0) == 0:
        issues.append({"severity": "medium", "source": "getClubCarts", "issue": "cart_money_not_extractable", "detail": "Carts exist but no common money fields were detected in sampled records."})
    if products.get("item_count") == 0:
        issues.append({"severity": "medium", "source": "getClubProducts", "issue": "empty_product_catalog"})
    if strains.get("item_count") == 0:
        issues.append({"severity": "low", "source": "getClubStrains", "issue": "empty_strain_catalog"})
    return {"period_start": period_start, "period_end": period_end, "issue_count": len(issues), "issues": issues, "notes": ["This report is based on metadata, counts, and safe field-shape diagnostics rather than raw personal records."], "source_operations": diagnostics.get("source_operations", [])}


def summarize_items_for_diagnostics(operation_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    field_counts: dict[str, int] = defaultdict(int)
    date_fields: set[str] = set()
    status_counts: dict[str, int] = defaultdict(int)
    money_fields: dict[str, int] = defaultdict(int)
    quantity_fields: dict[str, int] = defaultdict(int)
    items_with_extractable_money = 0
    extractable_money_sources: dict[str, int] = defaultdict(int)
    items_with_items_list = 0
    for item in items[:200]:
        flattened = _flatten_shape(item)
        for key, value in flattened.items():
            field_counts[key] += 1
            key_l = key.lower()
            if "date" in key_l or key_l.endswith("_at") or key_l in {"createdat", "updatedat"}:
                date_fields.add(key)
            if key_l.endswith("status") or key_l == "status":
                status_counts[str(value)] += 1
            if any(fragment in key_l for fragment in ("amount", "total", "price", "gross", "net", "paid", "fee", "sum")):
                if _coerce_float(value) is not None:
                    money_fields[key] += 1
            if any(fragment in key_l for fragment in ("quantity", "weight", "gram", "amount")):
                if _coerce_float(value) is not None:
                    quantity_fields[key] += 1
        amount, source = _extract_money_amount_with_source(item)
        if amount is not None:
            items_with_extractable_money += 1
            extractable_money_sources[source or "unknown"] += 1
        if _extract_cart_items(item):
            items_with_items_list += 1
    return {"ok": True, "operation_id": operation_id, "item_count": len(items), "sampled_count": min(len(items), 200), "date_fields_seen": sorted(date_fields), "status_counts": dict(sorted(status_counts.items(), key=lambda item: (-item[1], item[0]))[:20]), "candidate_money_fields": dict(sorted(money_fields.items(), key=lambda item: (-item[1], item[0]))[:40]), "candidate_quantity_fields": dict(sorted(quantity_fields.items(), key=lambda item: (-item[1], item[0]))[:20]), "items_with_extractable_money": items_with_extractable_money, "extractable_money_sources": dict(sorted(extractable_money_sources.items())), "items_with_items_list": items_with_items_list, "top_level_fields": sorted({key.split(".")[0] for key in field_counts})[:80]}


def sanitize_for_privacy(data: Any, *, context: tuple[str, ...] = ()) -> Any:
    if isinstance(data, list):
        return [sanitize_for_privacy(item, context=context) for item in data]
    if not isinstance(data, dict):
        return data
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        key_l = key.lower()
        next_context = (*context, key_l)
        sanitized[key] = "[REDACTED]" if _is_sensitive_key(key_l, context) else sanitize_for_privacy(value, context=next_context)
    return sanitized


def summarize_member_statistics(member_statistics: Any) -> dict[str, Any]:
    if isinstance(member_statistics, list):
        return {"raw_list_length": len(member_statistics)}
    if not isinstance(member_statistics, dict):
        return {"raw_type": type(member_statistics).__name__}
    summary: dict[str, Any] = {}
    for key in ("total", "active", "requested", "pending", "exit_requested", "exit_scheduled", "suspended", "unverified"):
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
    amount, _source = _extract_money_amount_with_source(charge)
    return amount


def _extract_money_amount_with_source(record: dict[str, Any]) -> tuple[float | None, str | None]:
    for path in MONEY_FIELD_PRIORITY:
        value = _pick_value(record, [path])
        number = _coerce_float(value)
        if number is not None:
            return number, path
    item_total = _sum_line_items(record)
    if item_total is not None:
        return item_total, "line_items_sum"
    flattened = _flatten_shape(record)
    candidates: list[tuple[str, float]] = []
    for key, value in flattened.items():
        key_l = key.lower()
        if any(fragment in key_l for fragment in ("total", "amount", "gross", "net", "paid")) and not any(fragment in key_l for fragment in ("quantity", "weight", "gram")):
            number = _coerce_float(value)
            if number is not None:
                candidates.append((key, number))
    if candidates:
        candidates.sort(key=lambda item: (_money_key_rank(item[0]), item[0]))
        return candidates[0][1], candidates[0][0]
    return None, None


def _sum_line_items(record: dict[str, Any]) -> float | None:
    items = _extract_cart_items(record)
    if not items:
        return None
    total = 0.0
    found = False
    for item in items:
        direct_amount = _pick_first_number(item, LINE_ITEM_MONEY_FIELDS)
        quantity = _extract_quantity(item)
        unit_price = _pick_first_number(item, ("unit_price", "unitPrice", "price", "amount"))
        if direct_amount is not None and ("unit_price" not in item and "unitPrice" not in item):
            total += direct_amount
            found = True
        elif quantity is not None and unit_price is not None:
            total += quantity * unit_price
            found = True
    return total if found else None


def _money_key_rank(key: str) -> int:
    key_l = key.lower()
    rank = 100
    for idx, token in enumerate(("paid", "gross", "net", "total", "amount", "price")):
        if token in key_l:
            rank = min(rank, idx)
    if any(token in key_l for token in ("tax", "vat", "fee", "discount")):
        rank += 20
    return rank


def _pick_first_number(data: dict[str, Any], paths: tuple[str, ...]) -> float | None:
    for path in paths:
        number = _coerce_float(_pick_value(data, [path]))
        if number is not None:
            return number
    return None


def _extract_quantity(item: dict[str, Any]) -> float | None:
    for path in ("fulfilled_quantity", "fulfilledQuantity", "dispensed_quantity", "dispensedQuantity", "quantity", "amount", "weight"):
        number = _coerce_float(_pick_value(item, [path]))
        if number is not None:
            return number
    return None


def _build_product_lookup(products: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for product in products:
        product_id = _pick_value(product, ["id", "product_id"])
        if product_id is None:
            continue
        lookup[str(product_id)] = {"category": _pick_value(product, ["category", "type", "kind"]), "strain_name": _pick_value(product, ["strain_name", "strain.name"]), "strain_id": _pick_value(product, ["strain_id", "strain.id"]), "unit": _pick_value(product, ["unit", "quantity_unit"])}
    return lookup


def _build_strain_lookup(strains: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for strain in strains:
        strain_id = _pick_value(strain, ["id", "strain_id"])
        if strain_id is not None:
            lookup[str(strain_id)] = {"name": _pick_value(strain, ["name", "title"])}
    return lookup


def _lookup_strain_context(product_lookup: dict[str, dict[str, Any]], strain_lookup: dict[str, dict[str, Any]], product_id: Any, strain_id: Any) -> tuple[str | None, Any]:
    if product_id is not None and str(product_id) in product_lookup:
        product = product_lookup[str(product_id)]
        if product.get("strain_id") and str(product["strain_id"]) in strain_lookup:
            return strain_lookup[str(product["strain_id"])] ["name"], product.get("unit")
        return product.get("strain_name"), product.get("unit")
    if strain_id is not None and str(strain_id) in strain_lookup:
        return strain_lookup[str(strain_id)]["name"], None
    return None, None


def _extract_cart_items(cart: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "cart_items", "cartItems", "line_items", "lineItems", "positions", "products"):
        items = cart.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    for value in cart.values():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            sample = value[0]
            if any(key in sample for key in ("quantity", "fulfilled_quantity", "fulfilledQuantity", "product_id", "productId", "strain_id", "strainId", "price", "amount", "total")):
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
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace("€", "").replace(" ", "")
        if "," in cleaned and "." not in cleaned:
            cleaned = cleaned.replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _to_sorted_breakdown(values: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"key": key, "count": value["count"], "amount_total": round(value["amount_total"], 2)} for key, value in sorted(values.items(), key=lambda item: (-item[1]["amount_total"], item[0]))]


def _collapse_named_totals(values: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"key": key, "quantity_total": round(value["quantity_total"], 3), "item_count": value["item_count"]} for key, value in sorted(values.items(), key=lambda item: (-item[1]["quantity_total"], item[0]))]


def _flatten_shape(data: Any, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                flattened.update(_flatten_shape(value, path))
            elif isinstance(value, list):
                flattened[path] = f"list[{len(value)}]"
                if value and isinstance(value[0], dict):
                    flattened.update(_flatten_shape(value[0], f"{path}[]"))
            else:
                flattened[path] = value
    return flattened


def _is_sensitive_key(key_l: str, context: tuple[str, ...]) -> bool:
    if any(fragment in key_l for fragment in SENSITIVE_KEY_FRAGMENTS):
        return True
    if key_l in PERSON_NAME_KEYS:
        if any(part in SAFE_NAME_CONTEXTS for part in context):
            return False
        if any(part in PERSON_ENTITY_KEYS for part in context):
            return True
        return key_l != "name"
    return False
