from __future__ import annotations

import unittest

from cannanas_mcp.openapi_index import OperationIndex, OperationSpec, ParameterSpec


class OpenApiIndexTests(unittest.TestCase):
    def test_render_path_replaces_required_parameters(self) -> None:
        index = OperationIndex({"paths": {}})
        operation = OperationSpec(
            operation_id="test",
            method="GET",
            path="/v1/clubs/{clubId}/members/{userId}",
            summary="",
            description="",
            tags=[],
            parameters=[
                ParameterSpec(name="clubId", location="path", required=True, description=None, schema_summary={}),
                ParameterSpec(name="userId", location="path", required=True, description=None, schema_summary={}),
            ],
            request_body=None,
            supported=True,
            unsupported_reason=None,
            supports_pagination=False,
        )
        rendered = index.render_path(operation, {"clubId": "club-1", "userId": "user-2"})
        self.assertEqual(rendered, "/v1/clubs/club-1/members/user-2")
