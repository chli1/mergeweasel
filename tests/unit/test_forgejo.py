import urllib.error
import urllib.request

import pytest
from fake_http import API, SERVER, Response, http_error, serve

from mergeweasel.api import ApiError, WorkflowRun
from mergeweasel.forgejo import Forgejo, detect
from mergeweasel.gitea import Gitea


def test_workflow_runs_name_the_trigger_not_the_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    # Forgejo's event is the webhook's: push for a scheduled run, empty for a dispatch.
    seen = serve(
        monkeypatch,
        {
            "total_count": 2,
            "workflow_runs": [
                {"id": 8, "status": "waiting", "event": "push", "trigger_event": "schedule"},
                {"id": 7, "status": "success", "event": "", "trigger_event": "workflow_dispatch"},
            ],
        },
    )
    forgejo = Forgejo(SERVER, "org/repo", "secret")
    assert forgejo.workflow_runs("q.yml") == [
        WorkflowRun(8, False, "schedule"),
        WorkflowRun(7, True, "workflow_dispatch"),
    ]
    # page: without it, Forgejo ignores limit and reads the workflow's whole history.
    assert seen[0][1] == f"{API}/actions/runs?workflow_id=q.yml&limit=50&page=1"


@pytest.mark.parametrize(
    "status, finished",
    [
        ("success", True),
        ("failure", True),
        ("cancelled", True),
        ("skipped", True),
        ("waiting", False),
        ("running", False),
        ("blocked", False),
        ("unknown", False),
        ("someday", False),
    ],
)
def test_run_states(monkeypatch: pytest.MonkeyPatch, status: str, finished: bool) -> None:
    serve(monkeypatch, {"workflow_runs": [{"id": 1, "status": status, "event": "", "trigger_event": "schedule"}]})
    assert Forgejo(SERVER, "org/repo", "").workflow_runs("q.yml")[0].finished is finished


def test_remove_label_names_the_label(monkeypatch: pytest.MonkeyPatch) -> None:
    # An organization label's id answers 422 on Forgejo; its name resolves.
    seen = serve(monkeypatch, None)
    Forgejo(SERVER, "org/repo", "secret").remove_label(3, "kind/needs fix", 12)
    assert seen == [("DELETE", f"{API}/issues/3/labels/kind%2Fneeds%20fix", None)]


def test_detect_finds_forgejo_by_its_version_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = serve(monkeypatch, {"version": "16.0.6+gitea-1.22.0"})
    assert detect(SERVER, "secret") is Forgejo
    assert seen == [("GET", f"{SERVER}/api/forgejo/v1/version", None)]


def test_detect_takes_a_404_for_gitea(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, http_error(404))
    assert detect(SERVER, "secret") is Gitea


@pytest.mark.parametrize("answer", [Response(None), {"message": "sign in"}, ["16.0.6"]])
def test_detect_takes_an_answer_without_a_version_for_gitea(monkeypatch: pytest.MonkeyPatch, answer: object) -> None:
    # A proxy in front of Gitea may answer an unknown path with a page of its own.
    serve(monkeypatch, answer)
    assert detect(SERVER, "secret") is Gitea


@pytest.mark.parametrize(
    "error", [http_error(401, "token is expired"), http_error(502), urllib.error.URLError("connection refused")]
)
def test_detect_raises_any_other_answer(monkeypatch: pytest.MonkeyPatch, error: OSError) -> None:
    serve(monkeypatch, error)
    with pytest.raises(ApiError):
        detect(SERVER, "secret")


def test_detect_sends_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # An instance that requires sign-in answers its version only to a token.
    headers: list[str | None] = []

    def urlopen(request: urllib.request.Request, timeout: float | None = None) -> Response:
        headers.append(request.get_header("Authorization"))
        return Response({"version": "16.0.6"})

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    detect(SERVER, "secret")
    assert headers == ["token secret"]
