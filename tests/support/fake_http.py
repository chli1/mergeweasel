"""urlopen replaced by canned answers, for the API clients' unit tests."""

import email.message
import io
import json
import urllib.error
import urllib.request
from typing import Any, Literal, Self

import pytest


class Response:
    def __init__(self, payload: Any, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False

    def read(self) -> bytes:
        return b"" if self.payload is None else json.dumps(self.payload).encode()


SERVER = "https://git.example"
API = f"{SERVER}/api/v1/repos/org/repo"


def serve(monkeypatch: pytest.MonkeyPatch, *answers: Any) -> list[tuple[str, str, Any]]:
    """Each urlopen takes the next answer: a payload, a Response, or an exception to raise."""
    seen: list[tuple[str, str, Any]] = []
    pending = list(answers)

    def urlopen(request: urllib.request.Request, timeout: float | None = None) -> Response:
        seen.append(
            (
                request.get_method(),
                request.full_url,
                json.loads(request.data) if isinstance(request.data, bytes) and request.data else None,
            )
        )
        answer = pending.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, Response) else Response(answer)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def http_error(code: int, message: str | None = None) -> urllib.error.HTTPError:
    body = json.dumps({"message": message}).encode() if message else b""
    return urllib.error.HTTPError(f"{API}/x", code, "Reason", hdrs=email.message.Message(), fp=io.BytesIO(body))
