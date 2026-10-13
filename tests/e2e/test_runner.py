"""A job's container reaches Gitea - what every scenario builds on."""
from driver import eventually

PROBE = """name: probe
on: [push]
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - run: python3 -c "import os, urllib.request; urllib.request.urlopen(os.environ['GITHUB_SERVER_URL'] + '/api/healthz')"
"""


def test_a_job_reaches_gitea(server, repo, runner):
    repo.move_main({".gitea/workflows/probe.yml": PROBE}, "ci: probe")

    def finished():
        runs = repo.runs()
        return runs if runs and runs[0][2] in ("success", "failure") else None

    try:
        runs = eventually(finished, timeout=180, interval=2)
    except AssertionError:
        print(runner.log())
        raise
    assert runs[0] == ("probe.yml", "push", "success")
    # The offset keeps the job containers' names apart from other runs' on the same daemon.
    assert all(str(task).startswith(server.task_prefix) for task in repo.task_ids()), repo.task_ids()
