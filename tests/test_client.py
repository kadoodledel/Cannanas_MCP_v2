from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cannanas_mcp.client import CannanasClient
from cannanas_mcp.config import Settings


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.settings = Settings(
            api_base_url="https://api.example.com",
            api_key="test",
            openapi_path=Path(self.tempdir.name) / "spec.yaml",
            timeout_seconds=5,
            transport="stdio",
            production_mode=False,
            enable_search_operations=True,
            enable_describe_operation=True,
            read_only_mode=True,
            max_retries=1,
            retry_backoff_seconds=0.01,
            max_pages=3,
            max_records=100,
            default_page_size=50,
            allowed_operation_ids=frozenset({"getClubCarts"}),
        )
        self.settings.openapi_path.write_text("openapi: 3.0.0\npaths: {}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_extract_list_items_from_standard_container(self) -> None:
        client = CannanasClient(settings=self.settings)
        items = client.extract_list_items({"data": [{"id": 1}, {"id": 2}]})
        self.assertEqual(len(items), 2)

    def test_extract_list_items_from_direct_list(self) -> None:
        client = CannanasClient(settings=self.settings)
        items = client.extract_list_items([{"id": 1}, {"id": 2}])
        self.assertEqual(len(items), 2)
