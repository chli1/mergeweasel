"""The queue on a real runner: Gitea starts the workflows, nobody dispatches a pass. End states only."""
from actions import CI_CONTEXT, settle

BODY = "Adds {name}.{marker}\n\n## Commit\n\n- adds {name}\n"


def open_approved(repo, name, marker=""):
    """A pull request whose CI runs, approved by the reviewer."""
    repo.commit("author", repo.branch, {f"{name}.txt": f"{name}\n"}, f"add {name}", new_branch=f"add-{name}")
    number = repo.open_pull("author", f"add-{name}", f"feat: add {name}", BODY.format(name=name, marker=marker))
    repo.approve(number)
    return number


def ci_state(repo, number):
    return repo.states(repo.head(number)).get(CI_CONTEXT)


def merged(repo, number):
    return repo.pull(number)["merged"]


def test_the_label_comes_last(actions_repo, runner):
    repo = actions_repo
    number = open_approved(repo, "a")
    settle(repo, runner, lambda: ci_state(repo, number) == "success")

    repo.add_label("author", number, repo.automerge)

    settle(repo, runner, lambda: merged(repo, number))


def test_ci_comes_last(actions_repo, runner):
    repo = actions_repo
    number = open_approved(repo, "b", marker=" ci-slow")
    repo.add_label("author", number, repo.automerge)

    # The label's pass finds CI still running and leaves the pull request: CI finishing has to wake the queue.
    settle(repo, runner, lambda: any(workflow == "mergeweasel-queue.yml" and status in ("success", "failure")
                                     for workflow, _, status in repo.runs()))
    assert ci_state(repo, number) != "success", "CI finished before a pass ran - the scenario tests nothing"

    settle(repo, runner, lambda: merged(repo, number))


def test_a_moved_main_is_rebased_and_tested_again(actions_repo, runner):
    repo = actions_repo
    number = open_approved(repo, "c")
    settle(repo, runner, lambda: ci_state(repo, number) == "success")
    moved = repo.move_main({"other.txt": "other\n"}, "chore: move main (#1)")

    repo.add_label("author", number, repo.automerge)

    settle(repo, runner, lambda: merged(repo, number))
    # The squash commit sits on the moved main: the queue rebased, CI ran again, the queue merged.
    assert [parent["sha"] for parent in repo.git_commit(repo.main_sha())["parents"]] == [moved]


def test_red_ci_needs_a_fix(actions_repo, runner):
    repo = actions_repo
    number = open_approved(repo, "d", marker=" ci-fail")
    repo.add_label("author", number, repo.automerge)

    # The pass adds the label, then comments: the comment is the end state.
    settle(repo, runner, lambda: any("required checks failed" in body for body in repo.comments_by("queue", number)))
    assert "needs-fix" in repo.labels(number)
    assert not merged(repo, number)


def test_the_next_moves_up_after_a_merge(actions_repo, runner):
    repo = actions_repo
    first, second = open_approved(repo, "e"), open_approved(repo, "f")
    settle(repo, runner, lambda: ci_state(repo, first) == ci_state(repo, second) == "success")

    repo.add_label("author", first, repo.automerge)
    repo.add_label("author", second, repo.automerge)

    settle(repo, runner, lambda: merged(repo, first) and merged(repo, second), timeout=600)
