"""Serializing the queue passes without a concurrency group, whose replaced runs Gitea reports as failed."""

import dataclasses
import time
from collections.abc import Iterable

from mergeweasel.api import Client, WorkflowRun

QUEUE_WORKFLOW_FILE = "mergeweasel-queue.yml"
# A manual dispatch can be a dry run, which must neither replace an event's pass nor be replaced.
DISPATCH_EVENT = "workflow_dispatch"
# The runs that never replace an older pass: a dispatch, and a schedule's tick - Forgejo creates the next
# tick when it is due, even while the previous one still waits for a runner, so ticks replacing each
# other would leave no pass to run as long as the runner is busy.
NO_REPLACING = frozenset({DISPATCH_EVENT, "schedule"})
TURN_POLL_SECONDS = 5


@dataclasses.dataclass(frozen=True)
class OwnTurn:
    pass


@dataclasses.dataclass(frozen=True)
class AfterOlder:
    run_id: int


@dataclasses.dataclass(frozen=True)
class Superseded:
    run_id: int


def turn(runs: Iterable[WorkflowRun], own_id: int, own_event: str) -> OwnTurn | AfterOlder | Superseded:
    """A newer pass reads a newer state, so an event's pass leaves to a newer event's pass; otherwise
    it waits until every older pass has finished.
    """
    unfinished = [run for run in runs if not run.finished and run.id != own_id]
    newer = [run.id for run in unfinished if run.id > own_id and run.event not in NO_REPLACING]
    if newer and own_event != DISPATCH_EVENT:
        return Superseded(max(newer))
    older = [run.id for run in unfinished if run.id < own_id]
    if older:
        return AfterOlder(max(older))
    return OwnTurn()


def wait_for_turn(gitea: Client, own_id: int, own_event: str) -> bool:
    """True once this pass may run, False if a newer pass takes over."""
    while True:
        # One page: the newest runs come first, and an unfinished one is never 50 runs old.
        position = turn(gitea.workflow_runs(QUEUE_WORKFLOW_FILE), own_id, own_event)
        if isinstance(position, OwnTurn):
            return True
        if isinstance(position, Superseded):
            print(f"superseded by run {position.run_id}")
            return False
        print(f"waiting for run {position.run_id}")
        time.sleep(TURN_POLL_SECONDS)
