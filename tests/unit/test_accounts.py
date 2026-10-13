import types

import pytest

from mergeweasel.accounts import DRY_RUN_APPROVER, Approvers, DryRunApprovers, DryRunMerger, Merger


def pull(poster="author", approved_head=frozenset()):
    return types.SimpleNamespace(poster=poster, approved_head=approved_head)


def test_the_merger_owns_only_its_login():
    assert Merger("weasel-bot").owns("weasel-bot")
    assert not Merger("weasel-bot").owns("stranger")
    assert not Merger("weasel-bot").owns(None)


def test_the_dry_run_merger_owns_everything():
    assert DryRunMerger().owns("stranger") and DryRunMerger().owns(None)


def test_approvers_keep_the_secrets_order():
    approvers = Approvers({"a2": "client-2", "a1": "client-1"})
    assert approvers.any() and approvers.logins() == ("a2", "a1")
    assert approvers.client("a1") == "client-1"


def test_no_approvers_approve_nothing():
    assert not Approvers({}).any() and Approvers({}).logins() == ()


def test_assign_skips_the_poster_and_the_heads_approvers():
    approvers = Approvers(dict.fromkeys(("a1", "a2", "a3", "a4")))
    assert approvers.assign(pull(poster="a1", approved_head=frozenset({"a2"})), 2) == ("a3", "a4")


def test_assign_returns_none_on_a_shortfall():
    approvers = Approvers(dict.fromkeys(("a1", "a2")))
    assert approvers.assign(pull(poster="a1"), 2) is None


def test_the_dry_run_approvers_are_assumed_enough():
    approvers = DryRunApprovers()
    assert approvers.any() and approvers.logins() == ()
    assert approvers.assign(pull(), 2) == (DRY_RUN_APPROVER, DRY_RUN_APPROVER)


def test_the_dry_run_approvers_never_approve():
    with pytest.raises(RuntimeError, match="a dry run never approves"):
        DryRunApprovers().client(DRY_RUN_APPROVER)


def test_the_dry_run_approver_names_itself():
    assert repr(DRY_RUN_APPROVER) == "DRY_RUN_APPROVER"
    assert repr(DryRunMerger()) == "DryRunMerger()"
