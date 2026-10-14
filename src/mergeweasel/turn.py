"""Serializing the queue passes without a concurrency group, whose replaced runs Gitea reports as failed."""
import dataclasses
import time

QUEUE_WORKFLOW_FILE = "mergeweasel-queue.yml"
# A manual dispatch can be a dry run, which must neither replace an event's pass nor be replaced.
DISPATCH_EVENT = "workflow_dispatch"
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


def turn(runs, own_id, own_event):
    """A newer pass reads a newer state, so an event's pass leaves to a newer event's pass; otherwise
    it waits until every older pass has finished.
    """
    unfinished = [run for run in runs if not run.finished and run.id != own_id]
    newer = [run.id for run in unfinished if run.id > own_id and run.event != DISPATCH_EVENT]
    if newer and own_event != DISPATCH_EVENT:
        return Superseded(max(newer))
    older = [run.id for run in unfinished if run.id < own_id]
    if older:
        return AfterOlder(max(older))
    return OwnTurn()


def wait_for_turn(gitea, own_id, own_event):
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
