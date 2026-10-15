"""The accounts a pass acts as: the merger, and the approver accounts that restore approvals.

The DryRun variants stand in for an account whose token a dry run lacks; only `mergeweasel pass
--dry-run` builds them. How and why: docs/design.md.
"""

import dataclasses
from typing import NoReturn, Protocol

from mergeweasel.api import Client


@dataclasses.dataclass(frozen=True)
class Merger:
    login: str

    def owns(self, login: str | None) -> bool:
        return login == self.login


@dataclasses.dataclass(frozen=True)
class DryRunMerger:
    """A dry run without the merger token: every carry-over and every push counts as the merger's."""

    def owns(self, login: str | None) -> bool:
        return True


class DryRunApprover:
    """The approver a dry run without approver tokens assumes for each approval."""

    def __repr__(self) -> str:
        return "DRY_RUN_APPROVER"


DRY_RUN_APPROVER = DryRunApprover()
# An approver account's login, or DRY_RUN_APPROVER in a dry run.
Approver = str | DryRunApprover


class Approvable(Protocol):
    """What assigning approvers reads of a pull request."""

    @property
    def poster(self) -> str: ...

    @property
    def approved_head(self) -> frozenset[str]: ...


@dataclasses.dataclass(frozen=True)
class Approvers:
    # Login -> client, in the secret's order: the order the approvals are assigned in.
    clients: dict[str, Client]

    def any(self) -> bool:
        """Renovate's update can be approved; without approver accounts it is approved by hand."""
        return bool(self.clients)

    def logins(self) -> tuple[str, ...]:
        return tuple(self.clients)

    def assign(self, pull: Approvable, count: int) -> tuple[str, ...] | None:
        """count approver logins for the pull request, None if too few accounts can approve it."""
        # Gitea refuses approving one's own pull request; an approval it ignored is not repeated.
        usable = [login for login in self.clients if login != pull.poster and login not in pull.approved_head]
        if len(usable) < count:
            return None
        return tuple(usable[:count])

    def client(self, login: Approver) -> Client:
        # Only DryRunApprovers assigns DRY_RUN_APPROVER.
        assert isinstance(login, str)
        return self.clients[login]


@dataclasses.dataclass(frozen=True)
class DryRunApprovers:
    """A dry run without approver tokens: enough approver accounts assumed, none to approve with."""

    def any(self) -> bool:
        return True

    def logins(self) -> tuple[str, ...]:
        return ()

    def assign(self, pull: Approvable, count: int) -> tuple[DryRunApprover, ...]:
        return (DRY_RUN_APPROVER,) * count

    def client(self, login: Approver) -> NoReturn:
        raise RuntimeError("a dry run never approves")


# The merger a pass acts as: the real account, or a dry run's stand-in.
MergerAccount = Merger | DryRunMerger
# The approver accounts a pass acts as: the real ones, or a dry run's stand-in.
ApproverAccounts = Approvers | DryRunApprovers
