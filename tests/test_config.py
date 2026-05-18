from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cannanas_mcp.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_load_settings_requires_existing_openapi_file(self) -> None:
        with patch.dict(os.environ, {"CANNANAS_OPENAPI_PATH": str(Path("missing.yaml"))}, clear=False):
            with self.assertRaises(FileNotFoundError):
                load_settings()

    def test_load_settings_requires_api_key_in_production(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml") as handle:
            with patch.dict(
                os.environ,
                {
                    "CANNANAS_OPENAPI_PATH": handle.name,
                    "CANNANAS_PRODUCTION_MODE": "true",
                    "CANNANAS_API_KEY": "",
                },
                clear=False,
            ):
                with self.assertRaises(ValueError):
                    load_settings()
