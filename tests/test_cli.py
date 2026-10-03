import io
import urllib.error
from unittest import mock

import pytest

from mergeweasel import cli
from mergeweasel.gitea import Gitea

ENVIRONMENT = ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "MERGEWEASEL_TOKEN", "GITHUB_TOKEN",
               "GITHUB_RUN_ID", "GITHUB_EVENT_NAME")


@pytest.fixture
def env(monkeypatch):
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://git.example")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    return monkeypatch


@pytest.fixture
def fake(monkeypatch):
    """Gitea replaced by a mock; returns how it was built, the client, and the mocked pass."""
    built = {}
    client = mock.Mock(spec=Gitea)
    client.own_login.return_value = "weasel-bot"

    def factory(server, repository, token):
        built.update(server=server, repository=repository, token=token)
        return client

    monkeypatch.setattr(cli, "Gitea", factory)
    run_pass = mock.Mock()
    monkeypatch.setattr(cli.queue, "run_pass", run_pass)
    return built, client, run_pass


@pytest.mark.parametrize("name", ["GITHUB_SERVER_URL", "GITHUB_REPOSITORY"])
def test_missing_environment_exits_1(env, fake, capsys, name):
    env.delenv(name)
    assert cli.main(["pass"]) == 1
    assert f"{name} is not set" in capsys.readouterr().err


def test_server_url_loses_its_trailing_slash(env, fake):
    env.setenv("GITHUB_SERVER_URL", "https://git.example/")
    env.setenv("MERGEWEASEL_TOKEN", "pat")
    assert cli.main(["pass"]) == 0
    assert fake[0]["server"] == "https://git.example"


def test_pass_without_the_token_exits_1(env, fake, capsys):
    assert cli.main(["pass"]) == 1
    assert "MERGEWEASEL_TOKEN is not set" in capsys.readouterr().err
    fake[2].assert_not_called()


def test_pass_derives_the_account_from_the_token(env, fake):
    env.setenv("MERGEWEASEL_TOKEN", "pat")
    built, client, run_pass = fake
    assert cli.main(["pass"]) == 0
    assert built == {"server": "https://git.example", "repository": "org/repo", "token": "pat"}
    run_pass.assert_called_once_with(client, "https://git.example", False, "weasel-bot")


def test_pass_fails_when_the_account_cannot_be_read(env, fake, capsys):
    env.setenv("MERGEWEASEL_TOKEN", "pat")
    built, client, run_pass = fake
    client.own_login.side_effect = urllib.error.HTTPError(
        "https://git.example/api/v1/user", 403, "Forbidden", hdrs=None, fp=None)
    assert cli.main(["pass"]) == 1
    assert "read:user" in capsys.readouterr().err
    run_pass.assert_not_called()


def test_dry_run_with_the_token_knows_its_account(env, fake):
    env.setenv("MERGEWEASEL_TOKEN", "pat")
    built, client, run_pass = fake
    assert cli.main(["pass", "--dry-run"]) == 0
    run_pass.assert_called_once_with(client, "https://git.example", True, "weasel-bot")


def test_dry_run_without_the_token_uses_the_run_token_and_no_account(env, fake, capsys):
    env.setenv("GITHUB_TOKEN", "run-token")
    built, client, run_pass = fake
    assert cli.main(["pass", "--dry-run"]) == 0
    assert built["token"] == "run-token"
    client.own_login.assert_not_called()
    run_pass.assert_called_once_with(client, "https://git.example", True, None)
    assert "queue account unknown, carry-over assumed genuine" in capsys.readouterr().out


def test_dry_run_without_any_token_runs_anonymously(env, fake):
    built, client, run_pass = fake
    assert cli.main(["pass", "--dry-run"]) == 0
    assert built["token"] == ""


def test_a_superseded_pass_succeeds_without_running(env, fake, monkeypatch):
    env.setenv("MERGEWEASEL_TOKEN", "pat")
    env.setenv("GITHUB_RUN_ID", "7")
    env.setenv("GITHUB_EVENT_NAME", "pull_request_target")
    built, client, run_pass = fake
    wait = mock.Mock(return_value=False)
    monkeypatch.setattr(cli.queue, "wait_for_turn", wait)
    assert cli.main(["pass"]) == 0
    wait.assert_called_once_with(client, 7, "pull_request_target")
    run_pass.assert_not_called()


def test_without_a_run_id_the_pass_does_not_wait(env, fake, monkeypatch):
    env.setenv("MERGEWEASEL_TOKEN", "pat")
    wait = mock.Mock()
    monkeypatch.setattr(cli.queue, "wait_for_turn", wait)
    assert cli.main(["pass"]) == 0
    wait.assert_not_called()


def test_check_commits_runs_the_check_with_the_run_token(env, fake, monkeypatch):
    env.setenv("GITHUB_TOKEN", "run-token")
    built, client, _ = fake
    check_run = mock.Mock(return_value=1)
    monkeypatch.setattr(cli.check, "run", check_run)
    assert cli.main(["check-commits", "--event", "/tmp/event.json"]) == 1
    assert built["token"] == "run-token"
    check_run.assert_called_once_with(client, "/tmp/event.json")


def test_a_failed_account_read_shows_giteas_reason(env, fake, capsys):
    env.setenv("MERGEWEASEL_TOKEN", "pat")
    built, client, run_pass = fake
    client.own_login.side_effect = urllib.error.HTTPError(
        "https://git.example/api/v1/user", 401, "Unauthorized", hdrs=None,
        fp=io.BytesIO(b'{"message": "token is expired"}'))
    assert cli.main(["pass"]) == 1
    err = capsys.readouterr().err
    assert "token is expired" in err and "e.g. missing scope read:user" in err
