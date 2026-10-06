import dataclasses
import io
import urllib.error
from unittest import mock

import pytest

from mergeweasel import cli
from mergeweasel.gitea import Gitea
from mergeweasel.settings import VARIABLES, Settings

ENVIRONMENT = ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "MERGEWEASEL_MERGER_TOKEN", "MERGEWEASEL_APPROVER_TOKENS",
               "GITHUB_TOKEN", "GITHUB_RUN_ID", "GITHUB_EVENT_NAME", *VARIABLES.values())
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
    release: mock.Mock
    # The labels the repository has.
    labels: list = dataclasses.field(default_factory=lambda: ["automerge", "needs-fix", "keep-commits"])
    # The labels its organization has, None if they are unreadable.
    org_labels: list | None = None

    def args(self):
        """The pass's arguments but the settings."""
        return self.run_pass.call_args.args[:-1]

    def settings(self):
        return self.run_pass.call_args.args[-1]


@pytest.fixture
def fake(monkeypatch):
    """Gitea replaced by one mock per token, the pass by a mock."""
    fake = Fake({}, {"pat": "weasel-bot", "a-1": "approver-1", "a-2": "approver-2"}, mock.Mock(), mock.Mock(return_value=True))

    def factory(server, repository, token):
        client = mock.Mock(spec=Gitea)
        client.built = (server, repository, token)

        def own_login():
            login = fake.logins[token]
            if isinstance(login, Exception):
                raise login
            return login

        client.own_login.side_effect = own_login
        client.owner = "org"
        client.call.side_effect = lambda method, path, body=None: {"default_branch": "trunk"} if path == "" else None

        def paged(path, root=False):
            if not root:
                return [{"name": name} for name in fake.labels]
            if fake.org_labels is None:
                raise urllib.error.HTTPError(f"{SERVER}/api/v1{path}", 404, "Not Found", hdrs=None, fp=None)
            return [{"name": name} for name in fake.org_labels]

        client.paged.side_effect = paged
        fake.clients[token] = client
        return client

    monkeypatch.setattr(cli, "Gitea", factory)
    monkeypatch.setattr(cli.queue, "run_pass", fake.run_pass)
    monkeypatch.setattr(cli.release, "run", fake.release)
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
    check_run.assert_called_once_with(fake.clients["run-token"], "/tmp/event.json", Settings("trunk"))


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


def test_pass_releases_before_it_runs(env, fake):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    order = []
    fake.release.side_effect = lambda gitea, dry_run, base_branch: order.append(("release", dry_run, base_branch)) or True
    fake.run_pass.side_effect = lambda *args: order.append(("pass",))
    assert cli.main(["pass"]) == 0
    assert order == [("release", False, "trunk"), ("pass",)]
    assert fake.release.call_args.args[0] is fake.clients["pat"]


def test_release_error_still_runs_the_pass_and_exits_1(env, fake):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    fake.release.return_value = False
    assert cli.main(["pass"]) == 1
    fake.run_pass.assert_called_once()


def test_superseded_pass_does_not_release(env, fake, monkeypatch):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    env.setenv("GITHUB_RUN_ID", "7")
    monkeypatch.setattr(cli.queue, "wait_for_turn", lambda gitea, own_id, event: False)
    assert cli.main(["pass"]) == 0
    fake.release.assert_not_called()


def test_pass_prints_and_passes_the_instances_settings(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    env.setenv("MERGEWEASEL_AUTOMERGE_LABEL", "merge-me")
    env.setenv("MERGEWEASEL_RENOVATE_ACCOUNT", "deps-bot")
    fake.labels = ["merge-me", "needs-fix", "keep-commits"]
    assert cli.main(["pass"]) == 0
    assert fake.settings() == Settings("trunk", automerge="merge-me", renovate="deps-bot")
    assert "settings: base branch trunk; labels merge-me, needs-fix, keep-commits; Renovate account deps-bot" in capsys.readouterr().out


def test_a_missing_label_fails_the_pass_before_the_release(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    env.setenv("MERGEWEASEL_NEEDS_FIX_LABEL", "broken")
    fake.labels = ["automerge"]
    fake.org_labels = ["keep-commits"]
    assert cli.main(["pass"]) == 1
    assert "labels missing in the repository and its organization: broken" in capsys.readouterr().err
    fake.release.assert_not_called()
    fake.run_pass.assert_not_called()


def test_unreadable_organization_labels_skip_the_check(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    fake.labels = []
    assert cli.main(["pass"]) == 0
    assert "not checked whether the labels exist" in capsys.readouterr().out


def test_one_label_for_two_roles_fails_both_commands(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    env.setenv("MERGEWEASEL_KEEP_COMMITS_LABEL", "automerge")
    assert cli.main(["pass"]) == 1
    assert cli.main(["check-commits", "--event", "/tmp/event.json"]) == 1
    assert capsys.readouterr().err.count("three different labels") == 2
    fake.run_pass.assert_not_called()


def test_an_unreadable_repository_fails_the_check(env, fake, capsys, monkeypatch):
    check_run = mock.Mock()
    monkeypatch.setattr(cli.check, "run", check_run)
    factory = cli.Gitea

    def failing(*arguments):
        client = factory(*arguments)
        client.call.side_effect = unauthorized()
        return client

    monkeypatch.setattr(cli, "Gitea", failing)
    assert cli.main(["check-commits", "--event", "/tmp/event.json"]) == 1
    assert "cannot read the repository: token is expired" in capsys.readouterr().err
    check_run.assert_not_called()


def test_an_unreadable_label_list_fails_the_pass(env, fake, capsys, monkeypatch):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    monkeypatch.setattr(cli.instance, "missing_labels", mock.Mock(side_effect=unauthorized()))
    assert cli.main(["pass"]) == 1
    assert "cannot read the labels: token is expired" in capsys.readouterr().err
    fake.release.assert_not_called()


def test_a_missing_keep_commits_label_is_fine_unless_named(env, fake, capsys):
    env.setenv("MERGEWEASEL_MERGER_TOKEN", "pat")
    fake.labels = ["automerge", "needs-fix"]
    fake.org_labels = []
    assert cli.main(["pass"]) == 0
    env.setenv("MERGEWEASEL_KEEP_COMMITS_LABEL", "no-squash")
    assert cli.main(["pass"]) == 1
    assert "labels missing in the repository and its organization: no-squash" in capsys.readouterr().err
