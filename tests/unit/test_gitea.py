from fake_http import API, SERVER, serve

from mergeweasel.api import WorkflowRun
from mergeweasel.gitea import Gitea


def test_workflow_runs(monkeypatch):
    seen = serve(
        monkeypatch,
        {
            "workflow_runs": [
                {"id": 8, "status": "completed", "event": "workflow_dispatch"},
                {"id": 7, "status": "queued", "event": "push", "x": 1},
            ]
        },
    )
    runs = Gitea(SERVER, "org/repo", "secret").workflow_runs("q.yml")
    assert runs == [WorkflowRun(8, True, "workflow_dispatch"), WorkflowRun(7, False, "push")]
    assert seen[0][1] == f"{API}/actions/workflows/q.yml/runs?limit=50&page=1"
