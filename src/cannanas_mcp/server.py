from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from fastmcp import FastMCP

from cannanas_mcp.client import CannanasClient
from cannanas_mcp.config import Settings, load_settings
from cannanas_mcp.openapi_index import OperationIndex
from cannanas_mcp.policy import is_operation_allowed
from cannanas_mcp.reporting import (
    build_category_breakdown,
    build_dispensed_amounts,
    build_revenue_summary,
    build_strain_performance,
    build_weekly_metrics,
    resolve_period_window,
    summarize_member_statistics,
)


mcp = FastMCP("Cannanas MCP Server")
logger = logging.getLogger("cannanas_mcp")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


@lru_cache(maxsize=1)
def get_index() -> OperationIndex:
    settings = get_settings()
    return OperationIndex.from_file(settings.openapi_path)


def _missing_api_key_error() -> dict[str, Any]:
    return {
        "ok": False,
        "error": "Missing Cannanas API key. Set CANNANAS_API_KEY in the MCP server environment before calling Cannanas endpoints.",
    }


def _make_client(settings: Settings) -> CannanasClient:
    return CannanasClient(settings=settings)


def _tool_disabled_error(tool_name: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"Tool '{tool_name}' is disabled in this deployment.",
    }


async def _call_paginated(
    *,
    settings: Settings,
    operation_id: str,
    path_params: dict[str, Any],
    query_params: dict[str, Any] | None,
    reason: str,
    page_size: int | None = None,
    max_records: int | None = None,
) -> dict[str, Any]:
    operation = get_index().get(operation_id)
    allowed, error = is_operation_allowed(settings, operation)
    if not allowed:
        return {
            "ok": False,
            "error": error,
            "operation": operation.to_summary(),
        }

    rendered_path = get_index().render_path(operation, path_params)
    client = _make_client(settings)
    logger.info("Calling Cannanas paginated operation %s", operation_id)
    if operation.supports_pagination:
        return await client.call_paginated_operation(
            operation=operation,
            rendered_path=rendered_path,
            query_params=query_params,
            reason=reason,
            page_size=page_size,
            max_records=max_records,
        )

    result = await client.call_operation(
        operation=operation,
        rendered_path=rendered_path,
        query_params=query_params,
        reason=reason,
    )
    if not result.get("ok"):
        return result
    items = client.extract_list_items(result.get("data"))
    return {
        "ok": True,
        "status_code": result.get("status_code"),
        "operation_id": operation_id,
        "reason": reason,
        "items": items,
        "item_count": len(items),
        "pagination": {
            "page_size": None,
            "pages_fetched": 1,
            "raw_page_counts": [len(items)],
            "max_pages": 1,
            "max_records": max_records or settings.max_records,
            "truncated": False,
        },
    }


async def _call_raw(
    *,
    settings: Settings,
    operation_id: str,
    path_params: dict[str, Any],
    query_params: dict[str, Any] | None,
    body: dict[str, Any] | list[Any] | None,
    reason: str,
) -> dict[str, Any]:
    operation = get_index().get(operation_id)
    allowed, error = is_operation_allowed(settings, operation)
    if not allowed:
        return {
            "ok": False,
            "error": error,
            "operation": operation.to_summary(),
        }

    rendered_path = get_index().render_path(operation, path_params)
    logger.info("Calling Cannanas operation %s", operation_id)
    return await _make_client(settings).call_operation(
        operation=operation,
        rendered_path=rendered_path,
        query_params=query_params,
        body=body,
        reason=reason,
    )


@mcp.resource("cannanas://info")
def server_info() -> dict[str, Any]:
    settings = get_settings()
    index = get_index()
    return {
        "name": "Cannanas MCP Server",
        "base_url": settings.api_base_url,
        "openapi_path": str(settings.openapi_path),
        "operations_indexed": len(index.operations),
        "tags": index.tags(),
        "production_mode": settings.production_mode,
        "read_only_mode": settings.read_only_mode,
        "allowed_operation_ids": sorted(settings.allowed_operation_ids),
        "tools": [
            "search_operations",
            "describe_operation",
            "auth_test",
            "call_operation",
            "get_weekly_metrics",
            "get_revenue_summary",
            "get_dispensed_amounts",
            "get_strain_performance",
            "get_category_breakdown",
        ],
    }


@mcp.tool
def search_operations(
    query: str = "",
    tag: str | None = None,
    method: str | None = None,
    limit: int = 15,
    include_unsupported: bool = False,
) -> dict[str, Any]:
    """Search Cannanas API operations by free text, tag, or HTTP method."""
    settings = get_settings()
    if not settings.enable_search_operations:
        return _tool_disabled_error("search_operations")

    index = get_index()
    results = index.search(
        query=query,
        tag=tag,
        method=method,
        limit=max(1, min(limit, 50)),
        include_unsupported=include_unsupported,
    )
    return {
        "query": query,
        "tag": tag,
        "method": method,
        "count": len(results),
        "results": results,
    }


@mcp.tool
def describe_operation(operation_id: str) -> dict[str, Any]:
    """Return the full input shape for a Cannanas API operation before you call it."""
    settings = get_settings()
    if not settings.enable_describe_operation:
        return _tool_disabled_error("describe_operation")
    return get_index().get(operation_id).to_detail()


@mcp.tool
async def auth_test() -> dict[str, Any]:
    """Call Cannanas /v1/auth/test using the configured CANNANAS_API_KEY."""
    settings = get_settings()
    if not settings.api_key:
        return _missing_api_key_error()

    operation = get_index().get("testAuth")
    rendered_path = get_index().render_path(operation, {})
    return await _make_client(settings).call_operation(
        operation=operation,
        rendered_path=rendered_path,
        reason="Validate the configured Cannanas API key.",
    )


@mcp.tool
async def call_operation(
    operation_id: str,
    path_params: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    dry_run: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Call a supported Cannanas API operation by operation_id."""
    settings = get_settings()
    if not settings.api_key:
        return _missing_api_key_error()

    operation = get_index().get(operation_id)
    if not operation.supported:
        return {
            "ok": False,
            "error": operation.unsupported_reason,
            "operation": operation.to_summary(),
        }

    allowed, error = is_operation_allowed(settings, operation)
    if not allowed:
        return {
            "ok": False,
            "error": error,
            "operation": operation.to_summary(),
        }

    rendered_path = get_index().render_path(operation, path_params)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "operation": operation.to_summary(),
            "rendered_path": rendered_path,
            "query_params": query_params,
            "body": body,
            "reason": reason,
        }

    logger.info("Calling Cannanas operation %s", operation_id)
    return await _make_client(settings).call_operation(
        operation=operation,
        rendered_path=rendered_path,
        query_params=query_params,
        body=body,
        reason=reason,
    )


@mcp.tool
async def get_revenue_summary(
    club_id: str,
    start_date: str,
    end_date: str,
    include_archived: bool = False,
    page_size: int | None = None,
) -> dict[str, Any]:
    """Return a normalized revenue summary for a date range using club charges."""
    settings = get_settings()
    if not settings.api_key:
        return _missing_api_key_error()

    period_start, period_end = resolve_period_window(start_date, end_date, default_days=7)
    response = await _call_paginated(
        settings=settings,
        operation_id="getClubCharges",
        path_params={"clubId": club_id},
        query_params={
            "created_at_start": period_start,
            "created_at_end": period_end,
            "archived": include_archived,
        },
        reason="Build a normalized revenue summary for reporting.",
        page_size=page_size,
    )
    if not response.get("ok"):
        return response
    result = build_revenue_summary(response["items"], period_start=period_start, period_end=period_end)
    result["pagination"] = response["pagination"]
    return result


@mcp.tool
async def get_dispensed_amounts(
    club_id: str,
    start_date: str,
    end_date: str,
    page_size: int | None = None,
) -> dict[str, Any]:
    """Return normalized dispensed quantities from fulfilled carts for a date range."""
    settings = get_settings()
    if not settings.api_key:
        return _missing_api_key_error()

    period_start, period_end = resolve_period_window(start_date, end_date, default_days=7)
    response = await _call_paginated(
        settings=settings,
        operation_id="getClubCarts",
        path_params={"clubId": club_id},
        query_params={
            "created_at_start": period_start,
            "created_at_end": period_end,
            "status": ["fulfilled"],
            "archived": False,
        },
        reason="Aggregate dispensed quantities from fulfilled carts.",
        page_size=page_size,
    )
    if not response.get("ok"):
        return response
    result = build_dispensed_amounts(response["items"], period_start=period_start, period_end=period_end)
    result["pagination"] = response["pagination"]
    return result


@mcp.tool
async def get_strain_performance(
    club_id: str,
    start_date: str,
    end_date: str,
    page_size: int | None = None,
) -> dict[str, Any]:
    """Return normalized strain performance using fulfilled carts plus product and strain lookups."""
    settings = get_settings()
    if not settings.api_key:
        return _missing_api_key_error()

    period_start, period_end = resolve_period_window(start_date, end_date, default_days=7)
    carts_response = await _call_paginated(
        settings=settings,
        operation_id="getClubCarts",
        path_params={"clubId": club_id},
        query_params={
            "created_at_start": period_start,
            "created_at_end": period_end,
            "status": ["fulfilled"],
            "archived": False,
        },
        reason="Analyze fulfilled carts for strain performance.",
        page_size=page_size,
    )
    if not carts_response.get("ok"):
        return carts_response
    products_response = await _call_paginated(
        settings=settings,
        operation_id="getClubProducts",
        path_params={"clubId": club_id},
        query_params={},
        reason="Enrich strain performance with the product catalog.",
        page_size=page_size,
    )
    if not products_response.get("ok"):
        return products_response
    strains_response = await _call_paginated(
        settings=settings,
        operation_id="getClubStrains",
        path_params={"clubId": club_id},
        query_params={"archived": False},
        reason="Enrich strain performance with strain metadata.",
        page_size=page_size,
    )
    if not strains_response.get("ok"):
        return strains_response
    result = build_strain_performance(
        carts_response["items"],
        products_response["items"],
        strains_response["items"],
        period_start=period_start,
        period_end=period_end,
    )
    result["pagination"] = {
        "carts": carts_response["pagination"],
        "products": products_response["pagination"],
        "strains": strains_response["pagination"],
    }
    return result


@mcp.tool
async def get_category_breakdown(
    club_id: str,
    start_date: str,
    end_date: str,
    page_size: int | None = None,
) -> dict[str, Any]:
    """Return normalized category/type breakdown from fulfilled carts."""
    settings = get_settings()
    if not settings.api_key:
        return _missing_api_key_error()

    period_start, period_end = resolve_period_window(start_date, end_date, default_days=7)
    carts_response = await _call_paginated(
        settings=settings,
        operation_id="getClubCarts",
        path_params={"clubId": club_id},
        query_params={
            "created_at_start": period_start,
            "created_at_end": period_end,
            "status": ["fulfilled"],
            "archived": False,
        },
        reason="Aggregate fulfilled cart items by category.",
        page_size=page_size,
    )
    if not carts_response.get("ok"):
        return carts_response
    products_response = await _call_paginated(
        settings=settings,
        operation_id="getClubProducts",
        path_params={"clubId": club_id},
        query_params={},
        reason="Enrich category breakdown with the product catalog.",
        page_size=page_size,
    )
    if not products_response.get("ok"):
        return products_response
    result = build_category_breakdown(
        carts_response["items"],
        products_response["items"],
        period_start=period_start,
        period_end=period_end,
    )
    result["pagination"] = {
        "carts": carts_response["pagination"],
        "products": products_response["pagination"],
    }
    return result


@mcp.tool
async def get_weekly_metrics(
    club_id: str,
    end_date: str | None = None,
    start_date: str | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    """Return a normalized weekly metrics package for reporting agents."""
    settings = get_settings()
    if not settings.api_key:
        return _missing_api_key_error()

    period_start, period_end = resolve_period_window(start_date, end_date, default_days=7)

    revenue_summary = await get_revenue_summary(
        club_id=club_id,
        start_date=period_start,
        end_date=period_end,
        include_archived=False,
        page_size=page_size,
    )
    if not revenue_summary.get("period_start"):
        return revenue_summary

    dispensed_amounts = await get_dispensed_amounts(
        club_id=club_id,
        start_date=period_start,
        end_date=period_end,
        page_size=page_size,
    )
    if not dispensed_amounts.get("period_start"):
        return dispensed_amounts

    strain_performance = await get_strain_performance(
        club_id=club_id,
        start_date=period_start,
        end_date=period_end,
        page_size=page_size,
    )
    if not strain_performance.get("period_start"):
        return strain_performance

    category_breakdown = await get_category_breakdown(
        club_id=club_id,
        start_date=period_start,
        end_date=period_end,
        page_size=page_size,
    )
    if not category_breakdown.get("period_start"):
        return category_breakdown

    member_statistics_result = await _call_raw(
        settings=settings,
        operation_id="getMemberstatistics",
        path_params={"clubId": club_id},
        query_params={},
        body=None,
        reason="Fetch member statistics for weekly metrics.",
    )
    if not member_statistics_result.get("ok"):
        return member_statistics_result
    member_statistics = member_statistics_result.get("data")

    journals_response = await _call_paginated(
        settings=settings,
        operation_id="getClubMemberJournals",
        path_params={"clubId": club_id},
        query_params={
            "created_at_start": period_start,
            "created_at_end": period_end,
            "archived": False,
        },
        reason="Count member journal activity for weekly metrics.",
        page_size=page_size,
    )
    if not journals_response.get("ok"):
        return journals_response

    result = build_weekly_metrics(
        period_start=period_start,
        period_end=period_end,
        revenue_summary=revenue_summary,
        dispensed_amounts=dispensed_amounts,
        strain_performance=strain_performance,
        category_breakdown=category_breakdown,
        member_statistics=member_statistics,
        member_journals=journals_response["items"],
    )
    result["pagination"] = {
        "revenue": revenue_summary.get("pagination"),
        "dispensed": dispensed_amounts.get("pagination"),
        "strain_performance": strain_performance.get("pagination"),
        "category_breakdown": category_breakdown.get("pagination"),
        "member_journals": journals_response.get("pagination"),
    }
    result["membership_statistics_summary"] = summarize_member_statistics(member_statistics)
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mcp.run(transport=get_settings().transport)
