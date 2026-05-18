from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from cannanas_mcp.openapi_index import OperationSpec

if TYPE_CHECKING:
    from cannanas_mcp.config import Settings


SAFE_REPORT_WRITE_OPERATION_IDS = frozenset(
    {
        "getCsvReport",
        "getXlsxReport",
        "getXlsxReportValidations",
        "createAnnualReportPdf",
    }
)

DEFAULT_ALLOWED_OPERATION_IDS = frozenset(
    {
        "testAuth",
        "getClubCarts",
        "getClubCharges",
        "getClubProducts",
        "getClubStrains",
        "getMemberstatistics",
        "getClubMemberJournals",
        *SAFE_REPORT_WRITE_OPERATION_IDS,
    }
)


def parse_allowed_operation_ids(value: str | None) -> frozenset[str]:
    if value is None:
        return DEFAULT_ALLOWED_OPERATION_IDS
    parsed = {item.strip() for item in value.split(",") if item.strip()}
    return frozenset(parsed) if parsed else DEFAULT_ALLOWED_OPERATION_IDS


def is_operation_allowed(settings: Settings, operation: OperationSpec) -> tuple[bool, str | None]:
    if operation.operation_id not in settings.allowed_operation_ids:
        return (
            False,
            f"Operation '{operation.operation_id}' is not enabled in this deployment. "
            "Use one of the explicit reporting tools or update the allowlist deliberately.",
        )
    if settings.read_only_mode and operation.method != "GET" and operation.operation_id not in SAFE_REPORT_WRITE_OPERATION_IDS:
        return (
            False,
            f"Operation '{operation.operation_id}' is blocked because the server is running in read-only mode.",
        )
    return True, None


def normalize_source_operations(operation_ids: Iterable[str]) -> list[str]:
    return sorted({operation_id for operation_id in operation_ids if operation_id})
