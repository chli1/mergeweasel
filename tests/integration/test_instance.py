"""Every default changed: the instance's names - a default branch other than main, labels named
otherwise - and the repository's mergeweasel.toml."""

from collections.abc import Callable

import pytest
from driver import Repo

CONFIG = """[release]
enabled = true
tag = "{YYYY}.{MM}.{DD}.{count}+{sha}"
minor_types = ["feature"]
patch_types = ["fix"]

[commits]
squash_append_pull_reference = false
subject = ["[{key}] {conventional} {description}", "{conventional} {description} (PR #{pull})"]
projects = ["PROJ"]
scopes = ["app"]
types = ["feature", "fix", "ci"]
max_line = 100
"""
TITLE = "[PROJ-1] feature(app): add a"
# The second line is longer than the default limit and within max_line.
BODY = (
    "Adds a.\n\n## Commit\n\n- adds a\n"
    "- adds a, with a line longer than the default limit of 72 characters, kept by max_line\n"
)
RUN_URL = "https://ci.example.com/runs/1"


@pytest.mark.needs_new_image  # on Forgejo: the Forgejo client, removing labels by name
@pytest.mark.instance(branch="trunk", labels=("merge-me", "broken", "no-squash", "hotfix"))
def test_the_queue_works_with_every_default_changed(repo: Repo, run_pass: Callable[..., str]) -> None:
    repo.move_main({"mergeweasel.toml": CONFIG}, "[PROJ-1] ci(app): configure mergeweasel")
    repo.commit("author", "trunk", {"b.txt": "b\n"}, "add b", new_branch="add-b")
    red_number = repo.open_pull("author", "add-b", "[PROJ-2] feature: add b", BODY)
    repo.ready(red_number, state="failure", target_url=RUN_URL)
    kept = repo.commit("author", "trunk", {"a.txt": "a\n"}, TITLE, new_branch="add-a")
    number = repo.open_pull("author", "add-a", TITLE, BODY)
    repo.add_label("author", number, "no-squash")
    repo.ready(number)

    output = run_pass(mergeable=number)

    assert (
        "settings: base branch trunk; labels merge-me, broken, no-squash, hotfix; Renovate account renovate" in output
    )
    assert repo.labels(red_number) == {"merge-me", "broken"}
    assert "remove `broken`" in repo.comments_by("queue", red_number)[0]
    # no-squash fast-forwards: trunk's head is the pull request's own commit.
    assert repo.main_sha() == kept
    assert repo.pull(number)["merged"]
    assert "merge-me" not in repo.labels(number)

    # The merge wakes the next pass; it releases trunk with trunk's templates.
    run_pass()

    names = [name for name, sha in repo.tags().items() if sha == kept]
    assert len(names) == 1, names
    assert repo.release(names[0])["body"] == "## Features\n\n- [PROJ-1] app: add a\n"
