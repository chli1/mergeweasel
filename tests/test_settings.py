import urllib.error
from unittest import mock

import pytest

from mergeweasel import settings
from mergeweasel.gitea import Gitea


def gitea_with(default_branch="trunk", repo_labels=(), org_labels=()):
    """org_labels: the organization's label names, or the HTTPError reading them raises."""
    gitea = mock.Mock(spec=Gitea)
    gitea.owner = "org"
    gitea.call.side_effect = lambda method, path: {"default_branch": default_branch}

    def paged(path, root=False):
        if (path, root) == ("/labels", False):
            return [{"name": name} for name in repo_labels]
        assert (path, root) == ("/orgs/org/labels", True)
        if isinstance(org_labels, Exception):
            raise org_labels
        return [{"name": name} for name in org_labels]

    gitea.paged.side_effect = paged
    return gitea


def http_error(code):
    return urllib.error.HTTPError("url", code, "error", hdrs=None, fp=None)


def test_without_variables_the_defaults_hold_and_renovate_is_off():
    gitea = gitea_with()
    assert settings.read(gitea, {}) == settings.Settings("trunk", "automerge", "needs-fix", "keep-commits", None)
    gitea.call.assert_called_once_with("GET", "")


def test_each_variable_sets_its_name():
    environ = {"MERGEWEASEL_AUTOMERGE_LABEL": "merge-me", "MERGEWEASEL_NEEDS_FIX_LABEL": "broken",
               "MERGEWEASEL_KEEP_COMMITS_LABEL": "no-squash", "MERGEWEASEL_RENOVATE_ACCOUNT": "deps-bot"}
    assert settings.read(gitea_with(), environ) == settings.Settings("trunk", "merge-me", "broken", "no-squash", "deps-bot")


def test_an_empty_or_blank_variable_keeps_the_default():
    # ${{ vars.X }} of an unset variable is the empty string.
    environ = {"MERGEWEASEL_AUTOMERGE_LABEL": "", "MERGEWEASEL_RENOVATE_ACCOUNT": "  "}
    assert settings.read(gitea_with(), environ) == settings.Settings("trunk")


def test_two_roles_on_one_label_are_refused():
    with pytest.raises(settings.SettingsError, match="three different labels, not automerge, automerge, keep-commits"):
        settings.read(gitea_with(), {"MERGEWEASEL_NEEDS_FIX_LABEL": "automerge"})


def test_describe_names_every_setting():
    assert settings.Settings("trunk", renovate="deps-bot").describe() == (
        "settings: base branch trunk; labels automerge, needs-fix, keep-commits; Renovate account deps-bot")
    assert settings.Settings("trunk").describe().endswith("; Renovate rule off")


ALL = ("automerge", "needs-fix", "keep-commits")


def test_automerge_and_needs_fix_are_always_required():
    assert settings.required_labels(settings.Settings("trunk"), {}) == ("automerge", "needs-fix")


def test_keep_commits_is_required_once_its_variable_names_it():
    named = settings.Settings("trunk", keep_commits="no-squash")
    environ = {"MERGEWEASEL_KEEP_COMMITS_LABEL": "no-squash"}
    assert settings.required_labels(named, environ) == ("automerge", "needs-fix", "no-squash")
    assert settings.required_labels(settings.Settings("trunk"), {"MERGEWEASEL_KEEP_COMMITS_LABEL": " "}) == (
        "automerge", "needs-fix")


def test_labels_of_the_repository_need_no_organization_read():
    gitea = gitea_with(repo_labels=ALL)
    assert settings.missing_labels(gitea, ALL) == []
    gitea.paged.assert_called_once_with("/labels")


def test_labels_of_the_organization_count():
    gitea = gitea_with(repo_labels=("automerge",), org_labels=("needs-fix", "keep-commits"))
    assert settings.missing_labels(gitea, ALL) == []


def test_a_label_nowhere_is_missing():
    gitea = gitea_with(repo_labels=("automerge",), org_labels=("keep-commits",))
    assert settings.missing_labels(gitea, ALL) == ["needs-fix"]


@pytest.mark.parametrize("code", [403, 404])
def test_unreadable_organization_labels_leave_it_unknown(code):
    gitea = gitea_with(org_labels=http_error(code))
    assert settings.missing_labels(gitea, ALL) is None


def test_another_error_reading_the_organization_is_raised():
    with pytest.raises(urllib.error.HTTPError):
        settings.missing_labels(gitea_with(org_labels=http_error(500)), ALL)
