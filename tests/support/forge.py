"""The forges the Docker layers run against: each forge's own behaviour, in its own class."""
import abc
import os

import pytest

# Picks the forge; Gitea when unset.
FORGE_VARIABLE = "MERGEWEASEL_E2E_FORGE"
# What a Forgejo user adds to mergeweasel-queue.yml: Forgejo has no workflow_run, a schedule wakes
# the queue. Forgejo fires it about 60 s after the previous run at the earliest; with the schedule
# checker forgejo.Dockerfile sets, every 5 s plus that.
SCHEDULE = '  schedule:\n    - cron: "@every 5s"\n'


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

    @abc.abstractmethod
    def squash_message(self, message, co_author):
        """The message a squash merge lands, for `message` written by `co_author`, not the merger."""

    @abc.abstractmethod
    def queue_workflow(self, text):
        """mergeweasel-queue.yml as a user of this forge sets it up."""


class Gitea(Forge):
    name = "gitea"
    title = "Gitea"
    dockerfile = "gitea.Dockerfile"
    runner_dockerfile = "gitea-runner.Dockerfile"
    runner_command = "act_runner"
    job_prefix = "GITEA-ACTIONS-TASK"
    # The label's commit check wakes the queue on completion, within seconds.
    ci_slow_seconds = 10

    def squash_message(self, message, co_author):
        # ADD_CO_COMMITTER_TRAILERS, on by default.
        return f"{message}\nCo-authored-by: {co_author}\n"

    def queue_workflow(self, text):
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

    def squash_message(self, message, co_author):
        return message

    def queue_workflow(self, text):
        if "\non:\n" not in text:
            pytest.fail("mergeweasel-queue.yml has no on: block to add the schedule to")
        return text.replace("\non:\n", f"\non:\n{SCHEDULE}", 1)


FORGES = {forge.name: forge for forge in (Gitea(), Forgejo())}


def selected():
    name = os.environ.get(FORGE_VARIABLE, "gitea")
    if name not in FORGES:
        pytest.fail(f"{FORGE_VARIABLE}={name} names no forge - one of: {', '.join(FORGES)}")
    return FORGES[name]
