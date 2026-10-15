"""The names the Gitea instance gives things, from Gitea Actions variables. Variables: docs/configuration.md.

Never from the repository's files: its commits must work unchanged on another instance, with other
labels, accounts and default branch.
"""

import dataclasses

# Settings field -> the environment variable the workflow fills from the Actions variable.
VARIABLES = {
    "automerge": "MERGEWEASEL_AUTOMERGE_LABEL",
    "needs_fix": "MERGEWEASEL_NEEDS_FIX_LABEL",
    "keep_commits": "MERGEWEASEL_KEEP_COMMITS_LABEL",
    "prio": "MERGEWEASEL_PRIO_LABEL",
    "renovate": "MERGEWEASEL_RENOVATE_ACCOUNT",
}


class SettingsError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Settings:
    # The repository's default branch: the queue lands on it and releases it.
    base_branch: str
    automerge: str = "automerge"
    needs_fix: str = "needs-fix"
    keep_commits: str = "keep-commits"
    prio: str = "prio"
    # None: the Renovate rule is off - a trusted account is never a default.
    renovate: str | None = None
    # True if the keep-commits variable names a label: then it has to exist.
    keep_commits_required: bool = False
    # True if the prio variable names a label: then it has to exist.
    prio_required: bool = False

    def labels(self):
        return (self.automerge, self.needs_fix, self.keep_commits, self.prio)

    def describe(self):
        renovate = f"Renovate account {self.renovate}" if self.renovate else "Renovate rule off"
        return f"settings: base branch {self.base_branch}; labels {', '.join(self.labels())}; {renovate}"


def read(gitea, environ):
    """An empty or unset variable keeps its default."""
    values = {field: environ.get(variable, "").strip() for field, variable in VARIABLES.items()}
    settings = Settings(
        gitea.default_branch(),
        **{field: value for field, value in values.items() if value},
        keep_commits_required=bool(values["keep_commits"]),
        prio_required=bool(values["prio"]),
    )
    if len(set(settings.labels())) < len(settings.labels()):
        raise SettingsError(f"the label variables must name four different labels, not {', '.join(settings.labels())}")
    return settings


def required_labels(settings):
    """The labels that must exist: the queue cannot work without automerge and needs-fix.

    keep-commits and prio are optional - without them every pull request is squashed and none goes
    first - unless their variable names one, which then has to exist; naming the default counts too.
    """
    optional = ((settings.keep_commits, settings.keep_commits_required), (settings.prio, settings.prio_required))
    return (settings.automerge, settings.needs_fix, *(label for label, required in optional if required))


def missing_labels(gitea, required):
    """The required labels neither the repository nor its organization has, None if that is unknown.

    Unknown: the organization's labels are unreadable - a user's repository, or a token without access.
    """
    names = set(gitea.labels())
    missing = [name for name in required if name not in names]
    if not missing:
        return []
    organization = gitea.org_labels()
    if organization is None:
        return None
    return [name for name in required if name not in names | set(organization)]
