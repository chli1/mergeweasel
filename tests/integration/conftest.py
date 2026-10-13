"""The integration layer: one pass at a time, started by the test, against a real Gitea."""
import os
import subprocess

import pytest

import workflow
from driver import eventually
from server import LABEL

PASS_TIMEOUT_SECONDS = 300


@pytest.fixture
def pass_workflow(workflow_text):
    """What mergeweasel-queue.yml hands a pass, as the variant runs it."""
    return workflow.parse(workflow_text("mergeweasel-queue.yml"))


@pytest.fixture
def run_pass(server, image, pass_workflow, repo, instance):
    def run(mergeable=None, approvers=("queue",)):
        """One pass, with the environment and the command mergeweasel-queue.yml gives it.

        No GITHUB_RUN_ID: there is no other pass to wait for. `mergeable` names a pull request whose
        mergeable check must settle first - otherwise Gitea defers the merge to a later pass.
        `approvers` default: the merger's own token, one account in both roles.
        """
        if mergeable is not None:
            eventually(lambda: repo.pull(mergeable)["mergeable"])
        automerge, needs_fix, keep_commits, prio = instance[1]
        # The repository's Actions variables and secrets; the default branch is the repository's own.
        contexts = {
            "vars": {"MERGEWEASEL_AUTOMERGE_LABEL": automerge, "MERGEWEASEL_NEEDS_FIX_LABEL": needs_fix,
                     "MERGEWEASEL_KEEP_COMMITS_LABEL": keep_commits, "MERGEWEASEL_PRIO_LABEL": prio,
                     "MERGEWEASEL_RENOVATE_ACCOUNT": "renovate"},
            "secrets": {"MERGEWEASEL_MERGER_TOKEN": server.tokens["queue"],
                        "MERGEWEASEL_APPROVER_TOKENS": "\n".join(server.tokens[account] for account in approvers)},
        }
        # The runner's context, which the workflow does not spell out.
        passed = {"GITHUB_SERVER_URL": server.url, "GITHUB_REPOSITORY": repo.name, **pass_workflow.resolve(contexts)}
        # By name only: the tokens stay off the command line.
        options = [option for name in passed for option in ("--env", name)]
        result = subprocess.run(
            ["docker", "run", "--rm", "--label", LABEL, *options, image, *pass_workflow.command],
            env={**os.environ, **passed}, capture_output=True, text=True, timeout=PASS_TIMEOUT_SECONDS)
        # pytest shows it with a failing test: what the pass decided.
        print(f"pass:\n{result.stdout}{result.stderr}")
        assert result.returncode == 0, "the pass failed"
        return result.stdout
    return run
