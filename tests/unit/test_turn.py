import time
from typing import Any
from unittest import mock

import pytest

from mergeweasel import turn
from mergeweasel.api import WorkflowRun
from mergeweasel.gitea import Gitea


def run(run_id: int, finished: bool = False, event: str = "workflow_run") -> WorkflowRun:
    return WorkflowRun(run_id, finished, event)


@pytest.fixture
def gitea() -> Any:
    return mock.create_autospec(Gitea, instance=True)


def test_alone_its_the_passes_turn() -> None:
    assert turn.turn([run(7), run(6, finished=True)], 7, "workflow_run") == turn.OwnTurn()


def test_an_unfinished_older_pass_goes_first() -> None:
    runs = [run(7), run(6), run(5)]
    assert turn.turn(runs, 7, "push") == turn.AfterOlder(6)


def test_a_newer_events_pass_supersedes() -> None:
    runs = [run(9), run(8), run(7), run(6)]
    assert turn.turn(runs, 7, "pull_request_target") == turn.Superseded(9)


def test_a_finished_newer_pass_supersedes_nothing() -> None:
    assert turn.turn([run(8, finished=True), run(7)], 7, "workflow_run") == turn.OwnTurn()


def test_a_scheduled_pass_supersedes_nothing_and_leaves_to_a_newer_event() -> None:
    assert turn.turn([run(8, event="schedule"), run(7)], 7, "workflow_run") == turn.OwnTurn()
    assert turn.turn([run(8), run(7, event="schedule")], 7, "schedule") == turn.Superseded(8)


def test_ticks_piling_up_behind_a_busy_runner_each_take_their_turn() -> None:
    # Forgejo creates a scheduled run when it is due, whether or not the previous one has run yet.
    ticks = [run(9, event="schedule"), run(8, event="schedule"), run(7, event="schedule")]
    assert turn.turn(ticks, 7, "schedule") == turn.OwnTurn()
    assert turn.turn(ticks, 8, "schedule") == turn.AfterOlder(7)


def test_a_dispatch_neither_supersedes_nor_is_superseded() -> None:
    assert turn.turn([run(8, event="workflow_dispatch"), run(7)], 7, "workflow_run") == turn.OwnTurn()
    runs = [run(9), run(8, event="workflow_dispatch"), run(7)]
    assert turn.turn(runs, 8, "workflow_dispatch") == turn.AfterOlder(7)


def test_wait_for_turn_polls_until_the_older_pass_finished(monkeypatch: pytest.MonkeyPatch, gitea: Any) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    gitea.workflow_runs.side_effect = [[run(7), run(6)], [run(7), run(6, finished=True)]]
    assert turn.wait_for_turn(gitea, 7, "workflow_run")
    assert sleeps == [turn.TURN_POLL_SECONDS]
    gitea.workflow_runs.assert_called_with("mergeweasel-queue.yml")


def test_wait_for_turn_leaves_to_a_newer_pass_while_waiting(monkeypatch: pytest.MonkeyPatch, gitea: Any) -> None:
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    gitea.workflow_runs.side_effect = [[run(7), run(6)], [run(8), run(7), run(6)]]
    assert not turn.wait_for_turn(gitea, 7, "workflow_run")
