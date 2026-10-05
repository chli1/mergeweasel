import io
import json
import urllib.error
from unittest import mock

import pytest

from mergeweasel import gitea as gitea_module
from mergeweasel.commit_rule import Commit


class Response:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def recording(monkeypatch, payloads):
    seen = []

    def urlopen(request, timeout=None):
        seen.append({"url": request.full_url, "method": request.get_method(),
                     "auth": request.get_header("Authorization"), "timeout": timeout})
        return Response(payloads.pop(0))

    monkeypatch.setattr(gitea_module.urllib.request, "urlopen", urlopen)
    return seen


def test_calls_are_repository_relative_with_token_and_timeout(monkeypatch):
    seen = recording(monkeypatch, [{}])
    gitea_module.Gitea("https://git.example", "org/repo", "secret").call("GET", "/branches/main")
    assert seen == [{"url": "https://git.example/api/v1/repos/org/repo/branches/main", "method": "GET",
                     "auth": "token secret", "timeout": gitea_module.HTTP_TIMEOUT_SECONDS}]


def test_without_a_token_no_authorization_header(monkeypatch):
    seen = recording(monkeypatch, [{}])
    gitea_module.Gitea("https://git.example", "org/repo", "").call("GET", "/branches/main")
    assert seen[0]["auth"] is None


def test_own_login_reads_the_user_of_the_token(monkeypatch):
    seen = recording(monkeypatch, [{"login": "weasel-bot"}])
    assert gitea_module.Gitea("https://git.example", "org/repo", "secret").own_login() == "weasel-bot"
    assert seen[0]["url"] == "https://git.example/api/v1/user"


def test_paged_reads_until_a_short_page(monkeypatch):
    seen = recording(monkeypatch, [[{"n": index} for index in range(50)], [{"n": 50}]])
    items = gitea_module.Gitea("https://git.example", "org/repo", "").paged("/issues?state=all")
    assert len(items) == 51
    assert [call["url"].split("?", 1)[1] for call in seen] == [
        "state=all&limit=50&page=1", "state=all&limit=50&page=2"]


def test_issue_numbers_and_pull_commits_read_through_paged():
    gitea = mock.Mock(spec=gitea_module.Gitea)
    gitea.paged.side_effect = lambda path: {
        "/issues?state=all&type=issues": [{"number": 1}, {"number": 4}],
        "/pulls/7/commits?stat=false&verification=false&files=false": [
            {"sha": "a" * 40, "commit": {"message": "feat: x (#1)\n\nbody"}, "parents": [{"sha": "b" * 40}]}],
    }[path]
    assert gitea_module.issue_numbers(gitea) == {1, 4}
    assert gitea_module.pull_commits(gitea, 7) == [Commit("a" * 40, "feat: x (#1)", 1)]


def test_retried_gives_up_after_the_last_attempt(monkeypatch):
    monkeypatch.setattr(gitea_module.time, "sleep", lambda seconds: None)
    operation = mock.Mock(side_effect=OSError("down"))
    with pytest.raises(OSError):
        gitea_module.retried(operation)
    assert operation.call_count == gitea_module.RETRY_ATTEMPTS


def test_error_message_reads_giteas_message():
    error = urllib.error.HTTPError("url", 405, "Method Not Allowed", hdrs=None,
                                   fp=io.BytesIO(b'{"message": "Please try again later"}'))
    assert gitea_module.error_message(error) == "Please try again later"


def test_total_count_reads_gitea_s_count_header(monkeypatch):
    seen = []

    def urlopen(request, timeout=None):
        seen.append(request.full_url)
        return Response([{}], {"X-Total-Count": "412"})

    monkeypatch.setattr(gitea_module.urllib.request, "urlopen", urlopen)
    client = gitea_module.Gitea("https://git.example", "org/repo", "secret")
    assert client.total_count("/commits?sha=abc&limit=1") == 412
    assert seen == ["https://git.example/api/v1/repos/org/repo/commits?sha=abc&limit=1"]


def test_total_count_without_the_header_is_an_error(monkeypatch):
    monkeypatch.setattr(gitea_module.urllib.request, "urlopen", lambda request, timeout=None: Response([{}]))
    with pytest.raises(LookupError, match="X-Total-Count"):
        gitea_module.Gitea("https://git.example", "org/repo", "").total_count("/commits?limit=1")
