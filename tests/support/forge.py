"""The forges the Docker layers run against: each forge's own behaviour, in its own class."""

import abc
import os
import pathlib
import re

import pytest
import workflow

# Picks the forge; Gitea when unset.
FORGE_VARIABLE = "MERGEWEASEL_E2E_FORGE"
# The schedule the test gives Forgejo's queue in place of the shipped one: Forgejo fires it about 60 s
# after the previous run at the earliest; with the schedule checker forgejo.Dockerfile sets, every 5 s
# plus that.
TEST_CRON = '- cron: "@every 5s"'
CRON = re.compile(r"^(\s*)- cron: .*$", re.MULTILINE)


class Forge(abc.ABC):
    # The value of FORGE_VARIABLE, and part of the Docker names.
    name: str
    title: str
    # Next to this file.
    dockerfile: str
    # The runner next to the forge: its Dockerfile, next to this file, and its command.
    runner_dockerfile: str
    runner_command: str
    # The runner names a job's container <job_prefix>-<task id>_WORKFLOW-..._JOB-...
    job_prefix: str
    # How long the test repository's CI runs on ci-slow: longer than a pass takes to come, so one
    # finds CI still running.
    ci_slow_seconds: int
    # The mergeweasel-queue.yml a user of this forge takes from the release.
    queue_source: pathlib.Path
    # Where a user of this forge puts the workflow files.
    workflow_directory: str

    @abc.abstractmethod
    def squash_message(self, message: str, co_author: str) -> str:
        """The message a squash merge lands, for `message` written by `co_author`, not the merger."""

    @abc.abstractmethod
    def queue_workflow(self, text: str) -> str:
        """The queue_source text as the test runs it."""


class Gitea(Forge):
    name = "gitea"
    title = "Gitea"
    dockerfile = "gitea.Dockerfile"
    runner_dockerfile = "gitea-runner.Dockerfile"
    runner_command = "act_runner"
    job_prefix = "GITEA-ACTIONS-TASK"
    # The label's commit check wakes the queue on completion, within seconds - more of them when
    # several CI runs share the host.
    ci_slow_seconds = 30
    queue_source = workflow.QUEUE_WORKFLOW
    workflow_directory = ".gitea/workflows"

    def squash_message(self, message: str, co_author: str) -> str:
        # ADD_CO_COMMITTER_TRAILERS, on by default.
        return f"{message}\nCo-authored-by: {co_author}\n"

    def queue_workflow(self, text: str) -> str:
        return text


class Forgejo(Forge):
    name = "forgejo"
    title = "Forgejo"
    dockerfile = "forgejo.Dockerfile"
    runner_dockerfile = "forgejo-runner.Dockerfile"
    runner_command = "forgejo-runner"
    job_prefix = "FORGEJO-ACTIONS-TASK"
    # Only the schedule wakes the queue, every 65 s.
    ci_slow_seconds = 90
    queue_source = workflow.FORGEJO_QUEUE_WORKFLOW
    workflow_directory = ".forgejo/workflows"

    def squash_message(self, message: str, co_author: str) -> str:
        return message

    def queue_workflow(self, text: str) -> str:
        crons = CRON.findall(text)
        if len(crons) != 1:
            pytest.fail(f"Forgejo's mergeweasel-queue.yml needs one cron line to replace, has {len(crons)}")
        return CRON.sub(lambda match: match.group(1) + TEST_CRON, text)


FORGES = {forge.name: forge for forge in (Gitea(), Forgejo())}


def selected() -> Forge:
    name = os.environ.get(FORGE_VARIABLE, "gitea")
    if name not in FORGES:
        pytest.fail(f"{FORGE_VARIABLE}={name} names no forge - one of: {', '.join(FORGES)}")
    return FORGES[name]
