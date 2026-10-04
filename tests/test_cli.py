import dataclasses
import io
import urllib.error
from unittest import mock

import pytest

from mergeweasel import cli
from mergeweasel.gitea import Gitea

ENVIRONMENT = ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "MERGEWEASEL_MERGER_TOKEN", "MERGEWEASEL_APPROVER_TOKENS",
               "GITHUB_TOKEN", "GITHUB_RUN_ID", "GITHUB_EVENT_NAME")
SERVER = "https://git.example"


@pytest.fixture
def env(monkeypatch):
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_SERVER_URL", SERVER)
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    return monkeypatch


@dataclasses.dataclass
class Fake:
    # The mocked client of each token, the latest one per token.
    clients: dict
    # What own_login answers per token: a login, or an exception it raises.
    logins: dict
    run_pass: mock.Mock

    def args(self):
        return self.run_pass.call_args.args


@pytest.fixture
def fake(monkeypatch):
    """Gitea replaced by one mock per token, the pass by a mock."""
    fake = Fake({}, {"pat": "weasel-bot", "a-1": "approver-1", "a-2": "approver-2"}, mock.Mock())

    def factory(server, repository, token):
        client = mock.Mock(spec=Gitea)
        client.built = (server, repository, token)

        def own_login():
            login = fake.logins[token]
            if isinstance(login, Exception):
                raise login
            return login

        client.own_login.side_effect = own_login
        fake.clients[token] = client
        return client

    monkeypatch.setattr(cli, "Gitea", factory)
    monkeypatch.setattr(cli.queue, "run_pass", fake.run_pass)
    return fake


def unauthorized(message="token is expired"):
    return urllib.error.HTTPError(f"{SERVER}/api/v1/user", 401, "Unauthorized", hdrs=None,
                                  fp=io.BytesIO(f'{{"message": "{message}"}}'.encode()))


@pytest.mark.parametrize("name", ["GITHUB_SERVER_URL", "GITHUB_REPOSITORY"])
def test_missing_environment_exits_1(env, fake, capsys, name):
    env.delenv(name)
    assert cli.main(["pass"]) == 1
    assert f"{name} is not set" in capsys.readouterr().err


def test_server_url_loses_its_trailing_slash(env, fake):
    env.setenv("GITHUB_SERVER_URL", f"{SERVER}/")
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    assert cli.main(["pass"]) == 0
    assert fake.clients["pat"].built == (SERVER, "org/repo", "pat")


def test_pass_without_the_merger_token_exits_1(env, fake, capsys):
    assert cli.main(["pass"]) == 1
    assert "MERGEWEASEL_MERGER_TOKEN is not set" in capsys.readouterr().err
    fake.run_pass.assert_not_called()


def test_pass_derives_the_merger_and_the_approvers(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    env.setenv("MERGEWEASEL_APPROVER_TOKENS", "a-1\na-2\n")
    assert cli.main(["pass"]) == 0
    gitea, server, dry_run, merger, approvers = fake.args()
    assert (gitea, server, dry_run, merger) == (fake.clients["pat"], SERVER, False, "weasel-bot")
    assert list(approvers.items()) == [("approver-1", fake.clients["a-1"]), ("approver-2", fake.clients["a-2"])]
    assert "approvers: approver-1, approver-2" in capsys.readouterr().out


def test_approver_tokens_tolerate_crlf_and_blank_lines(env, fake):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    env.setenv("MERGEWEASEL_APPROVER_TOKENS", "\r\n  a-2  \r\n\r\na-1\r\n")
    assert cli.main(["pass"]) == 0
    assert list(fake.args()[4]) == ["approver-2", "approver-1"]


def test_the_merger_token_can_be_an_approver_token(env, fake):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    env.setenv("MERGEWEASEL_APPROVER_TOKENS", "pat")
    assert cli.main(["pass"]) == 0
    assert fake.args()[3] == "weasel-bot" and list(fake.args()[4]) == ["weasel-bot"]


def test_a_pass_without_approver_tokens_has_none(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    assert cli.main(["pass"]) == 0
    assert fake.args()[4] == {}
    assert "approvers: none" in capsys.readouterr().out


def test_a_rejected_approver_token_fails_the_pass(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    env.setenv("MERGEWEASEL_APPROVER_TOKENS", "a-1\nsecret-value")
    fake.logins["secret-value"] = unauthorized()
    assert cli.main(["pass"]) == 1
    out, err = capsys.readouterr()
    assert "token 2 of MERGEWEASEL_APPROVER_TOKENS" in err and "token is expired" in err
    assert "secret-value" not in out + err
    fake.run_pass.assert_not_called()


def test_two_approver_tokens_of_one_account_fail_the_pass(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    env.setenv("MERGEWEASEL_APPROVER_TOKENS", "a-1\na-2")
    fake.logins["a-2"] = "approver-1"
    assert cli.main(["pass"]) == 1
    assert "token 2 of MERGEWEASEL_APPROVER_TOKENS belongs to approver-1 again" in capsys.readouterr().err
    fake.run_pass.assert_not_called()


def test_pass_fails_when_the_merger_cannot_be_read(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    fake.logins["pat"] = urllib.error.HTTPError(f"{SERVER}/api/v1/user", 403, "Forbidden", hdrs=None, fp=None)
    assert cli.main(["pass"]) == 1
    assert "read:user" in capsys.readouterr().err
    fake.run_pass.assert_not_called()


def test_a_failed_account_read_shows_giteas_reason(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    fake.logins["pat"] = unauthorized()
    assert cli.main(["pass"]) == 1
    err = capsys.readouterr().err
    assert "MERGEWEASEL_MERGER_TOKEN" in err and "token is expired" in err and "e.g. missing scope read:user" in err


@pytest.mark.parametrize("login", ["", None])
def test_a_token_without_a_login_fails_the_pass(env, fake, capsys, login):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    fake.logins["pat"] = login
    assert cli.main(["pass"]) == 1
    assert "no login" in capsys.readouterr().err
    fake.run_pass.assert_not_called()


def test_dry_run_without_approver_tokens_assumes_enough(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    assert cli.main(["pass", "--dry-run"]) == 0
    assert fake.args() == (fake.clients["pat"], SERVER, True, "weasel-bot", None)
    assert "approver accounts unknown, assuming enough" in capsys.readouterr().out


def test_dry_run_with_approver_tokens_resolves_them(env, fake):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    env.setenv("MERGEWEASEL_APPROVER_TOKENS", "a-1")
    assert cli.main(["pass", "--dry-run"]) == 0
    assert list(fake.args()[4]) == ["approver-1"]


def test_dry_run_without_the_merger_token_uses_the_run_token_and_no_merger(env, fake, capsys):
    env.setenv("GITHUB_TOKEN", "run-token")
    assert cli.main(["pass", "--dry-run"]) == 0
    client = fake.clients["run-token"]
    client.own_login.assert_not_called()
    assert fake.args() == (client, SERVER, True, None, None)
    assert "merger account unknown, carry-over assumed genuine" in capsys.readouterr().out


def test_dry_run_without_any_token_runs_anonymously(env, fake):
    assert cli.main(["pass", "--dry-run"]) == 0
    assert fake.args()[0].built[2] == ""


def test_a_superseded_pass_succeeds_without_running(env, fake, monkeypatch):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    env.setenv("GITHUB_RUN_ID", "7")
    env.setenv("GITHUB_EVENT_NAME", "pull_request_target")
    wait = mock.Mock(return_value=False)
    monkeypatch.setattr(cli.queue, "wait_for_turn", wait)
    assert cli.main(["pass"]) == 0
    wait.assert_called_once_with(fake.clients["pat"], 7, "pull_request_target")
    fake.run_pass.assert_not_called()


def test_without_a_run_id_the_pass_does_not_wait(env, fake, monkeypatch):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    wait = mock.Mock()
    monkeypatch.setattr(cli.queue, "wait_for_turn", wait)
    assert cli.main(["pass"]) == 0
    wait.assert_not_called()


def test_check_commits_runs_the_check_with_the_run_token(env, fake, monkeypatch):
    env.setenv("GITHUB_TOKEN", "run-token")
    check_run = mock.Mock(return_value=1)
    monkeypatch.setattr(cli.check, "run", check_run)
    assert cli.main(["check-commits", "--event", "/tmp/event.json"]) == 1
    check_run.assert_called_once_with(fake.clients["run-token"], "/tmp/event.json")


def test_the_merger_token_tolerates_a_trailing_newline(env, fake):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat\n")
    assert cli.main(["pass"]) == 0
    assert fake.args()[3] == "weasel-bot"


def test_a_malformed_token_is_not_printed(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    fake.logins["pat"] = ValueError("Invalid header value b'token secret-value\\n'")
    assert cli.main(["pass"]) == 1
    err = capsys.readouterr().err
    assert "secret-value" not in err and "MERGEWEASEL_MERGER_TOKEN" in err
