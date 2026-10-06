"""Another instance's names: a default branch other than main, labels named otherwise."""
import pytest

TITLE = "feat: add a (#1)"
BODY = "Adds a.\n\n## Commit\n\n- adds a\n"
RUN_URL = "https://ci.example.com/runs/1"


@pytest.mark.instance(branch="trunk", labels=("merge-me", "broken", "no-squash"))
def test_the_queue_works_with_the_instances_names(repo, run_pass):
    red = repo.commit("author", "trunk", {"b.txt": "b\n"}, "add b", new_branch="add-b")
    red_number = repo.open_pull("author", "add-b", "feat: add b (#1)", BODY)
    repo.ready(red_number, state="failure", target_url=RUN_URL)
    kept = repo.commit("author", "trunk", {"a.txt": "a\n"}, TITLE, new_branch="add-a")
    number = repo.open_pull("author", "add-a", TITLE, BODY)
    repo.add_label("author", number, "no-squash")
    repo.ready(number)

    output = run_pass(mergeable=number)

    assert "settings: base branch trunk; labels merge-me, broken, no-squash; Renovate account renovate" in output
    assert repo.labels(red_number) == {"merge-me", "broken"}
    assert "remove `broken`" in repo.comments_by("queue", red_number)[0]
    # no-squash fast-forwards: trunk's head is the pull request's own commit.
    assert repo.main_sha() == kept
    assert repo.pull(number)["merged"]
    assert "merge-me" not in repo.labels(number)
