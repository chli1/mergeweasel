"""The forge the Docker layers pick, and what tells the forges apart. No forge needed."""

from typing import Any

import forge
import pytest
import workflow
import yaml


def test_gitea_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(forge.FORGE_VARIABLE, raising=False)
    assert forge.selected() is forge.FORGES["gitea"]


def test_the_variable_picks_the_forge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(forge.FORGE_VARIABLE, "forgejo")
    assert forge.selected() is forge.FORGES["forgejo"]


def test_an_unknown_forge_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(forge.FORGE_VARIABLE, "github")
    with pytest.raises(pytest.fail.Exception, match="names no forge - one of: gitea, forgejo"):
        forge.selected()


def test_gitea_appends_the_co_author_trailer() -> None:
    assert forge.FORGES["gitea"].squash_message("feat: a (#1)\n\n- a\n", "x <x@y>") == (
        "feat: a (#1)\n\n- a\n\nCo-authored-by: x <x@y>\n"
    )


def test_forgejo_appends_nothing() -> None:
    assert forge.FORGES["forgejo"].squash_message("feat: a (#1)\n\n- a\n", "x <x@y>") == "feat: a (#1)\n\n- a\n"


def queue_triggers(text: str) -> dict[str, Any]:
    # YAML 1.1 reads the key on as True.
    triggers: dict[str, Any] = yaml.safe_load(text)[True]
    return triggers


def test_gitea_runs_the_queue_workflow_as_published() -> None:
    text = workflow.QUEUE_WORKFLOW.read_text()
    assert forge.FORGES["gitea"].queue_workflow(text) == text


def test_forgejo_runs_its_queue_workflow_with_the_test_schedule() -> None:
    text = workflow.FORGEJO_QUEUE_WORKFLOW.read_text()
    triggers = queue_triggers(forge.FORGES["forgejo"].queue_workflow(text))
    assert triggers["schedule"] == [{"cron": "@every 5s"}]
    assert set(triggers) == set(queue_triggers(text))


def test_forgejo_fails_on_a_file_without_one_cron_line() -> None:
    with pytest.raises(pytest.fail.Exception, match="needs one cron line to replace, has 0"):
        forge.FORGES["forgejo"].queue_workflow(workflow.QUEUE_WORKFLOW.read_text())
