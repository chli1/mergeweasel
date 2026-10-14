"""The forge the Docker layers pick, and what tells the forges apart. No forge needed."""
import pytest
import yaml

import forge
import workflow


def test_gitea_by_default(monkeypatch):
    monkeypatch.delenv(forge.FORGE_VARIABLE, raising=False)
    assert forge.selected() is forge.FORGES["gitea"]


def test_the_variable_picks_the_forge(monkeypatch):
    monkeypatch.setenv(forge.FORGE_VARIABLE, "forgejo")
    assert forge.selected() is forge.FORGES["forgejo"]


def test_an_unknown_forge_fails(monkeypatch):
    monkeypatch.setenv(forge.FORGE_VARIABLE, "github")
    with pytest.raises(pytest.fail.Exception, match="names no forge - one of: gitea, forgejo"):
        forge.selected()


def test_gitea_appends_the_co_author_trailer():
    assert forge.FORGES["gitea"].squash_message("feat: a (#1)\n\n- a\n", "x <x@y>") == (
        "feat: a (#1)\n\n- a\n\nCo-authored-by: x <x@y>\n")


def test_forgejo_appends_nothing():
    assert forge.FORGES["forgejo"].squash_message("feat: a (#1)\n\n- a\n", "x <x@y>") == "feat: a (#1)\n\n- a\n"


def queue_triggers(text):
    # YAML 1.1 reads the key on as True.
    return yaml.safe_load(text)[True]


def test_gitea_runs_the_queue_workflow_as_published():
    text = workflow.QUEUE_WORKFLOW.read_text()
    assert forge.FORGES["gitea"].queue_workflow(text) == text


def test_forgejo_adds_the_schedule_and_keeps_every_trigger():
    text = workflow.QUEUE_WORKFLOW.read_text()
    triggers = queue_triggers(forge.FORGES["forgejo"].queue_workflow(text))
    assert triggers["schedule"] == [{"cron": "@every 5s"}]
    assert {name for name in triggers if name != "schedule"} == set(queue_triggers(text))


def test_forgejo_fails_on_a_file_without_an_on_block():
    with pytest.raises(pytest.fail.Exception, match="no on: block"):
        forge.FORGES["forgejo"].queue_workflow("name: x\njobs: {}\n")
