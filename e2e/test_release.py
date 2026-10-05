"""The release step against a real Gitea: the tag, the release and its notes."""

TITLE = "fix: repair a (#1)"


def merge_through_the_queue(repo, run_pass, title, branch="change"):
    repo.commit("author", "main", {f"{branch}.txt": "x\n"}, "change", new_branch=branch)
    number = repo.open_pull("author", branch, title)
    repo.ready(number)
    run_pass(mergeable=number)
    assert repo.pull(number)["merged"]
    return repo.main_sha()


def test_a_fix_releases_the_next_patch(repo, run_pass):
    repo.tag("v1.0.0")
    merged = merge_through_the_queue(repo, run_pass, TITLE)

    # The merge wakes the next pass; it releases first.
    output = run_pass()

    assert repo.tags()["v1.0.1"] == merged
    published = repo.release("v1.0.1")
    assert published["name"] == "v1.0.1"
    assert published["body"] == "## Fixes\n\n- repair a (#1)\n"
    assert "released v1.0.1" in output


def test_docs_release_nothing(repo, run_pass):
    repo.tag("v1.0.0")
    merge_through_the_queue(repo, run_pass, "docs: explain a (#1)")

    output = run_pass()

    assert set(repo.tags()) == {"v1.0.0"}
    assert "nothing to release since v1.0.0" in output


def test_without_a_version_tag_nothing_is_released(repo, run_pass):
    merge_through_the_queue(repo, run_pass, TITLE)

    output = run_pass()

    assert repo.tags() == {}
    assert "no vX.Y.Z tag" in output


def test_gitea_lists_every_tag_ref_in_one_call(repo):
    # The release step reads all tags at once; a cap at 50 would hide the base.
    names = {f"v0.0.{number}" for number in range(1, 61)}
    for name in names:
        repo.tag(name)
    refs = repo.read("/git/refs/tags")
    assert {ref["ref"].removeprefix("refs/tags/") for ref in refs} == names
