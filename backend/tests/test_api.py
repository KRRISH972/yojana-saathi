"""Tests for the HTTP API. Gemini and search are mocked; eligibility and data are real.

The TestClient is used without a ``with`` block, so the startup hook (which loads the
embedding model) does not run; nothing here needs it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app import routes
from backend.app.main import app
from backend.models.user_profile import UserProfile
from backend.services import assistant, rate_limit
from backend.services.llm import GeminiError, GeminiRateLimitError
from backend.services.rate_limit import SlidingWindowRateLimiter
from backend.services.retriever import SchemeMatch
from backend.services.understand import LanguageStyle, UnderstandingResult

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test its own generous limiters, so tests never limit each other."""
    limiters = (SlidingWindowRateLimiter(100), SlidingWindowRateLimiter(100))
    monkeypatch.setattr(rate_limit, "_limiters", lambda: limiters)


@pytest.fixture
def mocked_pipeline(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock Gemini (understand + reply) and search; return the understand mock."""
    understand = MagicMock(
        return_value=UnderstandingResult(
            profile_updates=UserProfile(age=35, owns_cultivable_land=True, landholding_hectares=1.0),
            search_query_en="schemes for farmers",
            search_query_hi="किसानों के लिए योजनाएं",
            language_style=LanguageStyle.HINGLISH,
        )
    )
    monkeypatch.setattr(assistant, "understand_message", understand)
    monkeypatch.setattr(assistant, "generate_text", MagicMock(return_value="Aapke liye 2 yojanayein hain."))
    monkeypatch.setattr(
        assistant, "search_schemes",
        MagicMock(return_value=[
            SchemeMatch(scheme_id="pm-kisan", category="agriculture", score=0.7),
            SchemeMatch(scheme_id="pm-kmy", category="pension", score=0.6),
        ]),
    )  # fmt: skip
    return understand


def test_health() -> None:
    """The liveness endpoint answers without touching Gemini."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_returns_reply_cards_and_state(mocked_pipeline: MagicMock) -> None:
    """A first message returns the reply, one card per scheme, and the state to send back."""
    response = client.post("/api/chat", json={"message": "Main 35 saal ka kisan hoon"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Aapke liye 2 yojanayein hain."
    assert body["language_style"] == "hinglish"
    assert body["understood_by"] == "gemini"
    assert {card["scheme_id"] for card in body["schemes"]} == {"pm-kisan", "pm-kmy"}
    kisan = next(card for card in body["schemes"] if card["scheme_id"] == "pm-kisan")
    assert kisan["status"] == "possibly_eligible"
    assert kisan["official_url"].startswith("https://")
    assert kisan["name_hi"]
    state = body["state"]
    assert state["profile"]["age"] == 35
    assert state["last_question"] == body["next_question"]
    assert state["last_question_field"]
    assert set(state["matched_scheme_ids"]) == {"pm-kisan", "pm-kmy"}


def test_state_round_trips_and_bare_no_skips_gemini(mocked_pipeline: MagicMock) -> None:
    """Sending the returned state back with "No" saves the answer in Python (no understand
    call), including the exclusion answer surviving the JSON round trip."""
    first = client.post("/api/chat", json={"message": "Main 35 saal ka kisan hoon"}).json()
    asked_field = first["state"]["last_question_field"]
    assert asked_field.startswith("exclusion:")

    second = client.post("/api/chat", json={"message": "No", "state": first["state"]})

    assert second.status_code == 200
    body = second.json()
    assert body["understood_by"] == "quick_answer"
    assert mocked_pipeline.call_count == 1  # only the first message used the understand call
    assert body["state"]["profile"]["exclusions"] == {asked_field.removeprefix("exclusion:"): False}
    assert body["next_question"] != first["next_question"]
    assert set(body["state"]["matched_scheme_ids"]) == {"pm-kisan", "pm-kmy"}


@pytest.mark.parametrize(
    "payload",
    [
        {"message": ""},
        {"message": "   "},
        {"message": "x" * 1001},
        {},
        {"message": "hi", "state": {"profile": {"age": 500}}},
        {"message": "hi", "unexpected": 1},
    ],
)
def test_invalid_requests_get_a_clear_422(payload: dict, mocked_pipeline: MagicMock) -> None:
    """Bad input is rejected with the invalid_request code, and Gemini is never called."""
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert mocked_pipeline.call_count == 0


def test_gemini_quota_error_becomes_429_with_its_friendly_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini's own rate limit reaches the UI as ai_quota_exhausted, with the friendly text."""
    message = "Gemini's free daily quota has been used up. Please try again tomorrow."
    monkeypatch.setattr(assistant, "understand_message", MagicMock(side_effect=GeminiRateLimitError(message)))
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 429
    assert response.json() == {"error": "ai_quota_exhausted", "detail": message}


def test_other_gemini_errors_become_503_without_internal_details(monkeypatch: pytest.MonkeyPatch) -> None:
    """Internal error text (which could include request details) is never sent to the user."""
    monkeypatch.setattr(assistant, "understand_message", MagicMock(side_effect=GeminiError("secret internals")))
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 503
    assert response.json()["error"] == "ai_unavailable"
    assert "secret internals" not in response.text


def test_per_visitor_rate_limit(monkeypatch: pytest.MonkeyPatch, mocked_pipeline: MagicMock) -> None:
    """A visitor over the per-minute limit gets rate_limited; another visitor still gets through."""
    limiters = (SlidingWindowRateLimiter(2), SlidingWindowRateLimiter(100))
    monkeypatch.setattr(rate_limit, "_limiters", lambda: limiters)
    visitor_a = {"x-forwarded-for": "1.1.1.1"}

    assert client.post("/api/chat", json={"message": "hi"}, headers=visitor_a).status_code == 200
    assert client.post("/api/chat", json={"message": "hi"}, headers=visitor_a).status_code == 200
    third = client.post("/api/chat", json={"message": "hi"}, headers=visitor_a)
    assert third.status_code == 429
    assert third.json()["error"] == "rate_limited"
    assert client.post("/api/chat", json={"message": "hi"}, headers={"x-forwarded-for": "2.2.2.2"}).status_code == 200
    assert mocked_pipeline.call_count == 3  # the rejected request never reached Gemini


def test_global_rate_limit_applies_across_visitors(monkeypatch: pytest.MonkeyPatch, mocked_pipeline: MagicMock) -> None:
    """Rotating addresses cannot get around the global cap."""
    limiters = (SlidingWindowRateLimiter(100), SlidingWindowRateLimiter(2))
    monkeypatch.setattr(rate_limit, "_limiters", lambda: limiters)
    codes = [
        client.post("/api/chat", json={"message": "hi"}, headers={"x-forwarded-for": f"9.9.9.{i}"}).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]


def test_sliding_window_frees_up_after_the_window() -> None:
    """Old hits leave the window, so the visitor can send again later."""
    now = [0.0]
    limiter = SlidingWindowRateLimiter(1, window_seconds=60, clock=lambda: now[0])
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    now[0] = 61.0
    assert limiter.allow("a") is True


def test_visitor_key_prefers_the_first_forwarded_address() -> None:
    """Behind a proxy, the first X-Forwarded-For entry is the real visitor."""
    request = MagicMock()
    request.headers = {"x-forwarded-for": "5.5.5.5, 10.0.0.1"}
    assert routes.visitor_key(request) == "5.5.5.5"
