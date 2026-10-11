from __future__ import annotations

import importlib.util
from io import BytesIO
from pathlib import Path


SCRIPT = Path(__file__).with_name("preview_server.py")
SPEC = importlib.util.spec_from_file_location("preview_server", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
NoCacheRequestHandler = MODULE.NoCacheRequestHandler


def test_preview_responses_disable_browser_caching() -> None:
    handler = object.__new__(NoCacheRequestHandler)
    handler.request_version = "HTTP/1.1"
    handler._headers_buffer = []
    handler.wfile = BytesIO()

    handler.end_headers()

    headers = handler.wfile.getvalue().decode("latin-1")
    assert "Cache-Control: no-store, max-age=0\r\n" in headers
    assert "Pragma: no-cache\r\n" in headers
    assert "Expires: 0\r\n" in headers
