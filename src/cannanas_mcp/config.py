from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cannanas_mcp.policy import parse_allowed_operation_ids


@dataclass(frozen=True)
class Settings:
    api_base_url: str
    api_key: str | None
    openapi_path: Path
    timeout_seconds: float
    transport: str
    production_mode: bool
    enable_search_operations: bool
    enable_describe_operation: bool
    read_only_mode: bool
    max_retries: int
    retry_backoff_seconds: float
    max_pages: int
    max_records: int
    default_page_size: int
    allowed_operation_ids: frozenset[str]


def get_default_openapi_path() -> Path:
    return Path(__file__).resolve().parents[2] / "cannanas-api-docs.yaml"


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_transport(value: str | None) -> str:
    """Return a FastMCP transport name suitable for hosted MCP deployments.

    Alpic validates this project with the streamable HTTP transport. The previous
    default of stdio is appropriate for local CLI use, but it causes hosted
    validation to hang because there is no HTTP server to answer the probe.
    """
    transport = (value or "streamable-http").strip().lower().replace("_", "-")
    aliases = {
        "streamablehttp": "streamable-http",
        "http": "streamable-http",
    }
    return aliases.get(transport, transport)


def load_settings() -> Settings:
    openapi_path = Path(os.getenv("CANNANAS_OPENAPI_PATH", str(get_default_openapi_path())))
    if not openapi_path.exists():
        raise FileNotFoundError(f"Cannanas OpenAPI file not found: {openapi_path}")

    production_mode = _parse_bool(os.getenv("CANNANAS_PRODUCTION_MODE"), default=False)
    api_key = os.getenv("CANNANAS_API_KEY")
    if production_mode and not api_key:
        raise ValueError("CANNANAS_API_KEY must be set when CANNANAS_PRODUCTION_MODE=true.")

    return Settings(
        api_base_url=os.getenv("CANNANAS_BASE_URL", "https://api.cannanas.club").rstrip("/"),
        api_key=api_key,
        openapi_path=openapi_path,
        timeout_seconds=float(os.getenv("CANNANAS_TIMEOUT_SECONDS", "45")),
        transport=_normalize_transport(os.getenv("MCP_TRANSPORT")),
        production_mode=production_mode,
        enable_search_operations=_parse_bool(
            os.getenv("CANNANAS_ENABLE_SEARCH_OPERATIONS"),
            default=not production_mode,
        ),
        enable_describe_operation=_parse_bool(
            os.getenv("CANNANAS_ENABLE_DESCRIBE_OPERATION"),
            default=True,
        ),
        read_only_mode=_parse_bool(
            os.getenv("CANNANAS_READ_ONLY_MODE"),
            default=True,
        ),
        max_retries=int(os.getenv("CANNANAS_MAX_RETRIES", "2")),
        retry_backoff_seconds=float(os.getenv("CANNANAS_RETRY_BACKOFF_SECONDS", "1.0")),
        max_pages=int(os.getenv("CANNANAS_MAX_PAGES", "10")),
        max_records=int(os.getenv("CANNANAS_MAX_RECORDS", "1000")),
        default_page_size=int(os.getenv("CANNANAS_DEFAULT_PAGE_SIZE", "200")),
        allowed_operation_ids=parse_allowed_operation_ids(os.getenv("CANNANAS_ALLOWED_OPERATION_IDS")),
    )
