import base64
import json
import urllib.error
from unittest import mock

import pytest

from mergeweasel import check
from mergeweasel.gitea import Gitea

ISSUES_PATH = "/issues?state=all&type=issues"
COMMITS_PATH = "/pulls/213/commits?stat=false&verification=false&files=false"


def raw_commit(sha, subject, parents=1):
    return {"sha": sha, "commit": {"message": f"{subject}\n\nbody"}, "parents": [{"sha": "p" * 40}] * parents}


def api(commits=(), issues=(99, 209), config=None):
    """config: mergeweasel.toml's text at the head, None without the file."""
    gitea = mock.Mock(spec=Gitea)

    def call(method, path):
        assert (method, path) == ("GET", f"/contents/mergeweasel.toml?ref={'h' * 40}")
        if config is None:
            raise urllib.error.HTTPError("url", 404, "Not Found", hdrs=None, fp=None)
        return {"type": "file", "content": base64.b64encode(config.encode()).decode()}

    gitea.call.side_effect = call

    def paged(path):
        if path == ISSUES_PATH:
            return [{"number": number} for number in issues]
        if path == COMMITS_PATH:
            return list(commits)
        raise AssertionError(f"unexpected read {path}")

    gitea.paged.side_effect = paged
    return gitea


def event(tmp_path, title="ci: squash by default (#209)", body="", labels=(), head="h" * 40, merged=False):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"pull_request": {
        "number": 213, "title": title, "body": body, "merged": merged,
        "labels": [{"name": name} for name in labels],
        "base": {"sha": "b" * 40}, "head": {"sha": head}}}))
    return str(path)


def read_paths(gitea):
    return [call.args[0] for call in gitea.paged.call_args_list]


# --- squash (default) ---

def test_squash_passes_with_a_valid_title_and_section(tmp_path, capsys):
    assert check.run(api(), event(tmp_path, body="## Commit\nWhy.")) == 0
    assert "ci: squash by default (#209)\n\nWhy." in capsys.readouterr().out


def test_squash_ignores_the_branch_commits(tmp_path):
    gitea = api(commits=[raw_commit("a" * 40, "wip")])
    assert check.run(gitea, event(tmp_path)) == 0
    assert COMMITS_PATH not in read_paths(gitea)


def test_squash_fails_on_a_bad_title(tmp_path, capsys):
    assert check.run(api(), event(tmp_path, title="squash by default")) == 1
    assert "PR title: subject must read" in capsys.readouterr().out


def test_squash_fails_on_a_bad_section(tmp_path, capsys):
    assert check.run(api(), event(tmp_path, body="## Commit\nCo-authored-by: X <x@y>")) == 1
    assert "attribution line" in capsys.readouterr().out


def test_null_body_and_labels_are_a_subject_only_commit(tmp_path):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"pull_request": {
        "number": 213, "title": "ci: x (#209)", "body": None, "labels": None,
        "base": {"sha": "b" * 40}, "head": {"sha": "h" * 40}}}))
    assert check.run(api(), str(path)) == 0


# --- configured scopes ---

SCOPES = '[commits]\nscopes = ["domain", "ui"]\n'


def test_a_configured_scope_passes(tmp_path):
    assert check.run(api(config=SCOPES), event(tmp_path, title="feat(ui): x (#209)")) == 0


def test_an_unknown_scope_fails_naming_the_allowed_ones(tmp_path, capsys):
    assert check.run(api(config=SCOPES), event(tmp_path, title="feat(domian): x (#209)")) == 1
    assert "unknown scope 'domian', expected one of: domain, ui" in capsys.readouterr().out


def test_without_the_file_any_scope_passes(tmp_path):
    assert check.run(api(), event(tmp_path, title="feat(domian): x (#209)")) == 0


def test_an_unknown_scope_fails_a_kept_commit(tmp_path, capsys):
    gitea = api(commits=[raw_commit("a" * 40, "feat(domian): x (#99)")], config=SCOPES)
    assert check.run(gitea, event(tmp_path, labels=("keep-commits",))) == 1
    assert "aaaaaaa: unknown scope 'domian'" in capsys.readouterr().out


def test_an_invalid_file_on_the_branch_fails(tmp_path, capsys):
    assert check.run(api(config="[comits]\n"), event(tmp_path)) == 1
    assert "mergeweasel.toml: unknown key comits" in capsys.readouterr().out


# --- keep-commits ---

def test_keep_commits_checks_every_commit_through_the_api(tmp_path):
    gitea = api(commits=[raw_commit("a" * 40, "ci: one (#99)"), raw_commit("c" * 40, "ci: two (#209)")])
    assert check.run(gitea, event(tmp_path, title="anything", labels=("keep-commits",))) == 0
    assert COMMITS_PATH in read_paths(gitea)


def test_keep_commits_fails_on_a_bad_commit(tmp_path, capsys):
    gitea = api(commits=[raw_commit("a" * 40, "wip")])
    assert check.run(gitea, event(tmp_path, labels=("keep-commits",))) == 1
    assert "aaaaaaa: subject must read" in capsys.readouterr().out


def test_keep_commits_rejects_a_merge_commit(tmp_path, capsys):
    gitea = api(commits=[raw_commit("abc1234" + "0" * 33, "ci: merge main (#99)", parents=2)])
    assert check.run(gitea, event(tmp_path, labels=("keep-commits",))) == 1
    assert "abc1234: merge commits are not allowed" in capsys.readouterr().out


def test_keep_commits_refuses_no_commits(tmp_path):
    assert check.run(api(commits=[]), event(tmp_path, labels=("keep-commits",))) == 1


@pytest.mark.parametrize("labels", [(), ("keep-commits",)])
def test_a_merged_pull_request_passes_without_a_check(tmp_path, capsys, labels):
    gitea = api()
    assert check.run(gitea, event(tmp_path, title="no longer valid", labels=labels, merged=True)) == 0
    assert "already landed" in capsys.readouterr().out
    assert read_paths(gitea) == []


# --- failures around the check ---

def test_fails_loudly_when_the_api_refuses(tmp_path, capsys):
    gitea = api()
    gitea.paged.side_effect = urllib.error.HTTPError(
        "https://git.example/api/v1/repos/org/repo/issues", 403, "Forbidden", hdrs=None, fp=None)
    assert check.run(gitea, event(tmp_path)) == 1
    err = capsys.readouterr().err
    assert "HTTP 403" in err and "GITHUB_TOKEN" in err


def test_not_a_pull_request_event_fails_clearly(tmp_path, capsys):
    push = tmp_path / "push.json"
    push.write_text(json.dumps({"ref": "refs/heads/main"}))
    empty = tmp_path / "empty.json"
    empty.write_text("")
    for path in (push, empty, tmp_path / "missing.json"):
        assert check.run(api(), str(path)) == 1
        assert "is not a pull_request event" in capsys.readouterr().err
