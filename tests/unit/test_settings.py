from unittest import mock

import pytest

from mergeweasel import settings
from mergeweasel.api import ApiError
from mergeweasel.gitea import Gitea


def gitea_with(default_branch="trunk", repo_labels=(), org_labels=()):
    """org_labels: the organization's label names, None if unreadable, or the error reading them raises."""
    gitea = mock.create_autospec(Gitea, instance=True)
    gitea.default_branch.return_value = default_branch
    gitea.labels.return_value = list(repo_labels)
    if isinstance(org_labels, Exception):
        gitea.org_labels.side_effect = org_labels
    else:
        gitea.org_labels.return_value = None if org_labels is None else list(org_labels)
    return gitea


def test_without_variables_the_defaults_hold_and_renovate_is_off():
    gitea = gitea_with()
    assert settings.read(gitea, {}) == settings.Settings(
        "trunk", "automerge", "needs-fix", "keep-commits", "prio", None
    )
    gitea.default_branch.assert_called_once_with()


def test_each_variable_sets_its_name():
    environ = {
        "MERGEWEASEL_AUTOMERGE_LABEL": "merge-me",
        "MERGEWEASEL_NEEDS_FIX_LABEL": "broken",
        "MERGEWEASEL_KEEP_COMMITS_LABEL": "no-squash",
        "MERGEWEASEL_PRIO_LABEL": "hotfix",
        "MERGEWEASEL_RENOVATE_ACCOUNT": "deps-bot",
    }
    assert settings.read(gitea_with(), environ) == settings.Settings(
        "trunk", "merge-me", "broken", "no-squash", "hotfix", "deps-bot", keep_commits_required=True, prio_required=True
    )


def test_an_empty_or_blank_variable_keeps_the_default():
    # ${{ vars.X }} of an unset variable is the empty string.
    environ = {"MERGEWEASEL_AUTOMERGE_LABEL": "", "MERGEWEASEL_RENOVATE_ACCOUNT": "  "}
    assert settings.read(gitea_with(), environ) == settings.Settings("trunk")


def test_two_roles_on_one_label_are_refused():
    with pytest.raises(
        settings.SettingsError, match="four different labels, not automerge, automerge, keep-commits, prio"
    ):
        settings.read(gitea_with(), {"MERGEWEASEL_NEEDS_FIX_LABEL": "automerge"})


def test_describe_names_every_setting():
    assert settings.Settings("trunk", renovate="deps-bot").describe() == (
        "settings: base branch trunk; labels automerge, needs-fix, keep-commits, prio; Renovate account deps-bot"
    )
    assert settings.Settings("trunk").describe().endswith("; Renovate rule off")


def test_prio_is_required_once_its_variable_names_it():
    named = settings.read(gitea_with(), {"MERGEWEASEL_PRIO_LABEL": "hotfix"})
    assert named.prio_required and not named.keep_commits_required
    assert settings.required_labels(named) == ("automerge", "needs-fix", "hotfix")
    both = settings.read(
        gitea_with(), {"MERGEWEASEL_KEEP_COMMITS_LABEL": "no-squash", "MERGEWEASEL_PRIO_LABEL": "prio"}
    )
    assert settings.required_labels(both) == ("automerge", "needs-fix", "no-squash", "prio")


ALL = ("automerge", "needs-fix", "keep-commits")


def test_automerge_and_needs_fix_are_always_required():
    assert settings.required_labels(settings.Settings("trunk")) == ("automerge", "needs-fix")


def test_keep_commits_is_required_once_its_variable_names_it():
    named = settings.read(gitea_with(), {"MERGEWEASEL_KEEP_COMMITS_LABEL": "no-squash"})
    assert named.keep_commits_required
    assert settings.required_labels(named) == ("automerge", "needs-fix", "no-squash")
    # Naming the default on purpose makes it required too.
    default = settings.read(gitea_with(), {"MERGEWEASEL_KEEP_COMMITS_LABEL": "keep-commits"})
    assert settings.required_labels(default) == ("automerge", "needs-fix", "keep-commits")
    blank = settings.read(gitea_with(), {"MERGEWEASEL_KEEP_COMMITS_LABEL": " "})
    assert not blank.keep_commits_required
    assert settings.required_labels(blank) == ("automerge", "needs-fix")


def test_labels_of_the_repository_need_no_organization_read():
    gitea = gitea_with(repo_labels=ALL)
    assert settings.missing_labels(gitea, ALL) == []
    gitea.org_labels.assert_not_called()


def test_labels_of_the_organization_count():
    gitea = gitea_with(repo_labels=("automerge",), org_labels=("needs-fix", "keep-commits"))
    assert settings.missing_labels(gitea, ALL) == []


def test_a_label_nowhere_is_missing():
    gitea = gitea_with(repo_labels=("automerge",), org_labels=("keep-commits",))
    assert settings.missing_labels(gitea, ALL) == ["needs-fix"]


def test_unreadable_organization_labels_leave_it_unknown():
    gitea = gitea_with(org_labels=None)
    assert settings.missing_labels(gitea, ALL) is None


def test_another_error_reading_the_organization_is_raised():
    with pytest.raises(ApiError):
        settings.missing_labels(gitea_with(org_labels=ApiError("boom", 500, "url")), ALL)
