"""The Gitea API client."""
from mergeweasel.api import PAGE_SIZE, Client, WorkflowRun


class Gitea(Client):
    def workflow_runs(self, file):
        raw = self._json("GET", f"/actions/workflows/{file}/runs?limit={PAGE_SIZE}&page=1")
        return [WorkflowRun(run["id"], run["status"] == "completed", run["event"]) for run in raw["workflow_runs"]]
