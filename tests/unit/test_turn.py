from unittest import mock

import pytest

from mergeweasel import turn
from mergeweasel.gitea import Gitea, WorkflowRun


def run(run_id, status="in_progress", event="workflow_run"):
    return WorkflowRun(run_id, status, event)


@pytest.fixture
def gitea():
    return mock.create_autospec(Gitea, instance=True)


def test_alone_its_the_passes_turn():
    assert turn.turn([run(7), run(6, "completed")], 7, "workflow_run") == turn.OwnTurn()


def test_an_unfinished_older_pass_goes_first():
    runs = [run(7), run(6, "queued"), run(5, "waiting")]
    assert turn.turn(runs, 7, "push") == turn.AfterOlder(6)


def test_a_newer_events_pass_supersedes():
    runs = [run(9, "queued"), run(8), run(7), run(6)]
    assert turn.turn(runs, 7, "pull_request_target") == turn.Superseded(9)


def test_a_finished_newer_pass_supersedes_nothing():
    assert turn.turn([run(8, "completed"), run(7)], 7, "workflow_run") == turn.OwnTurn()


def test_a_dispatch_neither_supersedes_nor_is_superseded():
    assert turn.turn([run(8, event="workflow_dispatch"), run(7)], 7, "workflow_run") == turn.OwnTurn()
    runs = [run(9), run(8, event="workflow_dispatch"), run(7)]
    assert turn.turn(runs, 8, "workflow_dispatch") == turn.AfterOlder(7)


def test_wait_for_turn_polls_until_the_older_pass_finished(monkeypatch, gitea):
    sleeps = []
    monkeypatch.setattr(turn.time, "sleep", sleeps.append)
    gitea.workflow_runs.side_effect = [[run(7), run(6)], [run(7), run(6, "completed")]]
    assert turn.wait_for_turn(gitea, 7, "workflow_run")
    assert sleeps == [turn.TURN_POLL_SECONDS]
    gitea.workflow_runs.assert_called_with("mergeweasel-queue.yml")


def test_wait_for_turn_leaves_to_a_newer_pass_while_waiting(monkeypatch, gitea):
    monkeypatch.setattr(turn.time, "sleep", lambda seconds: None)
    gitea.workflow_runs.side_effect = [[run(7), run(6)], [run(8, "queued"), run(7), run(6)]]
    assert not turn.wait_for_turn(gitea, 7, "workflow_run")
