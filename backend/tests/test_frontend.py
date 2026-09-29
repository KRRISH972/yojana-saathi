"""Tests that FastAPI serves the web UI correctly, safely, and small enough for slow networks.

The UI's own logic is unit-tested in JavaScript (frontend/tests/logic.test.js, run with
`node --test "frontend/tests/*.test.js"`); these tests cover how it is served.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import FRONTEND_DIR, app

client = TestClient(app)
PAGE_ASSETS = ("/css/app.css", "/js/logic.js", "/js/app.js")


def test_home_page_is_served_with_the_ui() -> None:
    """GET / returns the chat page with every element app.js needs."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    for element_id in ("log", "composer", "message", "send", "mic", "quick", "lang-hi", "lang-en", "new-chat"):
        assert f'id="{element_id}"' in response.text, element_id


def test_page_assets_are_served() -> None:
    """The CSS and both scripts referenced by the page load."""
    page = client.get("/").text
    for path in PAGE_ASSETS:
        assert path in page
        assert client.get(path).status_code == 200, path


def test_api_routes_still_win_over_static_files() -> None:
    """Mounting the UI at "/" must not shadow the API."""
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.post("/api/chat", json={"message": ""}).json()["error"] == "invalid_request"


def test_only_the_public_folder_is_served() -> None:
    """Tailwind sources and JS tests stay private."""
    assert client.get("/src/input.css").status_code == 404
    assert client.get("/tests/logic.test.js").status_code == 404
    assert client.get("/../backend/app/config.py").status_code == 404


def test_security_headers_and_strict_csp() -> None:
    """The page may only load its own scripts and styles, and can be framed by Hugging Face."""
    headers = client.get("/").headers
    csp = headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "https://huggingface.co" in csp
    assert headers["x-content-type-options"] == "nosniff"
    assert "microphone=(self)" in headers["permissions-policy"]


def test_docs_page_keeps_working_without_the_strict_csp() -> None:
    """FastAPI's /docs loads Swagger UI from a CDN, so it is exempt from the CSP."""
    response = client.get("/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers


def test_responses_are_gzipped_for_slow_networks() -> None:
    """Text assets are compressed when the browser accepts gzip."""
    response = client.get("/js/app.js", headers={"Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"


def test_page_weight_stays_small() -> None:
    """HTML + CSS + JS stay under 60 KB before compression (a budget for low-end phones)."""
    total = sum(len(client.get(path).content) for path in ("/", *PAGE_ASSETS))
    assert total < 60_000, total


def test_page_loads_nothing_from_other_sites() -> None:
    """No CDN scripts, fonts or styles: everything comes from our own server."""
    page = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r'(src|href)="https?://', page)


def test_scripts_never_use_innerhtml() -> None:
    """Server text (Gemini replies, scheme data) is only ever inserted with textContent,
    so a reply can never inject HTML or scripts into the page."""
    for script in Path(FRONTEND_DIR, "js").glob("*.js"):
        code = "\n".join(line for line in script.read_text(encoding="utf-8").splitlines() if not line.strip().startswith(("*", "/*", "//")))
        assert "innerHTML" not in code, script.name
        assert "insertAdjacentHTML" not in code, script.name
