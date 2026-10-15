"""Waiting for what the forge's workflows do, and saying what they did when it does not happen."""

from collections.abc import Callable
from typing import Literal

from driver import Repo, eventually
from runner import Runner

CI_CONTEXT = "ci / test (pull_request)"
CHECK_CONTEXT = "mergeweasel-commits / check (pull_request)"
TIMEOUT_SECONDS = 300


def settle[T](
    repo: Repo, runner: Runner, probe: Callable[[], T | Literal[False] | None], timeout: float = TIMEOUT_SECONDS
) -> T:
    """probe's first truthy result; on a timeout the runs and the runner's log go into the failure."""
    try:
        return eventually(probe, timeout=timeout, interval=2)
    except AssertionError as error:
        runs = "\n".join(f"  {workflow} {event} {status}" for workflow, event, status in repo.runs())
        raise AssertionError(f"{error}\nruns, newest first:\n{runs}\nrunner:\n{runner.log()}") from None
