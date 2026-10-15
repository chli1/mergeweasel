import dataclasses

import pytest

from mergeweasel.accounts import DRY_RUN_APPROVER, Approvers, DryRunApprovers, DryRunMerger, Merger
from mergeweasel.gitea import Gitea


@dataclasses.dataclass(frozen=True)
class Pull:
    """What assigning approvers reads of a pull request."""

    poster: str = "author"
    approved_head: frozenset[str] = frozenset()


def client(token: str) -> Gitea:
    return Gitea("https://git.example", "org/repo", token)


def test_the_merger_owns_only_its_login() -> None:
    assert Merger("weasel-bot").owns("weasel-bot")
    assert not Merger("weasel-bot").owns("stranger")
    assert not Merger("weasel-bot").owns(None)


def test_the_dry_run_merger_owns_everything() -> None:
    assert DryRunMerger().owns("stranger") and DryRunMerger().owns(None)


def test_approvers_keep_the_secrets_order() -> None:
    first, second = client("token-1"), client("token-2")
    approvers = Approvers({"a2": second, "a1": first})
    assert approvers.any() and approvers.logins() == ("a2", "a1")
    assert approvers.client("a1") is first


def test_no_approvers_approve_nothing() -> None:
    assert not Approvers({}).any() and Approvers({}).logins() == ()


def test_assign_skips_the_poster_and_the_heads_approvers() -> None:
    approvers = Approvers({login: client(login) for login in ("a1", "a2", "a3", "a4")})
    assert approvers.assign(Pull(poster="a1", approved_head=frozenset({"a2"})), 2) == ("a3", "a4")


def test_assign_returns_none_on_a_shortfall() -> None:
    approvers = Approvers({login: client(login) for login in ("a1", "a2")})
    assert approvers.assign(Pull(poster="a1"), 2) is None


def test_the_dry_run_approvers_are_assumed_enough() -> None:
    approvers = DryRunApprovers()
    assert approvers.any() and approvers.logins() == ()
    assert approvers.assign(Pull(), 2) == (DRY_RUN_APPROVER, DRY_RUN_APPROVER)


def test_the_dry_run_approvers_never_approve() -> None:
    with pytest.raises(RuntimeError, match="a dry run never approves"):
        DryRunApprovers().client(DRY_RUN_APPROVER)


def test_the_dry_run_approver_names_itself() -> None:
    assert repr(DRY_RUN_APPROVER) == "DRY_RUN_APPROVER"
    assert repr(DryRunMerger()) == "DryRunMerger()"
