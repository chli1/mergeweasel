"""A job's container reaches the forge and knows its run - what every scenario builds on."""

from actions import settle
from driver import REQUIRED_CONTEXT, Repo, create_repo
from runner import Runner
from server import Server

# Posts GITHUB_RUN_ID as a status: a pass compares it with the run listing's ids to find its turn.
PROBE = """name: probe
on: [push]
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      # Passed on, as the reference workflows do: act_runner hands the token only to expressions.
      - env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          python3 - <<'EOF'
          import json, os, urllib.request
          env = os.environ
          status = {"state": "success", "context": "probe/run-id", "description": env["GITHUB_RUN_ID"]}
          request = urllib.request.Request(
              f"{env['GITHUB_SERVER_URL']}/api/v1/repos/{env['GITHUB_REPOSITORY']}/statuses/{env['GITHUB_SHA']}",
              data=json.dumps(status).encode(), method="POST",
              headers={"Authorization": f"token {env['GITHUB_TOKEN']}", "Content-Type": "application/json"})
          urllib.request.urlopen(request)
          EOF
"""
TRIVIAL = 'on: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: "true"\n'


def finished(repo: Repo) -> list[tuple[str, str, str]] | None:
    runs = repo.runs()
    return runs if runs and all(status in ("success", "failure") for _, _, status in runs) else None


def test_a_job_reaches_the_forge_and_knows_its_run(server: Server, repo: Repo, runner: Runner) -> None:
    # A run in another repository first: the probe's run id then differs from its number, 1.
    elsewhere = create_repo(server.clients, "probe-elsewhere", contexts=(REQUIRED_CONTEXT,))
    elsewhere.move_main({".gitea/workflows/trivial.yml": TRIVIAL}, "ci: trivial")
    settle(elsewhere, runner, lambda: finished(elsewhere), timeout=180)
    repo.move_main({".gitea/workflows/probe.yml": PROBE}, "ci: probe")

    runs = settle(repo, runner, lambda: finished(repo), timeout=180)

    assert runs[0] == ("probe.yml", "push", "success")
    # The offset keeps the job containers' names apart from other runs' on the same daemon.
    assert all(str(task).startswith(server.task_prefix) for task in repo.task_ids()), repo.task_ids()
    # Both forges list a repository's runs under /actions/runs, each with its id.
    listed = [run["id"] for run in repo.read("/actions/runs?limit=50&page=1")["workflow_runs"]]
    posted = [status["description"] for status in repo.statuses(repo.main_sha()) if status["context"] == "probe/run-id"]
    # The repository's only run, number 1 - its id is not 1: a run elsewhere came first.
    assert len(listed) == 1 and listed[0] != 1, listed
    assert posted == [str(listed[0])]
