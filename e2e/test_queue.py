"""Real passes of the image under test against a throwaway Gitea.

The test plays the author, the reviewer, CI and Renovate; the pass does the rest. Comment texts are
asserted only for the cause they name - their wording is the unit tests' business.
"""
import pytest

from driver import ApiError, eventually

TITLE = "feat: add a (#1)"
BODY = "Adds a.\n\n## Commit\n\n- adds a\n"
LINES = "".join(f"line {number}\n" for number in range(1, 11))
RUN_URL = "https://ci.example.com/runs/1"


def changed(line, text):
    """LINES with one line replaced."""
    lines = LINES.splitlines(keepends=True)
    lines[line - 1] = f"{text}\n"
    return "".join(lines)


def test_squash_merge(repo, run_pass):
    repo.commit("author", "main", {"a.txt": "a\n"}, "add a", new_branch="add-a")
    repo.commit("author", "add-a", {"b.txt": "b\n"}, "wip")
    number = repo.open_pull("author", "add-a", TITLE, BODY)
    repo.ready(number)
    before = repo.main_sha()

    run_pass(mergeable=number)

    landed = repo.git_commit(repo.main_sha())
    assert [parent["sha"] for parent in landed["parents"]] == [before]
    # The branch's own commits never land; Gitea's default appends the trailer.
    assert landed["commit"]["message"] == f"{TITLE}\n\n- adds a\n\nCo-authored-by: author <author@example.com>\n"
    assert (landed["author"]["login"], landed["committer"]["login"]) == ("author", "queue")
    assert repo.pull(number)["merged"]
    assert "automerge" not in repo.labels(number)
    assert not [review for review in repo.reviews(number) if review["user"]["login"] == "queue"]


def test_keep_commits_fast_forwards(repo, run_pass):
    first = repo.commit("author", "main", {"a.txt": "a\n"}, "feat: add a (#1)", new_branch="add-a")
    second = repo.commit("author", "add-a", {"a.txt": "a\nb\n"}, "test: cover a (#1)")
    number = repo.open_pull("author", "add-a", TITLE, BODY)
    repo.add_label("author", number, "keep-commits")
    repo.ready(number)

    run_pass(mergeable=number)

    # The same commits, not copies: main's head is the pull request's head.
    assert repo.main_sha() == second
    assert [parent["sha"] for parent in repo.git_commit(second)["parents"]] == [first]
    assert repo.pull(number)["merged"]
    assert "automerge" not in repo.labels(number)


def test_rebase_carries_the_approval_over(repo, run_pass):
    repo.move_main({"a.txt": LINES}, "feat: add a (#1)")
    repo.commit("author", "main", {"a.txt": changed(1, "first")}, "change the first line", new_branch="first-line")
    number = repo.open_pull("author", "first-line", "feat: change the first line (#1)", BODY)
    repo.ready(number)
    old_head = repo.head(number)
    # The same file, no conflict: Gitea dismisses the approval on the rebase.
    repo.move_main({"a.txt": changed(10, "last")}, "feat: change the last line (#1)")

    run_pass(mergeable=number)

    new_head = eventually(lambda: (head := repo.head(number)) != old_head and head)
    assert [parent["sha"] for parent in repo.git_commit(new_head)["parents"]] == [repo.main_sha()]
    eventually(lambda: [review for review in repo.reviews(number)
                        if review["user"]["login"] == "reviewer" and review["dismissed"]])
    carry_over = [(status["status"], status["creator"]["login"], status["description"])
                  for status in repo.statuses(new_head) if status["context"] == "mergeweasel/approval"]
    assert carry_over == [("success", "queue", f"approved at {old_head[:7]}\napproved by reviewer")]
    assert not repo.pull(number)["merged"]

    repo.post_status(new_head, "success")
    # Recorded once Gitea has handled the rebase push: its mergeable check of the new head is queued.
    eventually(lambda: repo.pusher(number, new_head))
    run_pass(mergeable=number)

    assert repo.pull(number)["merged"]
    assert [review for review in repo.reviews(number)
            if review["user"]["login"] == "queue" and review["state"] == "APPROVED" and review["commit_id"] == new_head]


def test_red_checks_flag_needs_fix(repo, run_pass):
    repo.commit("author", "main", {"a.txt": "a\n"}, "add a", new_branch="add-a")
    number = repo.open_pull("author", "add-a", TITLE, BODY)
    repo.ready(number, state="failure", target_url=RUN_URL)

    run_pass(mergeable=number)

    assert repo.labels(number) == {"automerge", "needs-fix"}
    [comment] = repo.comments_by("queue", number)
    assert f"`ci/test`: failure - {RUN_URL}" in comment
    assert not repo.pull(number)["merged"]


def test_conflict_flags_needs_fix(repo, run_pass):
    repo.move_main({"a.txt": LINES}, "feat: add a (#1)")
    repo.commit("author", "main", {"a.txt": changed(1, "ours")}, "change the first line", new_branch="ours")
    number = repo.open_pull("author", "ours", "feat: change the first line (#1)", BODY)
    repo.ready(number)
    head = repo.head(number)
    repo.move_main({"a.txt": changed(1, "theirs")}, "feat: change the first line differently (#1)")

    # No wait for mergeable: the pull request conflicts, Gitea's rebase answers 409.
    run_pass()

    assert "needs-fix" in repo.labels(number)
    [comment] = repo.comments_by("queue", number)
    assert "conflict" in comment
    assert repo.head(number) == head


def test_invalid_commit_message_flags_needs_fix(repo, run_pass):
    repo.commit("author", "main", {"a.txt": "a\n"}, "add a", new_branch="add-a")
    number = repo.open_pull("author", "add-a", "add a", BODY)
    repo.ready(number)

    run_pass(mergeable=number)

    assert "needs-fix" in repo.labels(number)
    [comment] = repo.comments_by("queue", number)
    assert "invalid" in comment
    assert not repo.pull(number)["merged"]


def test_renovate_push_clears_needs_fix(repo, run_pass):
    repo.commit("renovate", "main", {"a.txt": "a 1\n"}, "fix(deps): update a (#1)", new_branch="renovate/a")
    number = repo.open_pull("renovate", "renovate/a", "fix(deps): update a (#1)", BODY)
    repo.ready(number, state="failure", target_url=RUN_URL, labeller="renovate")
    run_pass(mergeable=number)
    assert "needs-fix" in repo.labels(number)

    retry = repo.commit("renovate", "renovate/a", {"a.txt": "a 2\n"}, "fix(deps): update a (#1)")
    # The Gitea behaviour step 0 rests on: the timeline names renovate as the pusher.
    assert eventually(lambda: repo.pusher(number, retry)) == "renovate"
    run_pass(mergeable=number)

    assert repo.labels(number) == {"automerge"}
    # The new head has no ci/test yet: pending, not merged.
    assert not repo.pull(number)["merged"]


@pytest.mark.required_approvals(2)
def test_rebase_restores_two_approvals_through_two_approvers(repo, run_pass):
    approvers = ("approver1", "approver2")
    repo.move_main({"a.txt": LINES}, "feat: add a (#1)")
    repo.commit("author", "main", {"a.txt": changed(1, "first")}, "change the first line", new_branch="first-line")
    number = repo.open_pull("author", "first-line", "feat: change the first line (#1)", BODY)
    repo.post_status(repo.head(number), "success")
    repo.approve(number)
    repo.approve(number, account="reviewer2")
    repo.add_label("author", number, "automerge")
    old_head = repo.head(number)
    repo.move_main({"a.txt": changed(10, "last")}, "feat: change the last line (#1)")

    run_pass(mergeable=number, approvers=approvers)

    new_head = eventually(lambda: (head := repo.head(number)) != old_head and head)
    eventually(lambda: len([review for review in repo.reviews(number)
                            if review["user"]["login"] in ("reviewer", "reviewer2") and review["dismissed"]]) == 2)
    carry_over = [status["description"] for status in repo.statuses(new_head) if status["context"] == "mergeweasel/approval"]
    assert carry_over == [f"approved at {old_head[:7]}\napproved by reviewer\napproved by reviewer2"]

    repo.post_status(new_head, "success")
    eventually(lambda: repo.pusher(number, new_head))
    run_pass(mergeable=number, approvers=approvers)

    assert repo.pull(number)["merged"]
    restored = {review["user"]["login"] for review in repo.reviews(number)
                if review["state"] == "APPROVED" and review["commit_id"] == new_head and not review["dismissed"]}
    assert restored == set(approvers)


def test_gitea_refuses_approving_ones_own_pull(repo):
    # The approver choice skips the poster because of it.
    repo.commit("author", "main", {"a.txt": "a\n"}, "add a", new_branch="add-a")
    number = repo.open_pull("author", "add-a", TITLE, BODY)
    with pytest.raises(ApiError) as refusal:
        repo.approve(number, account="author")
    assert refusal.value.code == 422


def test_renovates_own_update_is_approved_and_merged(repo, run_pass):
    number = repo.open_renovate_pull("renovate/a", {"a.txt": "a 1\n"})

    run_pass(mergeable=number, approvers=("approver1",))

    assert repo.pull(number)["merged"]
    [approval] = [review for review in repo.reviews(number) if review["state"] == "APPROVED"]
    assert approval["user"]["login"] == "approver1"
    assert "renovate" in approval["body"]


def test_renovate_update_someone_else_pushed_to_waits_for_a_human(repo, run_pass):
    number = repo.open_renovate_pull("renovate/a", {"a.txt": "a 1\n"})
    pushed = repo.commit("author", "renovate/a", {"a.txt": "a 2\n"}, "fix(deps): update a (#1)")
    eventually(lambda: repo.pusher(number, pushed))
    repo.post_status(pushed, "success")

    run_pass(mergeable=number, approvers=("approver1",))

    assert not repo.pull(number)["merged"]
    assert not [review for review in repo.reviews(number) if review["state"] == "APPROVED"]


def test_renovates_update_labelled_at_creation_is_approved_and_merged(repo, run_pass):
    # Gitea must record the label set in the creating call as renovate's, or no update is trusted.
    number = repo.open_renovate_pull("renovate/a", {"a.txt": "a 1\n"}, label_at_creation=True)
    assert "automerge" in repo.labels(number)

    run_pass(mergeable=number, approvers=("approver1",))

    assert repo.pull(number)["merged"]


def test_renovates_update_stays_trusted_across_the_queues_rebase(repo, run_pass):
    number = repo.open_renovate_pull("renovate/a", {"a.txt": "a 1\n"})
    old_head = repo.head(number)
    repo.move_main({"b.txt": "b\n"}, "feat: add b (#1)")

    run_pass(mergeable=number, approvers=("approver1",))

    new_head = eventually(lambda: (head := repo.head(number)) != old_head and head)
    # The rule rests on it: Gitea records the queue's rebase as the merger's push.
    assert eventually(lambda: repo.pusher(number, new_head)) == "queue"
    assert not repo.pull(number)["merged"]

    repo.post_status(new_head, "success")
    run_pass(mergeable=number, approvers=("approver1",))

    assert repo.pull(number)["merged"]
    assert [review for review in repo.reviews(number)
            if review["user"]["login"] == "approver1" and review["state"] == "APPROVED" and review["commit_id"] == new_head]
