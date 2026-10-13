"""The forge the Docker layers pick, and what tells the forges apart. No forge needed."""
import pytest

import forge


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
