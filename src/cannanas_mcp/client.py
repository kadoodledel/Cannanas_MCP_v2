from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlencode

import httpx

from cannanas_mcp.config import Settings
from cannanas_mcp.openapi_index import OperationSpec


class CannanasClient:
    LIST_CONTAINER_KEYS = ("data", "items", "results", "records", "rows", "carts", "charges", "products", "strains")
    SAFE_HEADER_KEYS = ("content-type", "retry-after", "x-request-id", "x-ratelimit-remaining", "x-ratelimit-reset")

    def __init__(self, *, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.api_base_url.rstrip("/")
        self.api_key = settings.api_key or ""
        self.timeout_seconds = settings.timeout_seconds

    async def call_operation(
        self,
        *,
        operation: OperationSpec,
        rendered_path: str,
        query_params: dict[str, Any] | None = None,
        body: Any = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{rendered_path}"
        if query_params:
            url = f"{url}?{urlencode(self._flatten_query_params(query_params), doseq=True)}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        request_kwargs: dict[str, Any] = {"headers": headers}
        if body is not None:
            request_kwargs["json"] = body

        response: httpx.Response | None = None
        last_error: Exception | None = None

        for attempt in range(self.settings.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.request(operation.method, url, **request_kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt >= self.settings.max_retries:
                    return self._error_result(
                        operation=operation,
                        url=url,
                        reason=reason,
                        error_type="transient",
                        message=f"Cannanas API request failed after retries: {type(exc).__name__}",
                    )
                await asyncio.sleep(self.settings.retry_backoff_seconds * (2**attempt))
                continue

            if response.status_code in {429, 500, 502, 503, 504} and attempt < self.settings.max_retries:
                await asyncio.sleep(self.settings.retry_backoff_seconds * (2**attempt))
                continue
            break

        if response is None:
            return self._error_result(
                operation=operation,
                url=url,
                reason=reason,
                error_type="transient",
                message=f"Cannanas API request failed after retries: {type(last_error).__name__ if last_error else 'unknown'}",
            )

        result: dict[str, Any] = {
            "ok": response.is_success,
            "status_code": response.status_code,
            "method": operation.method,
            "url": url,
            "operation_id": operation.operation_id,
            "reason": reason,
            "response_headers": {
                key: value for key, value in response.headers.items() if key.lower() in self.SAFE_HEADER_KEYS
            },
        }
        content_type = response.headers.get("content-type", "")
        result["data"] = self._parse_response_body(response, content_type)
        if not response.is_success:
            result["error"] = self._classify_http_error(response)
            result["error_type"] = self._classify_error_type(response.status_code)
        return result

    async def call_paginated_operation(
        self,
        *,
        operation: OperationSpec,
        rendered_path: str,
        query_params: dict[str, Any] | None = None,
        reason: str | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
        max_records: int | None = None,
    ) -> dict[str, Any]:
        query = dict(query_params or {})
        limit = int(query.get("limit") or page_size or self.settings.default_page_size)
        offset = int(query.get("offset") or 0)
        max_pages = max_pages or self.settings.max_pages
        max_records = max_records or self.settings.max_records

        pages_fetched = 0
        raw_page_counts: list[int] = []
        collected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        final_status_code = 200

        while pages_fetched < max_pages and len(collected) < max_records:
            page_query = {**query, "limit": limit, "offset": offset}
            response = await self.call_operation(
                operation=operation,
                rendered_path=rendered_path,
                query_params=page_query,
                reason=reason,
            )
            final_status_code = response.get("status_code", final_status_code)
            if not response.get("ok"):
                response["pagination"] = {
                    "pages_fetched": pages_fetched,
                    "records_collected": len(collected),
                }
                return response

            page_items = self._sort_items(self.extract_list_items(response.get("data")))
            raw_page_counts.append(len(page_items))
            for item in page_items:
                item_id = self._stable_item_id(item)
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                collected.append(item)
                if len(collected) >= max_records:
                    break

            pages_fetched += 1
            if len(page_items) < limit:
                break
            offset += limit

        return {
            "ok": True,
            "status_code": final_status_code,
            "operation_id": operation.operation_id,
            "reason": reason,
            "items": collected,
            "item_count": len(collected),
            "pagination": {
                "page_size": limit,
                "pages_fetched": pages_fetched,
                "raw_page_counts": raw_page_counts,
                "max_pages": max_pages,
                "max_records": max_records,
                "truncated": len(collected) >= max_records or pages_fetched >= max_pages,
            },
        }

    def extract_list_items(self, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []
        for key in self.LIST_CONTAINER_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        list_values = [value for value in data.values() if isinstance(value, list)]
        if len(list_values) == 1:
            return [item for item in list_values[0] if isinstance(item, dict)]
        return []

    def _flatten_query_params(self, query_params: dict[str, Any]) -> dict[str, Any]:
        flattened: dict[str, Any] = {}
        for key, value in query_params.items():
            if value is None:
                continue
            flattened[key] = value
        return flattened

    def _parse_response_body(self, response: httpx.Response, content_type: str) -> Any:
        if "application/json" in content_type:
            try:
                return response.json()
            except ValueError:
                return {"raw_text": response.text, "parse_error": "invalid_json"}
        return response.text

    def _classify_error_type(self, status_code: int) -> str:
        if status_code in {401, 403}:
            return "auth"
        if status_code in {400, 404, 409, 422}:
            return "validation"
        if status_code in {429, 500, 502, 503, 504}:
            return "transient"
        return "server"

    def _classify_http_error(self, response: httpx.Response) -> str:
        error_type = self._classify_error_type(response.status_code)
        return f"Cannanas API request failed ({error_type}, status {response.status_code})."

    def _error_result(
        self,
        *,
        operation: OperationSpec,
        url: str,
        reason: str | None,
        error_type: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "status_code": None,
            "method": operation.method,
            "url": url,
            "operation_id": operation.operation_id,
            "reason": reason,
            "error_type": error_type,
            "error": message,
        }

    def _stable_item_id(self, item: dict[str, Any]) -> str:
        for key in ("id", "uuid", "_id"):
            value = item.get(key)
            if value is not None:
                return f"{key}:{value}"
        return repr(sorted(item.items()))

    def _sort_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def key_for(item: dict[str, Any]) -> tuple[str, str]:
            for key in ("created_at", "updated_at", "fulfilled_at", "started_at", "ended_at"):
                value = item.get(key)
                if value is not None:
                    return (str(value), self._stable_item_id(item))
            for key in ("id", "name", "title"):
                value = item.get(key)
                if value is not None:
                    return (str(value), self._stable_item_id(item))
            return ("", self._stable_item_id(item))

        return sorted(items, key=key_for)
