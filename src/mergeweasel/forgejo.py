"""The Forgejo API client, and telling Forgejo from Gitea."""
import json

from mergeweasel.api import PAGE_SIZE, Client, NotFound, WorkflowRun, exchange
from mergeweasel.gitea import Gitea

# Forgejo's run states that change no more; any other, known or not, holds the turn.
FINISHED = frozenset({"success", "failure", "cancelled", "skipped"})
# Forgejo answers it, Gitea does not know it.
VERSION_PATH = "/api/forgejo/v1/version"


class Forgejo(Client):
    def workflow_runs(self, file):
        # page: without it, Forgejo ignores limit and reads the workflow's whole history.
        raw = self._json("GET", f"/actions/runs?workflow_id={file}&limit={PAGE_SIZE}&page=1")
        # trigger_event: event is the webhook's - push for a scheduled run, empty for a dispatch.
        return [WorkflowRun(run["id"], run["status"] in FINISHED, run["trigger_event"])
                for run in raw["workflow_runs"]]


def detect(server, token):
    """The client class for the forge at server: Forgejo if it names its version."""
    try:
        payload, _ = exchange("GET", f"{server}{VERSION_PATH}", token)
    except NotFound:
        return Gitea
    try:
        answer = json.loads(payload)
    except ValueError:
        return Gitea
    # A proxy in front of Gitea may answer an unknown path with a page of its own.
    return Forgejo if isinstance(answer, dict) and "version" in answer else Gitea
