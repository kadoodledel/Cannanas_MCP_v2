from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml


HTTP_METHODS = ("get", "post", "put", "patch", "delete")


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    location: str
    required: bool
    description: str | None
    schema_summary: dict[str, Any]


@dataclass(frozen=True)
class OperationSpec:
    operation_id: str
    method: str
    path: str
    summary: str
    description: str
    tags: list[str]
    parameters: list[ParameterSpec]
    request_body: dict[str, Any] | None
    supported: bool
    unsupported_reason: str | None
    supports_pagination: bool

    def to_summary(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "method": self.method,
            "path": self.path,
            "summary": self.summary,
            "tags": self.tags,
            "supported": self.supported,
            "unsupported_reason": self.unsupported_reason,
            "supports_pagination": self.supports_pagination,
        }

    def to_detail(self) -> dict[str, Any]:
        return {
            **self.to_summary(),
            "description": self.description,
            "parameters": [asdict(parameter) for parameter in self.parameters],
            "request_body": self.request_body,
        }


class OperationIndex:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.operations = self._build_operations()

    @classmethod
    def from_file(cls, path: Path) -> "OperationIndex":
        with path.open("r", encoding="utf-8") as handle:
            spec = yaml.safe_load(handle)
        return cls(spec)

    def _build_operations(self) -> dict[str, OperationSpec]:
        operations: dict[str, OperationSpec] = {}
        for path, path_item in self.spec.get("paths", {}).items():
            common_parameters = path_item.get("parameters", [])
            for method in HTTP_METHODS:
                operation = path_item.get(method)
                if not operation:
                    continue
                operation_id = operation.get("operationId") or f"{method}_{path.strip('/').replace('/', '_')}"
                combined_parameters = [*common_parameters, *operation.get("parameters", [])]
                parameter_specs = [
                    ParameterSpec(
                        name=parameter.get("name", ""),
                        location=parameter.get("in", ""),
                        required=bool(parameter.get("required", False)),
                        description=parameter.get("description"),
                        schema_summary=self._summarize_schema(parameter.get("schema", {})),
                    )
                    for parameter in combined_parameters
                ]
                request_body = self._summarize_request_body(operation.get("requestBody"))
                supported, unsupported_reason = self._compute_support(request_body)
                query_parameter_names = {
                    parameter.get("name", "")
                    for parameter in combined_parameters
                    if parameter.get("in") == "query"
                }
                operations[operation_id] = OperationSpec(
                    operation_id=operation_id,
                    method=method.upper(),
                    path=path,
                    summary=operation.get("summary", ""),
                    description=operation.get("description", ""),
                    tags=operation.get("tags", []),
                    parameters=parameter_specs,
                    request_body=request_body,
                    supported=supported,
                    unsupported_reason=unsupported_reason,
                    supports_pagination={"offset", "limit"}.issubset(query_parameter_names),
                )
        return operations

    def resolve_ref(self, ref: str) -> dict[str, Any]:
        node: Any = self.spec
        for part in ref.removeprefix("#/").split("/"):
            node = node[part]
        return node

    def _summarize_request_body(self, request_body: dict[str, Any] | None) -> dict[str, Any] | None:
        if not request_body:
            return None
        if "$ref" in request_body:
            request_body = self.resolve_ref(request_body["$ref"])
        content = request_body.get("content", {})
        summary: dict[str, Any] = {
            "required": bool(request_body.get("required", False)),
            "content_types": sorted(content.keys()),
            "schemas": {},
        }
        for content_type, content_info in content.items():
            schema = content_info.get("schema", {})
            summary["schemas"][content_type] = self._summarize_schema(schema)
        return summary

    def _compute_support(self, request_body: dict[str, Any] | None) -> tuple[bool, str | None]:
        if not request_body:
            return True, None
        content_types = request_body.get("content_types", [])
        if "application/json" in content_types:
            return True, None
        return False, "Only endpoints with no body or an application/json body are supported in this version."

    def _summarize_schema(
        self,
        schema: dict[str, Any] | None,
        *,
        depth: int = 0,
        seen_refs: set[str] | None = None,
    ) -> dict[str, Any]:
        if not schema:
            return {}
        if seen_refs is None:
            seen_refs = set()
        if "$ref" in schema:
            ref = schema["$ref"]
            if ref in seen_refs:
                return {"$ref": ref}
            resolved = self.resolve_ref(ref)
            summary = self._summarize_schema(resolved, depth=depth + 1, seen_refs={*seen_refs, ref})
            return {"$ref": ref, **summary}
        if depth >= 3:
            compact: dict[str, Any] = {}
            if "type" in schema:
                compact["type"] = schema["type"]
            if "format" in schema:
                compact["format"] = schema["format"]
            return compact

        summary: dict[str, Any] = {}
        for key in ("type", "format", "description", "nullable", "default", "example"):
            if key in schema:
                summary[key] = schema[key]
        if "enum" in schema:
            summary["enum"] = schema["enum"][:20]
        if "required" in schema:
            summary["required"] = schema["required"]
        if "properties" in schema:
            summary["properties"] = {
                key: self._summarize_schema(value, depth=depth + 1, seen_refs=seen_refs)
                for key, value in list(schema["properties"].items())[:20]
            }
        if "items" in schema:
            summary["items"] = self._summarize_schema(schema["items"], depth=depth + 1, seen_refs=seen_refs)
        if "anyOf" in schema:
            summary["anyOf"] = [
                self._summarize_schema(item, depth=depth + 1, seen_refs=seen_refs)
                for item in schema["anyOf"][:10]
            ]
        if "allOf" in schema:
            summary["allOf"] = [
                self._summarize_schema(item, depth=depth + 1, seen_refs=seen_refs)
                for item in schema["allOf"][:10]
            ]
        return summary

    def tags(self) -> list[str]:
        return sorted(
            {
                tag
                for operation in self.operations.values()
                for tag in operation.tags
            }
        )

    def search(
        self,
        *,
        query: str = "",
        tag: str | None = None,
        method: str | None = None,
        limit: int = 15,
        include_unsupported: bool = False,
    ) -> list[dict[str, Any]]:
        query_text = query.lower().strip()
        method_text = method.upper() if method else None
        matches: list[tuple[int, OperationSpec]] = []
        for operation in self.operations.values():
            if tag and tag not in operation.tags:
                continue
            if method_text and operation.method != method_text:
                continue
            if not include_unsupported and not operation.supported:
                continue
            haystack = " ".join(
                [
                    operation.operation_id,
                    operation.summary,
                    operation.description,
                    operation.path,
                    " ".join(operation.tags),
                ]
            ).lower()
            score = 0
            if not query_text:
                score = 1
            else:
                for token in query_text.split():
                    if token in operation.operation_id.lower():
                        score += 5
                    if token in operation.summary.lower():
                        score += 4
                    if token in operation.path.lower():
                        score += 3
                    if token in haystack:
                        score += 1
            if score > 0:
                matches.append((score, operation))
        matches.sort(key=lambda item: (-item[0], item[1].operation_id))
        return [operation.to_summary() for _, operation in matches[:limit]]

    def get(self, operation_id: str) -> OperationSpec:
        try:
            return self.operations[operation_id]
        except KeyError as exc:
            raise KeyError(
                f"Unknown operation_id '{operation_id}'. Use search_operations to find a valid one."
            ) from exc

    def render_path(self, operation: OperationSpec, path_params: dict[str, Any] | None) -> str:
        rendered_path = operation.path
        for parameter in operation.parameters:
            if parameter.location != "path":
                continue
            if not path_params or parameter.name not in path_params:
                if parameter.required:
                    raise ValueError(f"Missing required path parameter '{parameter.name}'.")
                continue
            rendered_path = rendered_path.replace(
                "{" + parameter.name + "}",
                quote(str(path_params[parameter.name]), safe=""),
            )
        return rendered_path
