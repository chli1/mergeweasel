"""The accounts a pass acts as: the merger, and the approver accounts that restore approvals.

The DryRun variants stand in for an account whose token a dry run lacks; only `mergeweasel pass
--dry-run` builds them. How and why: docs/design.md.
"""
import dataclasses


@dataclasses.dataclass(frozen=True)
class Merger:
    login: str

    def owns(self, login):
        return login == self.login


@dataclasses.dataclass(frozen=True)
class DryRunMerger:
    """A dry run without the merger token: every carry-over and every push counts as the merger's."""

    def owns(self, login):
        return True


class DryRunApprover:
    """The approver a dry run without approver tokens assumes for each approval."""

    def __repr__(self):
        return "DRY_RUN_APPROVER"


DRY_RUN_APPROVER = DryRunApprover()


@dataclasses.dataclass(frozen=True)
class Approvers:
    # Login -> client, in the secret's order: the order the approvals are assigned in.
    clients: dict

    def any(self):
        """Renovate's update can be approved; without approver accounts it is approved by hand."""
        return bool(self.clients)

    def logins(self):
        return tuple(self.clients)

    def assign(self, pull, count):
        """count approver logins for the pull request, None if too few accounts can approve it."""
        # Gitea refuses approving one's own pull request; an approval it ignored is not repeated.
        usable = [login for login in self.clients if login != pull.poster and login not in pull.approved_head]
        if len(usable) < count:
            return None
        return tuple(usable[:count])

    def client(self, login):
        return self.clients[login]


@dataclasses.dataclass(frozen=True)
class DryRunApprovers:
    """A dry run without approver tokens: enough approver accounts assumed, none to approve with."""

    def any(self):
        return True

    def logins(self):
        return ()

    def assign(self, pull, count):
        return (DRY_RUN_APPROVER,) * count

    def client(self, login):
        raise RuntimeError("a dry run never approves")
