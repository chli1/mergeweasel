"""The forges the Docker layers run against: each forge's own behaviour, in its own class."""
import abc
import os

import pytest

# Picks the forge; Gitea when unset.
FORGE_VARIABLE = "MERGEWEASEL_E2E_FORGE"


class Forge(abc.ABC):
    # The value of FORGE_VARIABLE, and part of the Docker names.
    name: str
    title: str
    # Next to this file.
    dockerfile: str

    @abc.abstractmethod
    def squash_message(self, message, co_author):
        """The message a squash merge lands, for `message` written by `co_author`, not the merger."""


class Gitea(Forge):
    name = "gitea"
    title = "Gitea"
    dockerfile = "gitea.Dockerfile"

    def squash_message(self, message, co_author):
        # ADD_CO_COMMITTER_TRAILERS, on by default.
        return f"{message}\nCo-authored-by: {co_author}\n"


class Forgejo(Forge):
    name = "forgejo"
    title = "Forgejo"
    dockerfile = "forgejo.Dockerfile"

    def squash_message(self, message, co_author):
        return message


FORGES = {forge.name: forge for forge in (Gitea(), Forgejo())}


def selected():
    name = os.environ.get(FORGE_VARIABLE, "gitea")
    if name not in FORGES:
        pytest.fail(f"{FORGE_VARIABLE}={name} names no forge - one of: {', '.join(FORGES)}")
    return FORGES[name]
