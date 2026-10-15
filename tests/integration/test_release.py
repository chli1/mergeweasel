"""The release step against a real Gitea: the tag, the release and its notes."""

import datetime
import re
from collections.abc import Callable

import pytest
from driver import Repo

TITLE = "fix: repair a"
RELEASE_ON = "[release]\nenabled = true\n"


def merge_through_the_queue(
    repo: Repo, run_pass: Callable[..., str], title: str, branch: str = "change"
) -> tuple[int, str]:
    """The pull request's number and main's head after the queue merged it."""
    repo.commit("author", "main", {f"{branch}.txt": "x\n"}, "change", new_branch=branch)
    number = repo.open_pull("author", branch, title)
    repo.ready(number)
    run_pass(mergeable=number)
    assert repo.pull(number)["merged"]
    return number, repo.main_sha()


def switch_on(repo: Repo, text: str = RELEASE_ON) -> None:
    repo.move_main({"mergeweasel.toml": text}, "ci: release (#1)")


@pytest.mark.needs_new_image  # on Forgejo: the Forgejo client, removing labels by name
def test_a_fix_releases_the_next_patch(repo: Repo, run_pass: Callable[..., str]) -> None:
    repo.tag("v1.0.0")
    switch_on(repo)
    number, merged = merge_through_the_queue(repo, run_pass, TITLE)

    # The merge wakes the next pass; it releases first.
    output = run_pass()

    assert repo.tags()["v1.0.1"] == merged
    published = repo.release("v1.0.1")
    assert published["name"] == "v1.0.1"
    assert published["body"] == f"## Fixes\n\n- repair a (#{number})\n"
    assert "released v1.0.1" in output


@pytest.mark.needs_new_image  # on Forgejo: the Forgejo client, removing labels by name
def test_docs_release_nothing(repo: Repo, run_pass: Callable[..., str]) -> None:
    repo.tag("v1.0.0")
    switch_on(repo)
    merge_through_the_queue(repo, run_pass, "docs: explain a")

    output = run_pass()

    assert set(repo.tags()) == {"v1.0.0"}
    assert "nothing to release since v1.0.0" in output


@pytest.mark.needs_new_image  # on Forgejo: the Forgejo client, removing labels by name
def test_without_a_version_tag_nothing_is_released(repo: Repo, run_pass: Callable[..., str]) -> None:
    switch_on(repo)
    merge_through_the_queue(repo, run_pass, TITLE)

    output = run_pass()

    assert repo.tags() == {}
    assert "no tag matching v{major}.{minor}.{patch}" in output


def test_gitea_lists_every_tag_ref_in_one_call(repo: Repo) -> None:
    # The release step reads all tags at once; a cap at 50 would hide the base.
    names = {f"v0.0.{number}" for number in range(1, 61)}
    for name in names:
        repo.tag(name)
    refs = repo.read("/git/refs/tags")
    assert {ref["ref"].removeprefix("refs/tags/") for ref in refs} == names


CALVER = '[release]\nenabled = true\ntag = "{YYYY}.{MM}.{DD}.{count}+{sha}"\n'


def utc_dates(*moments: datetime.datetime) -> set[str]:
    return {f"{moment.year}.{moment.month}.{moment.day}" for moment in moments}


def release_on(repo: Repo, sha: str) -> str:
    names = [name for name, commit in repo.tags().items() if commit == sha]
    assert len(names) == 1, names
    return names[0]


def commit_count(repo: Repo, sha: str) -> int:
    return len(repo.read(f"/commits?sha={sha}&stat=false&verification=false&files=false&limit=50"))


@pytest.mark.needs_new_image  # on Forgejo: the Forgejo client, removing labels by name
def test_calver_releases_from_the_first_releasing_merge(repo: Repo, run_pass: Callable[..., str]) -> None:
    before = datetime.datetime.now(datetime.UTC)
    switch_on(repo, CALVER)
    number, first = merge_through_the_queue(repo, run_pass, TITLE)

    run_pass()

    name = release_on(repo, first)
    match = re.fullmatch(r"(\d{4}\.\d+\.\d+)\.(\d+)\+([0-9a-f]{7})", name)
    assert match is not None, name
    date, count, short = match.groups()
    assert date in utc_dates(before, datetime.datetime.now(datetime.UTC))
    assert int(count) == commit_count(repo, first) and short == first[:7]
    assert repo.release(name)["body"] == f"## Fixes\n\n- repair a (#{number})\n"

    number, second = merge_through_the_queue(repo, run_pass, "feat: add b", branch="second")
    run_pass()

    following = release_on(repo, second)
    assert following.endswith(f".{commit_count(repo, second)}+{second[:7]}")
    assert repo.release(following)["body"] == f"## Features\n\n- add b (#{number})\n"


@pytest.mark.needs_new_image  # on Forgejo: the Forgejo client, removing labels by name
def test_by_default_a_tagged_repository_gets_no_release(repo: Repo, run_pass: Callable[..., str]) -> None:
    repo.tag("v1.0.0")
    merge_through_the_queue(repo, run_pass, TITLE)

    output = run_pass()

    assert set(repo.tags()) == {"v1.0.0"}
    assert "to release, set release.enabled = true in mergeweasel.toml" in output
