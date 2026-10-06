import json
from unittest import mock

import pytest

from mergeweasel import check
from mergeweasel.settings import Settings
from mergeweasel.commit_rule import Commit
from mergeweasel.gitea import ApiError, Gitea, PullRequest, Unreachable

def raw_commit(sha, subject, parents=1):
    return Commit(sha, subject, parents)


def api(commits=(), issues=(99, 209), config=None, title="ci: squash by default (#209)", body="", labels=(),
        head="h" * 40, merged=False):
    """Pull request 213 as the API reads it when the job runs; config: mergeweasel.toml's text at the
    head, None without the file."""
    gitea = mock.create_autospec(Gitea, instance=True)
    gitea.pull.return_value = PullRequest(213, "main", "feature", head, {name: 13 for name in labels}, False,
                                          "author", title, body, merged)
    gitea.file.return_value = None if config is None else config.encode()
    gitea.issue_numbers.return_value = set(issues)
    gitea.pull_commits.return_value = list(commits)
    return gitea


def event(tmp_path):
    """A pull_request event for 213 whose payload is stale: only its number may count."""
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"pull_request": {
        "number": 213, "title": "stale title", "body": "## Commit\nCo-authored-by: X <x@y>", "merged": False,
        "labels": [{"name": "keep-commits"}], "base": {"sha": "b" * 40}, "head": {"sha": "s" * 40}}}))
    return str(path)


def run(gitea, event_path, settings=Settings("main")):
    return check.run(gitea, event_path, settings)


# --- squash (default) ---

def test_squash_passes_with_a_valid_title_and_section(tmp_path, capsys):
    gitea = api(body="## Commit\nWhy.")
    assert run(gitea, event(tmp_path)) == 0
    gitea.pull.assert_called_once_with(213)
    # The head as it is now, not the event's.
    gitea.file.assert_called_once_with("mergeweasel.toml", "h" * 40)
    assert "ci: squash by default (#209)\n\nWhy." in capsys.readouterr().out


def test_squash_ignores_the_branch_commits(tmp_path):
    gitea = api(commits=[raw_commit("a" * 40, "wip")])
    assert run(gitea, event(tmp_path)) == 0
    gitea.pull_commits.assert_not_called()


def test_squash_fails_on_a_bad_title(tmp_path, capsys):
    assert run(api(title="squash by default"), event(tmp_path)) == 1
    assert "PR title: subject must read" in capsys.readouterr().out


def test_squash_fails_on_a_bad_section(tmp_path, capsys):
    assert run(api(body="## Commit\nCo-authored-by: X <x@y>"), event(tmp_path)) == 1
    assert "attribution line" in capsys.readouterr().out


def test_a_re_run_judges_the_pull_request_as_it_is_now(tmp_path, capsys):
    # The event fired before the fix; re-running its job must see the fixed title.
    assert run(api(title="ci: fixed title (#209)"), event(tmp_path)) == 0
    assert "stale title" not in capsys.readouterr().out


def test_the_event_needs_only_the_number(tmp_path):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"pull_request": {"number": 213}}))
    assert run(api(), str(path)) == 0


# --- configured scopes ---

SCOPES = '[commits]\nscopes = ["domain", "ui"]\n'


def test_a_configured_scope_passes(tmp_path):
    assert run(api(config=SCOPES, title="feat(ui): x (#209)"), event(tmp_path)) == 0


def test_an_unknown_scope_fails_naming_the_allowed_ones(tmp_path, capsys):
    assert run(api(config=SCOPES, title="feat(domian): x (#209)"), event(tmp_path)) == 1
    assert "unknown scope 'domian', expected one of: domain, ui" in capsys.readouterr().out


def test_without_the_file_any_scope_passes(tmp_path):
    assert run(api(title="feat(domian): x (#209)"), event(tmp_path)) == 0


def test_an_unknown_scope_fails_a_kept_commit(tmp_path, capsys):
    gitea = api(commits=[raw_commit("a" * 40, "feat(domian): x (#99)")], config=SCOPES, labels=("keep-commits",))
    assert run(gitea, event(tmp_path)) == 1
    assert "aaaaaaa: unknown scope 'domian'" in capsys.readouterr().out


def test_an_invalid_file_on_the_branch_fails(tmp_path, capsys):
    assert run(api(config="[comits]\n"), event(tmp_path)) == 1
    assert "mergeweasel.toml: unknown key comits" in capsys.readouterr().out


# --- keep-commits ---

def test_keep_commits_checks_every_commit_through_the_api(tmp_path):
    gitea = api(commits=[raw_commit("a" * 40, "ci: one (#99)"), raw_commit("c" * 40, "ci: two (#209)")],
                title="anything", labels=("keep-commits",))
    assert run(gitea, event(tmp_path)) == 0
    gitea.pull_commits.assert_called_once_with(213)


def test_keep_commits_fails_on_a_bad_commit(tmp_path, capsys):
    gitea = api(commits=[raw_commit("a" * 40, "wip")], labels=("keep-commits",))
    assert run(gitea, event(tmp_path)) == 1
    assert "aaaaaaa: subject must read" in capsys.readouterr().out


def test_keep_commits_rejects_a_merge_commit(tmp_path, capsys):
    gitea = api(commits=[raw_commit("abc1234" + "0" * 33, "ci: merge main (#99)", parents=2)], labels=("keep-commits",))
    assert run(gitea, event(tmp_path)) == 1
    assert "abc1234: merge commits are not allowed" in capsys.readouterr().out


def test_keep_commits_refuses_no_commits(tmp_path):
    assert run(api(commits=[], labels=("keep-commits",)), event(tmp_path)) == 1


@pytest.mark.parametrize("labels", [(), ("keep-commits",)])
def test_a_merged_pull_request_passes_without_a_check(tmp_path, capsys, labels):
    # Merged since the event fired: the queue's label removal re-runs the check on a landed pull request.
    gitea = api(title="no longer valid", labels=labels, merged=True)
    assert run(gitea, event(tmp_path)) == 0
    assert "already landed" in capsys.readouterr().out
    gitea.issue_numbers.assert_not_called()


# --- failures around the check ---

def test_fails_loudly_when_the_api_refuses(tmp_path, capsys):
    gitea = api()
    gitea.issue_numbers.side_effect = ApiError("Forbidden", 403, "https://git.example/api/v1/repos/org/repo/issues")
    assert run(gitea, event(tmp_path)) == 1
    err = capsys.readouterr().err
    assert "HTTP 403 Forbidden (https://git.example/api/v1/repos/org/repo/issues)" in err and "GITHUB_TOKEN" in err


def test_an_answer_without_a_status_names_no_status_and_no_token_hint(tmp_path, capsys):
    gitea = api()
    gitea.issue_numbers.side_effect = ApiError("Gitea's answer is not JSON", None, "https://git.example/x")
    assert run(gitea, event(tmp_path)) == 1
    err = capsys.readouterr().err
    assert "could not read from the Gitea API: Gitea's answer is not JSON (https://git.example/x)" in err
    assert "None" not in err and "GITHUB_TOKEN" not in err


def test_fails_clearly_when_gitea_is_unreachable(tmp_path, capsys):
    gitea = api()
    gitea.file.side_effect = Unreachable("connection refused", None, "https://git.example/x")
    assert run(gitea, event(tmp_path)) == 1
    assert "could not reach the Gitea API: connection refused" in capsys.readouterr().err


def test_not_a_pull_request_event_fails_clearly(tmp_path, capsys):
    push = tmp_path / "push.json"
    push.write_text(json.dumps({"ref": "refs/heads/main"}))
    empty = tmp_path / "empty.json"
    empty.write_text("")
    for path in (push, empty, tmp_path / "missing.json"):
        assert run(api(), str(path)) == 1
        assert "is not a pull_request event" in capsys.readouterr().err


def test_the_instances_keep_commits_label_checks_the_commits(tmp_path, capsys):
    commits = [raw_commit("a" * 40, "feat: add a (#99)")]
    no_squash = Settings("main", keep_commits="no-squash")
    assert run(api(commits, title="anything", labels=("no-squash",)), event(tmp_path), no_squash) == 0
    assert "no-squash: 1 commit(s) checked" in capsys.readouterr().out
    assert run(api(commits, title="anything", labels=("keep-commits",)), event(tmp_path), no_squash) == 1
