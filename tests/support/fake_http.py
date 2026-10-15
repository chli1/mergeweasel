"""urlopen replaced by canned answers, for the API clients' unit tests."""

import io
import json
import urllib.error
import urllib.request


class Response:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"" if self.payload is None else json.dumps(self.payload).encode()


SERVER = "https://git.example"
API = f"{SERVER}/api/v1/repos/org/repo"


def serve(monkeypatch, *answers):
    """Each urlopen takes the next answer: a payload, a Response, or an exception to raise."""
    seen, pending = [], list(answers)

    def urlopen(request, timeout=None):
        seen.append((request.get_method(), request.full_url, json.loads(request.data) if request.data else None))
        answer = pending.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, Response) else Response(answer)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def http_error(code, message=None):
    body = json.dumps({"message": message}).encode() if message else b""
    return urllib.error.HTTPError(f"{API}/x", code, "Reason", hdrs=None, fp=io.BytesIO(body))
