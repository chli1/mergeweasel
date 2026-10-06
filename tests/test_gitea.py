import base64
import io
import json
import urllib.error
from unittest import mock

import pytest

from mergeweasel import gitea as gitea_module
from mergeweasel.commit_rule import Commit, SquashMessage


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


def client():
    return gitea_module.Gitea(SERVER, "org/repo", "secret")


def serve(monkeypatch, *answers):
    """Each urlopen takes the next answer: a payload, a Response, or an exception to raise."""
    seen, pending = [], list(answers)

    def urlopen(request, timeout=None):
        seen.append((request.get_method(), request.full_url, json.loads(request.data) if request.data else None))
        answer = pending.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, Response) else Response(answer)

    monkeypatch.setattr(gitea_module.urllib.request, "urlopen", urlopen)
    return seen


def http_error(code, message=None):
    body = json.dumps({"message": message}).encode() if message else b""
    return urllib.error.HTTPError(f"{API}/x", code, "Reason", hdrs=None, fp=io.BytesIO(body))


def test_own_login_reads_the_user_of_the_token(monkeypatch):
    seen = serve(monkeypatch, {"login": "weasel-bot"}, http_error(401, "token is expired"))
    assert client().own_login() == "weasel-bot"
    assert seen[0][1] == f"{SERVER}/api/v1/user"
    with pytest.raises(gitea_module.ApiError, match="token is expired"):
        client().own_login()


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(gitea_module.time, "sleep", sleeps.append)
    return sleeps


@pytest.mark.parametrize("error", [gitea_module.Unreachable("down"), gitea_module.ApiError("boom", 502)])
def test_retried_retries_network_errors_and_5xx_until_the_last_attempt(no_sleep, error):
    operation = mock.Mock(side_effect=error)
    with pytest.raises(type(error)):
        gitea_module.retried(operation)
    assert operation.call_count == gitea_module.RETRY_ATTEMPTS


def test_retried_returns_once_a_retry_succeeds(no_sleep):
    operation = mock.Mock(side_effect=[gitea_module.Unreachable("down"), "ok"])
    assert gitea_module.retried(operation) == "ok"
    assert no_sleep == [gitea_module.RETRY_DELAY_SECONDS]


@pytest.mark.parametrize("error", [gitea_module.ApiError("forbidden", 403), gitea_module.NotFound("gone", 404),
                                   KeyError("sha"), TypeError("bug")])
def test_retried_gives_up_at_once_on_a_4xx_or_a_bug(no_sleep, error):
    operation = mock.Mock(side_effect=error)
    with pytest.raises(type(error)):
        gitea_module.retried(operation)
    assert operation.call_count == 1 and no_sleep == []


def test_requests_carry_the_token_and_the_timeout(monkeypatch):
    seen = []

    def urlopen(request, timeout=None):
        seen.append((request.get_header("Authorization"), timeout))
        return Response({"default_branch": "main"})

    monkeypatch.setattr(gitea_module.urllib.request, "urlopen", urlopen)
    client().default_branch()
    gitea_module.Gitea(SERVER, "org/repo", "").default_branch()
    assert seen == [("token secret", gitea_module.HTTP_TIMEOUT_SECONDS), (None, gitea_module.HTTP_TIMEOUT_SECONDS)]


def test_lists_are_read_page_by_page(monkeypatch):
    seen = serve(monkeypatch, [{"name": str(n)} for n in range(50)], [{"name": "50"}])
    assert len(client().labels()) == 51
    assert [url for _, url, _ in seen] == [f"{API}/labels?limit=50&page=1", f"{API}/labels?limit=50&page=2"]


# --- errors ---

def test_an_http_error_carries_giteas_message_status_and_url(monkeypatch):
    serve(monkeypatch, http_error(403, "token does not have scope"))
    with pytest.raises(gitea_module.ApiError) as raised:
        client().default_branch()
    assert (raised.value.message, raised.value.status, raised.value.url) == ("token does not have scope", 403, API)
    assert str(raised.value) == "token does not have scope"


def test_without_giteas_message_the_reason_stands(monkeypatch):
    serve(monkeypatch, http_error(502))
    with pytest.raises(gitea_module.ApiError, match="^Reason$"):
        client().default_branch()


def test_a_404_is_not_found(monkeypatch):
    serve(monkeypatch, http_error(404, "not found"))
    with pytest.raises(gitea_module.NotFound):
        client().pull(3)


@pytest.mark.parametrize("error", [urllib.error.URLError("connection refused"), TimeoutError("timed out")])
def test_a_network_error_is_unreachable(monkeypatch, error):
    serve(monkeypatch, error)
    with pytest.raises(gitea_module.Unreachable) as raised:
        client().default_branch()
    assert raised.value.status is None


def test_a_malformed_header_hides_the_token(monkeypatch):
    serve(monkeypatch, ValueError("Invalid header value b'token secret-value\\n'"))
    with pytest.raises(gitea_module.ApiError) as raised:
        client().default_branch()
    assert "secret-value" not in raised.value.message
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_an_answer_that_is_not_json_is_an_api_error(monkeypatch):
    class Garbage(Response):
        def read(self):
            return b"<html>"
    serve(monkeypatch, Garbage(None))
    with pytest.raises(gitea_module.ApiError, match="not JSON"):
        client().default_branch()


# --- reads ---

def test_default_branch(monkeypatch):
    seen = serve(monkeypatch, {"default_branch": "trunk"})
    assert client().default_branch() == "trunk"
    assert seen == [("GET", API, None)]


def test_branch_with_and_without_status_checks(monkeypatch):
    raw = {"commit": {"id": "f" * 40}, "status_check_contexts": ["ci / *"], "required_approvals": 2}
    seen = serve(monkeypatch, {**raw, "enable_status_check": True}, {**raw, "enable_status_check": False})
    assert client().branch("main") == gitea_module.Branch("f" * 40, ("ci / *",), 2)
    assert client().branch("main") == gitea_module.Branch("f" * 40, (), 2)
    assert seen[0][1] == f"{API}/branches/main"


def test_branch_head_takes_the_exact_ref_among_prefix_matches(monkeypatch):
    seen = serve(monkeypatch, [{"ref": "refs/heads/renovate/gradle-8.x", "object": {"sha": "b" * 40}},
                               {"ref": "refs/heads/renovate/gradle", "object": {"sha": "c" * 40}}])
    assert client().branch_head("renovate/gradle") == "c" * 40
    assert seen[0][1] == f"{API}/git/refs/heads/renovate/gradle"


def test_branch_head_without_an_exact_ref_is_not_found(monkeypatch):
    serve(monkeypatch, [{"ref": "refs/heads/feature-2", "object": {"sha": "b" * 40}}])
    with pytest.raises(gitea_module.NotFound, match="refs/heads/feature not found"):
        client().branch_head("feature")


RAW_PULL = {"number": 5, "base": {"ref": "main"}, "head": {"ref": "feature", "sha": "5" * 40},
            "labels": [{"name": "automerge", "id": 13}], "draft": True, "user": {"login": "author"},
            "title": "feat: x (#1)", "body": None}
PULL = gitea_module.PullRequest(5, "main", "feature", "5" * 40, {"automerge": 13}, True, "author", "feat: x (#1)", "")


def test_open_pulls_and_pull(monkeypatch):
    seen = serve(monkeypatch, [RAW_PULL], RAW_PULL)
    assert client().open_pulls() == [PULL]
    assert client().pull(5) == PULL
    assert [url for _, url, _ in seen] == [f"{API}/pulls?state=open&limit=50&page=1", f"{API}/pulls/5"]


def test_statuses(monkeypatch):
    seen = serve(monkeypatch, {"statuses": [
        {"context": "ci", "status": "success", "target_url": "/run/1", "description": "ok", "creator": {"login": "bot"}},
        {"context": "x", "status": "pending", "target_url": None, "description": None, "creator": None}]},
        {"statuses": None})
    assert client().statuses("a" * 40) == [gitea_module.CommitStatus("ci", "success", "/run/1", "ok", "bot"),
                                           gitea_module.CommitStatus("x", "pending", "", "", None)]
    assert client().statuses("a" * 40) == []
    assert seen[0][1] == f"{API}/commits/{'a' * 40}/status?limit=50"


def test_is_behind(monkeypatch):
    seen = serve(monkeypatch, {"total_commits": 2}, {"total_commits": 0})
    assert client().is_behind("h", "b") and not client().is_behind("h", "b")
    assert seen[0][1] == f"{API}/compare/h...b"


def test_reviews(monkeypatch):
    serve(monkeypatch, [{"user": {"login": "chli1"}, "state": "APPROVED", "official": True, "dismissed": False,
                         "stale": False, "commit_id": "c" * 40}])
    assert client().reviews(5) == [gitea_module.Review("chli1", "APPROVED", True, False, False, "c" * 40)]


def test_timeline_decodes_pushes_and_label_events(monkeypatch):
    serve(monkeypatch, [
        {"id": 1, "type": "pull_push", "user": {"login": "renovate"}},
        {"id": 2, "type": "label", "body": "1", "user": {"login": "chli1"}, "label": {"name": "automerge"}},
        {"id": 3, "type": "label", "body": "", "user": {"login": "chli1"}, "label": {"name": "automerge"}},
        {"id": 4, "type": "comment", "user": {"login": "chli1"}}])
    assert client().timeline(5) == [
        gitea_module.TimelineEvent(1, gitea_module.PUSH, "renovate", None),
        gitea_module.TimelineEvent(2, gitea_module.LABEL_ADDED, "chli1", "automerge"),
        gitea_module.TimelineEvent(3, gitea_module.LABEL_REMOVED, "chli1", "automerge"),
        gitea_module.TimelineEvent(4, gitea_module.OTHER, "chli1", None)]


def test_pull_commits_and_issue_numbers(monkeypatch):
    seen = serve(monkeypatch, [{"sha": "a" * 40, "commit": {"message": "feat: x (#1)\n\nbody"}, "parents": [{}]}],
                 [{"number": 1}, {"number": 4}])
    assert client().pull_commits(7) == [Commit("a" * 40, "feat: x (#1)", 1)]
    assert client().issue_numbers() == {1, 4}
    assert [url for _, url, _ in seen] == [
        f"{API}/pulls/7/commits?stat=false&verification=false&files=false&limit=50&page=1",
        f"{API}/issues?state=all&type=issues&limit=50&page=1"]


def test_file(monkeypatch):
    seen = serve(monkeypatch, {"type": "file", "content": base64.b64encode(b"x = 1\n").decode()},
                 http_error(404), {"type": "dir"}, [{"type": "file"}], {"type": "symlink", "content": None})
    assert client().file("mergeweasel.toml", "main") == b"x = 1\n"
    assert client().file("mergeweasel.toml", "main") is None
    for _ in range(3):
        with pytest.raises(gitea_module.NotAFile, match="^mergeweasel.toml: not a file$"):
            client().file("mergeweasel.toml", "main")
    assert seen[0][1] == f"{API}/contents/mergeweasel.toml?ref=main"


@pytest.mark.parametrize("content", [None, "not base64!"])
def test_a_file_without_readable_content_is_not_a_file(monkeypatch, content):
    serve(monkeypatch, {"type": "file", "content": content})
    with pytest.raises(gitea_module.NotAFile, match="^mergeweasel.toml: cannot read its content$"):
        client().file("mergeweasel.toml", "main")


def test_tag_refs_and_tag_commit(monkeypatch):
    seen = serve(monkeypatch, [{"ref": "refs/tags/v1.0.0", "object": {"type": "commit", "sha": "a" * 40}},
                               {"ref": "refs/tags/v2.0.0", "object": {"type": "tag", "sha": "b" * 40}}],
                 http_error(404), {"object": {"sha": "c" * 40}})
    assert client().tag_refs() == [gitea_module.TagRef("v1.0.0", "a" * 40, False),
                                   gitea_module.TagRef("v2.0.0", "b" * 40, True)]
    assert client().tag_refs() == []
    assert client().tag_commit("b" * 40) == "c" * 40
    assert [url for _, url, _ in seen] == [f"{API}/git/refs/tags", f"{API}/git/refs/tags", f"{API}/git/tags/{'b' * 40}"]


def test_commits_reads_the_next_page_only_when_needed(monkeypatch):
    page = [{"sha": f"{n:040x}", "commit": {"message": "fix: x (#1)"}} for n in range(50)]
    seen = serve(monkeypatch, page, page[:1])
    commits = client().commits("main")
    assert next(commits) == gitea_module.HistoryCommit(f"{0:040x}", "fix: x (#1)")
    assert len(seen) == 1
    assert len(list(commits)) == 50
    assert [url for _, url, _ in seen] == [
        f"{API}/commits?sha=main&stat=false&verification=false&files=false&limit=50&page={n}" for n in (1, 2)]


def test_commit_count_reads_giteas_count_header(monkeypatch):
    seen = serve(monkeypatch, Response([{}], {"X-Total-Count": "412"}), Response([{}]))
    assert client().commit_count("abc") == 412
    assert seen[0][1] == f"{API}/commits?sha=abc&stat=false&verification=false&files=false&limit=1"
    with pytest.raises(gitea_module.ApiError, match="X-Total-Count"):
        client().commit_count("abc")


def test_labels_and_org_labels(monkeypatch):
    seen = serve(monkeypatch, [{"name": "automerge"}], [{"name": "needs-fix"}])
    assert client().labels() == ["automerge"]
    assert client().org_labels() == ["needs-fix"]
    assert [url for _, url, _ in seen] == [f"{API}/labels?limit=50&page=1",
                                           f"{SERVER}/api/v1/orgs/org/labels?limit=50&page=1"]


@pytest.mark.parametrize("code", [403, 404])
def test_org_labels_unreadable_is_none(monkeypatch, code):
    serve(monkeypatch, http_error(code))
    assert client().org_labels() is None


def test_org_labels_other_errors_raise(monkeypatch):
    serve(monkeypatch, http_error(500))
    with pytest.raises(gitea_module.ApiError):
        client().org_labels()


def test_workflow_runs(monkeypatch):
    seen = serve(monkeypatch, {"workflow_runs": [{"id": 7, "status": "queued", "event": "push", "x": 1}]})
    assert client().workflow_runs("q.yml") == [gitea_module.WorkflowRun(7, "queued", "push")]
    assert seen[0][1] == f"{API}/actions/workflows/q.yml/runs?limit=50"


# --- writes ---

def test_writes_send_their_request(monkeypatch):
    seen = serve(monkeypatch, *[None] * 7)
    gitea = client()
    gitea.rebase(3)
    gitea.approve(3, "h" * 40, "Approval of chli1.")
    gitea.add_label(3, "needs-fix")
    gitea.remove_label(3, 14)
    gitea.comment(3, "mergeweasel: red")
    gitea.set_status("a" * 40, "mergeweasel/approval", "success", "approved at abc")
    gitea.create_release("v1.0.1", "c" * 40, "## Fixes")
    assert seen == [
        ("POST", f"{API}/pulls/3/update?style=rebase", None),
        ("POST", f"{API}/pulls/3/reviews", {"event": "APPROVED", "commit_id": "h" * 40, "body": "Approval of chli1."}),
        ("POST", f"{API}/issues/3/labels", {"labels": ["needs-fix"]}),
        ("DELETE", f"{API}/issues/3/labels/14", None),
        ("POST", f"{API}/issues/3/comments", {"body": "mergeweasel: red"}),
        ("POST", f"{API}/statuses/{'a' * 40}", {"state": "success", "context": "mergeweasel/approval",
                                                "description": "approved at abc"}),
        ("POST", f"{API}/releases", {"tag_name": "v1.0.1", "target_commitish": "c" * 40, "name": "v1.0.1",
                                     "body": "## Fixes"})]


def test_merge_squashes_with_the_message_or_fast_forwards(monkeypatch):
    seen = serve(monkeypatch, None, None)
    client().merge(6, "h" * 40, SquashMessage("ci: land (#1)", "Why.", ()))
    client().merge(6, "h" * 40, None)
    assert seen == [
        ("POST", f"{API}/pulls/6/merge", {"Do": "squash", "MergeTitleField": "ci: land (#1)",
                                          "MergeMessageField": "Why.", "head_commit_id": "h" * 40}),
        ("POST", f"{API}/pulls/6/merge", {"Do": "fast-forward-only", "head_commit_id": "h" * 40})]


@pytest.mark.parametrize("code, message, kind", [
    (405, "not mergeable", gitea_module.MergeRefused),
    (405, "Please try again later", gitea_module.MergeDeferred),
    (409, "head moved", gitea_module.MergeDeferred),
    (500, "boom", gitea_module.ApiError),
])
def test_merge_errors(monkeypatch, code, message, kind):
    serve(monkeypatch, http_error(code, message))
    with pytest.raises(kind) as raised:
        client().merge(6, "h" * 40, None)
    assert type(raised.value) is kind and raised.value.message == message


def test_a_conflicting_rebase_and_an_existing_release(monkeypatch):
    serve(monkeypatch, http_error(409, "conflict"), http_error(409, "tag exists"), http_error(500, "boom"))
    with pytest.raises(gitea_module.Conflict):
        client().rebase(3)
    with pytest.raises(gitea_module.ReleaseExists):
        client().create_release("v1", "c" * 40, "")
    with pytest.raises(gitea_module.ApiError) as raised:
        client().rebase(3)
    assert type(raised.value) is gitea_module.ApiError
