from unittest import mock

import pytest

from mergeweasel import gitea as gitea_module, queue
from mergeweasel.accounts import DRY_RUN_APPROVER, Approvers, DryRunApprovers, DryRunMerger, Merger
from mergeweasel.commit_rule import Commit, SquashMessage
from mergeweasel.gitea import (LABEL_ADDED, LABEL_REMOVED, PUSH, ApiError, Branch, CommitStatus, Conflict, Gitea,
                               MergeDeferred, MergeRefused, NotFound, PullRequest, Review, TimelineEvent, Unreachable)
from mergeweasel.settings import Settings

QUEUE = "weasel-bot"
APPROVER = "approver-bot"
MERGER = Merger(QUEUE)

TEST = "build-and-push / test (pull_request)"
SMOKE = "build-and-push / image-smoke (pull_request)"
REQUIRED = [TEST, SMOKE]
# The default names, with the Renovate rule on.
SETTINGS = Settings("main", renovate="renovate")


def statuses(**states):
    """statuses(test="success", smoke="failure") - keyword per required context."""
    contexts = {"test": TEST, "smoke": SMOKE}
    return {contexts[key]: queue.Status(contexts[key], state, f"https://ci/{key}")
            for key, state in states.items()}


def green():
    return statuses(test="success", smoke="success")


def pull(number, *, labels=("automerge",), behind=False, approvers=("chli1",), carried=(), poster="author",
         approved_head=frozenset(), checks=None, draft=False, rejected=False, renovate_retried=False,
         renovate_trusted=False):
    return queue.Pull(
        number=number, head_ref=f"branch-{number}", head_sha=f"{number:040x}",
        labels=frozenset(labels), draft=draft, behind=behind,
        poster=poster, approvers=approvers, carried=carried, approved_head=approved_head, rejected=rejected,
        renovate_retried=renovate_retried, renovate_trusted=renovate_trusted, statuses=green() if checks is None else checks)


def approvers_of(*logins):
    """Approver accounts for decide, which never needs their clients."""
    return Approvers(dict.fromkeys(logins))


def decide(pulls, required, needed=1, approvers=approvers_of(APPROVER)):
    return queue.decide(pulls, required, needed, approvers, SETTINGS)


def merge_action(number, approvals=(), keep_commits=False):
    return queue.Merge(number, f"{number:040x}", approvals=approvals, keep_commits=keep_commits)


def land(number, message, approvals=(), keep_commits=False):
    return queue.Land(merge_action(number, approvals, keep_commits), message)


def run_pass(gitea, server, dry_run, approvers=None):
    """A pass as the merger QUEUE; approvers maps logins to clients, none by default."""
    return queue.run_pass(gitea, server, dry_run, MERGER, Approvers({} if approvers is None else approvers), SETTINGS)


# --- decide: selection ---

def test_idle_without_queued_pulls():
    assert decide([], REQUIRED) == queue.Idle()


def test_lowest_number_first_when_all_are_behind():
    action = decide([pull(5, behind=True), pull(3, behind=True)], REQUIRED)
    assert action == queue.Rebase(3, "branch-3", f"{3:040x}", ("chli1",))


def test_up_to_date_pull_beats_a_lower_number():
    pending = statuses(test="pending", smoke="pending")
    action = decide([pull(2, behind=True), pull(7, checks=pending)], REQUIRED)
    assert action == queue.Wait(7)


def test_unapproved_pull_is_not_queued():
    assert decide([pull(1, approvers=())], REQUIRED) == queue.Idle()


def test_needs_fix_draft_and_unlabelled_pulls_are_not_queued():
    pulls = [pull(1, labels=("automerge", "needs-fix")), pull(2, draft=True), pull(3, labels=())]
    assert decide(pulls, REQUIRED) == queue.Idle()


def test_pull_with_a_standing_request_for_changes_is_not_queued():
    assert decide([pull(1, carried=("chli1",), rejected=True)], REQUIRED) == queue.Idle()


def test_red_pull_behind_main_is_rebased_not_flagged():
    red = statuses(test="failure", smoke="success")
    action = decide([pull(4, behind=True, checks=red)], REQUIRED)
    assert isinstance(action, queue.Rebase)


# --- decide: verdict ---

def test_red_up_to_date_pull_is_flagged_with_its_failed_checks():
    red = statuses(test="failure", smoke="success")
    action = decide([pull(4, checks=red)], REQUIRED)
    assert isinstance(action, queue.FlagNeedsFix)
    assert action.number == 4
    assert TEST in action.comment and "https://ci/test" in action.comment
    assert SMOKE not in action.comment
    assert "Re-run the failed jobs" in action.comment


def test_missing_required_context_is_pending():
    action = decide([pull(1, checks=statuses(test="success"))], REQUIRED)
    assert action == queue.Wait(1)


def test_failure_wins_over_a_missing_context():
    action = decide([pull(1, checks=statuses(test="failure"))], REQUIRED)
    assert isinstance(action, queue.FlagNeedsFix)


def test_skipped_counts_as_success_and_warning_as_failure():
    skipped = statuses(test="success", smoke="skipped")
    assert isinstance(decide([pull(1, checks=skipped)], REQUIRED), queue.Merge)
    warning = statuses(test="success", smoke="warning")
    assert isinstance(decide([pull(1, checks=warning)], REQUIRED), queue.FlagNeedsFix)


def test_required_contexts_are_glob_patterns():
    red = statuses(test="success", smoke="failure")
    action = decide([pull(1, checks=red)], ["build-and-push / *"])
    assert isinstance(action, queue.FlagNeedsFix)


def test_without_required_contexts_nothing_merges():
    assert decide([pull(1)], []) == queue.Wait(1)


# --- decide: merge and approval ---

def test_green_pull_with_a_valid_approval_merges_without_approving():
    assert decide([pull(6)], REQUIRED) == merge_action(6)


def test_green_pull_with_only_a_carry_over_is_restored_through_an_approver():
    action = decide([pull(6, approvers=(), carried=("chli1",))], REQUIRED)
    assert action == merge_action(6, approvals=(queue.Restored(APPROVER, "chli1"),))


def test_no_required_approvals_queue_an_unapproved_pull():
    assert decide([pull(1, approvers=())], REQUIRED, needed=0) == merge_action(1)


def test_two_required_approvals_need_two_standing_reviewers():
    assert decide([pull(1, approvers=("chli1",))], REQUIRED, needed=2) == queue.Idle()
    action = decide([pull(1, approvers=("chli1",), carried=("alice",))], REQUIRED, needed=2)
    assert action == merge_action(1, approvals=(queue.Restored(APPROVER, "alice"),))


def test_a_login_counts_once_towards_the_required_approvals():
    assert decide([pull(1, approvers=("chli1",), carried=("chli1",))], REQUIRED, needed=2) == queue.Idle()


def test_only_the_missing_approvals_are_restored():
    action = decide([pull(1, approvers=("chli1",), carried=("chli1", "alice", "bob"))], REQUIRED,
                    needed=2, approvers=approvers_of("a1", "a2"))
    assert action == merge_action(1, approvals=(queue.Restored("a1", "alice"),))


def test_approvers_go_in_secret_order_skipping_the_poster_and_head_approvers():
    candidate = pull(1, approvers=(), carried=("alice", "bob"), poster="a1", approved_head=frozenset({"a2"}))
    action = decide([candidate], REQUIRED, needed=2, approvers=approvers_of("a1", "a2", "a3", "a4"))
    assert action == merge_action(1, approvals=(queue.Restored("a3", "alice"), queue.Restored("a4", "bob")))


def test_too_few_approver_accounts_flag_needs_fix():
    action = decide([pull(1, approvers=(), carried=("alice", "bob"))], REQUIRED, needed=2, approvers=approvers_of("a1"))
    assert isinstance(action, queue.FlagNeedsFix)
    assert action.number == 1 and "too few approver accounts" in action.comment


def test_without_approver_accounts_a_carry_over_flags_needs_fix():
    action = decide([pull(1, approvers=(), carried=("chli1",))], REQUIRED, approvers=approvers_of())
    assert isinstance(action, queue.FlagNeedsFix)


def test_an_approver_whose_approval_gitea_ignores_is_not_asked_again():
    candidate = pull(1, approvers=(), carried=("chli1",), approved_head=frozenset({APPROVER}))
    assert isinstance(decide([candidate], REQUIRED), queue.FlagNeedsFix)


def test_dry_run_approvers_are_assumed_enough():
    action = decide([pull(1, approvers=(), carried=("alice", "bob"))], REQUIRED, needed=2, approvers=DryRunApprovers())
    assert action == merge_action(1, approvals=(queue.Restored(DRY_RUN_APPROVER, "alice"),
                                                queue.Restored(DRY_RUN_APPROVER, "bob")))


def test_rebase_carries_every_standing_reviewer():
    action = decide([pull(8, behind=True, approvers=("alice", "bob"))], REQUIRED)
    assert action.reviewers == ("alice", "bob")


def test_a_second_rebase_does_not_list_restorations_again():
    candidate = pull(8, behind=True, approvers=(APPROVER, "carol"), carried=("alice",))
    assert decide([candidate], REQUIRED).reviewers == ("alice", "carol")


def test_without_a_carry_over_an_approver_account_is_a_reviewer():
    assert decide([pull(8, behind=True, approvers=(APPROVER,))], REQUIRED).reviewers == (APPROVER,)


# --- parsers ---

def review(state="APPROVED", login="chli1", official=True, dismissed=False, stale=False, commit_id="0" * 40):
    return Review(login, state, official, dismissed, stale, commit_id)


def test_request_for_changes_counts_per_reviewers_latest_verdict():
    assert queue.rejected([review(), review(state="REQUEST_CHANGES")])
    assert not queue.rejected([review(state="REQUEST_CHANGES"), review()])
    assert not queue.rejected([review(state="REQUEST_CHANGES"), review(dismissed=True)])
    assert not queue.rejected([review(state="REQUEST_CHANGES", dismissed=True)])
    assert not queue.rejected([review(state="REQUEST_CHANGES", official=False)])
    assert queue.rejected([review(state="REQUEST_CHANGES"), review(state="COMMENT")])


def test_valid_approvers_are_distinct_in_review_order():
    reviews = [review(login="chli1"), review(login="alice"), review(login="chli1")]
    assert queue.valid_approvers(reviews) == ("chli1", "alice")


def test_valid_approvers_ignore_dismissed_stale_unofficial_and_other_reviews():
    reviews = [review(dismissed=True), review(stale=True), review(official=False),
               review(state="COMMENT"), review(state="REQUEST_CHANGES")]
    assert queue.valid_approvers(reviews) == ()


def test_head_approvers_count_on_the_head_undismissed_official_or_not():
    head = "a" * 40
    reviews = [review(login=APPROVER, official=False, commit_id=head), review(login="old", commit_id="b" * 40),
               review(login="gone", dismissed=True, commit_id=head), review(state="COMMENT", login="talk", commit_id=head)]
    assert queue.head_approvers(reviews, head) == frozenset({APPROVER})


def status_json(context="mergeweasel/approval", state="success", creator=QUEUE,
                description="approved at 1234567\napproved by chli1"):
    return CommitStatus(context, state, "/org/repo/actions/runs/1/jobs/2", description, creator)


def test_carry_over_names_its_reviewers():
    description = "approved at 1234567\napproved by chli1\napproved by alice"
    assert queue.carried_reviewers([status_json(description=description)], MERGER) == ("chli1", "alice")


def test_carry_over_counts_only_from_the_merger():
    assert queue.carried_reviewers([status_json(creator="stranger")], MERGER) == ()
    assert queue.carried_reviewers([status_json(state="pending")], MERGER) == ()
    assert queue.carried_reviewers([status_json(context="other")], MERGER) == ()


@pytest.mark.parametrize("description", [
    "tampered", "", "approved at 1234567", "approved at 1234567\napproved by a b",
    "approved at 1234567\napproved by chli1\nextra", "approved at xyz\napproved by chli1",
    # 0.1.x wrote one line; the new image does not trust it.
    "approved by chli1 at 1234567",
])
def test_carry_over_rejects_malformed_and_old_descriptions(description):
    assert queue.carried_reviewers([status_json(description=description)], MERGER) == ()


def test_carry_over_description_round_trips():
    description = queue.carry_over_description(("chli1", "alice"), "abcdef0123456789")
    assert description == "approved at abcdef0\napproved by chli1\napproved by alice"
    assert queue.carried_reviewers([status_json(description=description)], MERGER) == ("chli1", "alice")


def test_the_dry_run_merger_counts_any_carry_over():
    assert queue.carried_reviewers([status_json(creator="someone-else")], DryRunMerger()) == ("chli1",)


def test_the_queues_own_statuses_never_count():
    own = [status_json(), status_json(context="mergeweasel-queue / pass (pull_request_target)", state="failure")]
    assert queue.statuses_by_context(own, "https://gitea.example") == {}


def test_statuses_by_context_makes_links_absolute():
    by_context = queue.statuses_by_context([status_json(context=TEST)], "https://gitea.example")
    assert by_context[TEST] == queue.Status(
        TEST, "success", "https://gitea.example/org/repo/actions/runs/1/jobs/2")


# --- step 0 ---

def event(kind, login, event_id, label="needs-fix", body="1"):
    """kind: Gitea's pull_push or label; body "1" for an added label."""
    if kind == "pull_push":
        return TimelineEvent(event_id, PUSH, login, None)
    return TimelineEvent(event_id, LABEL_ADDED if body == "1" else LABEL_REMOVED, login, label)



def renovate_pushed(timeline):
    return queue.renovate_pushed_since_needs_fix(timeline, SETTINGS)

def test_renovate_push_after_the_flag_retries():
    assert renovate_pushed([event("label", QUEUE, 10), event("pull_push", "renovate", 12)])


def test_older_or_foreign_pushes_retry_nothing():
    assert not renovate_pushed([event("pull_push", "renovate", 8), event("label", QUEUE, 10)])
    assert not renovate_pushed([event("label", QUEUE, 10), event("pull_push", "stranger", 12)])
    assert not renovate_pushed([event("label", QUEUE, 10), event("label", "chli1", 11, label="automerge"),
                                                            event("pull_push", QUEUE, 12)])
    assert not renovate_pushed([event("pull_push", "renovate", 12)])


def test_needs_fix_to_clear_takes_the_retried_flagged_pulls():
    retried = pull(9, labels=("automerge", "needs-fix"), renovate_retried=True)
    assert queue.needs_fix_to_clear([retried, pull(8, labels=("automerge", "needs-fix")), pull(7)], SETTINGS) == [retried]


def test_relabel_changes_names_only():
    pulls = [pull(1, labels=("automerge", "keep-commits")), pull(2)]
    flagged = queue.relabel(pulls, 1, add="needs-fix")
    assert flagged[0].labels == frozenset({"automerge", "keep-commits", "needs-fix"})
    assert flagged[1] == pulls[1]
    assert queue.relabel(flagged, 1, remove="needs-fix")[0].labels == frozenset({"automerge", "keep-commits"})


WRITES = ("rebase", "merge", "approve", "add_label", "remove_label", "comment", "set_status")


@pytest.fixture
def gitea():
    client = mock.create_autospec(Gitea, instance=True)
    client.is_issue.side_effect = lambda number: number == 1
    client.file.return_value = None
    return client


def writes(client):
    """The client's writes in order, as (method, args)."""
    return [(name, args) for name, args, _ in client.mock_calls if name in WRITES]


def answer_pull(gitea, number, title=None, body="", config=None):
    """The pull request and mergeweasel.toml (None: no file) as the queue reads them right before merging."""
    gitea.pull.return_value = PullRequest(number, "main", f"branch-{number}", f"{number:040x}", {"automerge": 0},
                                          False, "author", title or f"ci: land {number} (#1)", body, False)
    gitea.file.return_value = None if config is None else config.encode()


def squash(number, body=""):
    return SquashMessage(f"ci: land {number} (#1)", body, ())


LABEL_IDS = {"automerge": 13, "needs-fix": 14, "keep-commits": 15}


def given(monkeypatch, pulls, needed=1):
    monkeypatch.setattr(queue, "read_state", lambda gitea, server, merger, settings:
                        queue.State("f" * 40, tuple(REQUIRED), needed, pulls, {p.number: LABEL_IDS for p in pulls}))


def approver_clients(log, *logins):
    """One client per approver login; every approval lands in log as (login, number, sha, body)."""
    clients = {}
    for login in logins:
        client = mock.create_autospec(Gitea, instance=True)
        client.approve.side_effect = lambda number, sha, body, login=login: log.append((login, number, sha, body))
        clients[login] = client
    return clients


def test_rebase_sets_the_carry_over_on_the_new_head(monkeypatch, gitea):
    given(monkeypatch, [pull(3, behind=True)])
    new_head = "a" * 40
    gitea.branch_head.return_value = new_head
    run_pass(gitea, "https://gitea", dry_run=False)
    gitea.branch_head.assert_called_once_with("branch-3")
    assert writes(gitea) == [
        ("rebase", (3,)),
        ("set_status", (new_head, "mergeweasel/approval", "success",
                        "approved at " + f"{3:040x}"[:7] + "\napproved by chli1")),
    ]


def test_conflict_flags_needs_fix_and_hands_over(monkeypatch, gitea):
    given(monkeypatch, [pull(1, behind=True), pull(2, behind=True)])
    gitea.rebase.side_effect = [Conflict("rebase failed because of conflict", 409), None]
    gitea.branch_head.return_value = "d" * 40
    action = run_pass(gitea, "https://gitea", dry_run=False)
    done = writes(gitea)
    assert done[1] == ("add_label", (1, "needs-fix"))
    assert done[2][0] == "comment" and done[2][1][0] == 1 and "conflict" in done[2][1][1]
    assert done[3] == ("rebase", (2,))
    assert action == queue.Rebase(2, "branch-2", f"{2:040x}", ("chli1",))


def test_every_candidate_failing_ends_the_pass(monkeypatch, gitea):
    red = statuses(test="failure", smoke="success")
    given(monkeypatch, [pull(1, checks=red), pull(2, behind=True)])
    gitea.rebase.side_effect = Conflict("rebase failed because of conflict", 409)
    action = run_pass(gitea, "https://gitea", dry_run=False)
    assert [args[0] for name, args in writes(gitea) if name == "add_label"] == [1, 2]
    assert action == queue.Idle()


def test_carry_over_survives_a_transient_failure(monkeypatch, gitea):
    monkeypatch.setattr(gitea_module.time, "sleep", lambda seconds: None)
    given(monkeypatch, [pull(3, behind=True)])
    gitea.branch_head.side_effect = [Unreachable("timed out"), "a" * 40]
    run_pass(gitea, "https://gitea", dry_run=False)
    assert writes(gitea)[-1][0] == "set_status" and writes(gitea)[-1][1][0] == "a" * 40


def test_carry_over_gives_up_at_once_on_a_missing_branch(monkeypatch, gitea):
    sleeps = []
    monkeypatch.setattr(gitea_module.time, "sleep", sleeps.append)
    given(monkeypatch, [pull(3, behind=True)])
    gitea.branch_head.side_effect = NotFound("refs/heads/branch-3 not found")
    with pytest.raises(NotFound):
        run_pass(gitea, "https://gitea", dry_run=False)
    assert gitea.branch_head.call_count == 1 and sleeps == []


def test_unexpected_api_error_fails_the_pass(monkeypatch, gitea):
    given(monkeypatch, [pull(1, behind=True)])
    gitea.rebase.side_effect = ApiError("boom", 500, "url")
    with pytest.raises(ApiError):
        run_pass(gitea, "https://gitea", dry_run=False)


def test_merge_restores_each_missing_approval_through_its_approver_then_squashes(monkeypatch, gitea):
    given(monkeypatch, [pull(6, approvers=(), carried=("alice", "bob"))], needed=2)
    answer_pull(gitea, 6)
    log = []
    gitea.merge.side_effect = lambda *args: log.append((QUEUE, "merge", *args))
    run_pass(gitea, "https://gitea", dry_run=False, approvers=approver_clients(log, "a1", "a2"))
    head = f"{6:040x}"
    assert log == [
        ("a1", 6, head, "Approval of alice, carried over the merge queue's rebase."),
        ("a2", 6, head, "Approval of bob, carried over the merge queue's rebase."),
        (QUEUE, "merge", 6, head, squash(6)),
    ]


def test_rebase_without_standing_reviewers_writes_no_carry_over(monkeypatch, gitea):
    given(monkeypatch, [pull(3, behind=True, approvers=())], needed=0)
    run_pass(gitea, "https://gitea", dry_run=False)
    assert writes(gitea) == [("rebase", (3,))]


def test_shortfall_flags_needs_fix_without_approving(monkeypatch, gitea):
    given(monkeypatch, [pull(6, approvers=(), carried=("chli1",))])
    action = run_pass(gitea, "https://gitea", dry_run=False)
    done = writes(gitea)
    assert done[0] == ("add_label", (6, "needs-fix"))
    assert "too few approver accounts" in done[1][1][1]
    assert action == queue.Idle()


def test_merge_removes_the_automerge_label(monkeypatch, gitea):
    given(monkeypatch, [pull(6)])
    answer_pull(gitea, 6)
    run_pass(gitea, "https://gitea", dry_run=False)
    assert writes(gitea) == [("merge", (6, f"{6:040x}", squash(6))), ("remove_label", (6, 13))]


def test_keep_commits_label_reaches_the_merge_action():
    assert decide([pull(6, labels=("automerge", "keep-commits"))], REQUIRED) == merge_action(6, keep_commits=True)


def test_squash_body_is_the_commit_section(monkeypatch, gitea):
    given(monkeypatch, [pull(6)])
    answer_pull(gitea, 6, body="Closes #1\n\n## Commit\nWhy it is so.\n")
    run_pass(gitea, "https://gitea", dry_run=False)
    assert writes(gitea)[0] == ("merge", (6, f"{6:040x}", squash(6, body="Why it is so.")))


def test_keep_commits_fast_forwards_after_checking_every_commit(monkeypatch, gitea):
    given(monkeypatch, [pull(6, labels=("automerge", "keep-commits"))])
    answer_pull(gitea, 6, title="no format needed")
    gitea.pull_commits.return_value = [Commit("a" * 40, "ci: move (#1)", 1), Commit("b" * 40, "ci: edit (#1)", 1)]
    run_pass(gitea, "https://gitea", dry_run=False)
    gitea.pull_commits.assert_called_once_with(6)
    gitea.is_issue.assert_called_once_with(1)
    assert writes(gitea)[0] == ("merge", (6, f"{6:040x}", None))


def test_invalid_title_read_at_merge_time_flags_needs_fix_and_hands_over(monkeypatch, gitea):
    # The CI check passed earlier; the title was edited afterwards. Only the queue's own read counts.
    given(monkeypatch, [pull(6, approvers=(), carried=("chli1",)), pull(7, behind=True)])
    answer_pull(gitea, 6, title="land six")
    gitea.branch_head.return_value = "e" * 40
    log = []
    action = run_pass(gitea, "https://gitea", dry_run=False, approvers=approver_clients(log, APPROVER))
    done = writes(gitea)
    # Neither approved nor merged: the flag comes first.
    assert log == []
    assert done[0] == ("add_label", (6, "needs-fix"))
    assert "PR title: subject must read" in done[1][1][1]
    assert "`## Commit` section" in done[1][1][1]
    assert action == queue.Rebase(7, "branch-7", f"{7:040x}", ("chli1",))


def test_squash_looks_up_the_title_s_issue_at_merge_time(monkeypatch, gitea):
    given(monkeypatch, [pull(6)])
    answer_pull(gitea, 6, title=" ci: land 6 (#1) ", body="## Commit\nSee #4711.")
    run_pass(gitea, "https://gitea", dry_run=False)
    gitea.is_issue.assert_called_once_with(1)
    assert writes(gitea)[0][0] == "merge"


def test_title_referencing_no_issue_flags_needs_fix(monkeypatch, gitea):
    given(monkeypatch, [pull(6)])
    answer_pull(gitea, 6, title="ci: land 6 (#4711)")
    run_pass(gitea, "https://gitea", dry_run=False)
    done = writes(gitea)
    assert done[0] == ("add_label", (6, "needs-fix"))
    assert "PR title: #4711 is not an issue of this repository" in done[1][1][1]


def test_keep_commits_with_a_bad_commit_flags_needs_fix(monkeypatch, gitea):
    given(monkeypatch, [pull(6, labels=("automerge", "keep-commits"))])
    answer_pull(gitea, 6)
    gitea.pull_commits.return_value = [Commit("a" * 40, "wip", 1)]
    run_pass(gitea, "https://gitea", dry_run=False)
    done = writes(gitea)
    assert done[0] == ("add_label", (6, "needs-fix"))
    assert "aaaaaaa: subject must read" in done[1][1][1]
    assert "drop `keep-commits`" in done[1][1][1]


def test_unknown_scope_at_merge_time_flags_needs_fix(monkeypatch, gitea):
    given(monkeypatch, [pull(6)])
    answer_pull(gitea, 6, title="feat(domian): x (#1)", config='[commits]\nscopes = ["domain", "ui"]\n')
    run_pass(gitea, "https://gitea", dry_run=False)
    gitea.file.assert_called_once_with("mergeweasel.toml", f"{6:040x}")
    done = writes(gitea)
    assert done[0] == ("add_label", (6, "needs-fix"))
    assert "unknown scope 'domian', expected one of: domain, ui" in done[1][1][1]


def test_invalid_config_on_the_branch_flags_needs_fix_and_hands_over(monkeypatch, gitea):
    given(monkeypatch, [pull(6), pull(7, behind=True)])
    answer_pull(gitea, 6, config="[commits]\nmax_line = 0\n")
    gitea.branch_head.return_value = "e" * 40
    action = run_pass(gitea, "https://gitea", dry_run=False)
    done = writes(gitea)
    assert done[0] == ("add_label", (6, "needs-fix"))
    assert "mergeweasel.toml: commits.max_line must be at least 1" in done[1][1][1]
    assert "Fix `mergeweasel.toml` on the branch" in done[1][1][1]
    assert action == queue.Rebase(7, "branch-7", f"{7:040x}", ("chli1",))


def test_an_unknown_key_on_the_branch_does_not_stop_the_merge(monkeypatch, gitea, capsys):
    given(monkeypatch, [pull(6)])
    answer_pull(gitea, 6, config="[comits]\n")
    run_pass(gitea, "https://gitea", dry_run=False)
    assert ("merge", (6, f"{6:040x}", squash(6))) in writes(gitea)
    assert "mergeweasel.toml: unknown key comits ignored" in capsys.readouterr().out


def test_refused_merge_flags_needs_fix_with_giteas_reason_and_hands_over(monkeypatch, gitea):
    given(monkeypatch, [pull(6), pull(7, behind=True)])
    answer_pull(gitea, 6)
    gitea.merge.side_effect = MergeRefused("Does not have enough approvals", 405)
    gitea.branch_head.return_value = "e" * 40
    action = run_pass(gitea, "https://gitea", dry_run=False)
    done = writes(gitea)
    assert done[1] == ("add_label", (6, "needs-fix"))
    assert "Does not have enough approvals" in done[2][1][1]
    assert action == queue.Rebase(7, "branch-7", f"{7:040x}", ("chli1",))


def test_transient_merge_refusal_ends_the_pass_quietly(monkeypatch, gitea, capsys):
    given(monkeypatch, [pull(6), pull(7, behind=True)])
    answer_pull(gitea, 6)
    gitea.merge.side_effect = MergeDeferred("Please try again later", 405)
    action = run_pass(gitea, "https://gitea", dry_run=False)
    assert writes(gitea) == [("merge", (6, f"{6:040x}", squash(6)))]
    assert action == land(6, squash(6))
    assert "#6: merge deferred: Please try again later" in capsys.readouterr().out


def test_renovate_push_clears_needs_fix_and_waits_for_the_rerun_checks(monkeypatch, gitea):
    given(monkeypatch, [pull(9, labels=("automerge", "needs-fix"), renovate_retried=True)])
    action = run_pass(gitea, "https://gitea", dry_run=False)
    assert writes(gitea) == [("remove_label", (9, 14))]
    assert action == queue.Wait(9)


def test_each_pull_loses_its_own_labels_id(monkeypatch, gitea):
    # A repository label and an organization label can share a name, with different ids.
    pulls = [pull(8, labels=("automerge", "needs-fix"), renovate_retried=True),
             pull(9, labels=("automerge", "needs-fix"), renovate_retried=True)]
    label_ids = {8: {"automerge": 13, "needs-fix": 14}, 9: {"automerge": 13, "needs-fix": 100}}
    monkeypatch.setattr(queue, "read_state", lambda gitea, server, merger, settings:
                        queue.State("f" * 40, tuple(REQUIRED), 1, pulls, label_ids))
    run_pass(gitea, "https://gitea", dry_run=False)
    assert writes(gitea) == [("remove_label", (8, 14)), ("remove_label", (9, 100))]


def test_cleared_pull_behind_main_is_rebased(monkeypatch, gitea):
    given(monkeypatch, [pull(9, labels=("automerge", "needs-fix"), behind=True, renovate_retried=True)])
    gitea.branch_head.return_value = "a" * 40
    action = run_pass(gitea, "https://gitea", dry_run=False)
    assert ("rebase", (9,)) in writes(gitea)
    assert isinstance(action, queue.Rebase)


def test_dry_run_writes_nothing(monkeypatch, gitea):
    red = statuses(test="failure", smoke="success")
    given(monkeypatch, [pull(1, checks=red), pull(2, behind=True),
                        pull(9, labels=("automerge", "needs-fix"), renovate_retried=True)])
    action = run_pass(gitea, "https://gitea", dry_run=True)
    assert writes(gitea) == []
    # 1 is flagged (in memory only), 9 had its needs-fix cleared and waits for its re-run checks
    # ahead of 2, which is behind.
    assert action == queue.Wait(9)


def test_dry_run_with_dry_run_approvers_assumes_enough(monkeypatch, gitea):
    given(monkeypatch, [pull(6, approvers=(), carried=("chli1",))])
    answer_pull(gitea, 6)
    action = queue.run_pass(gitea, "https://gitea", True, DryRunMerger(), DryRunApprovers(), SETTINGS)
    assert writes(gitea) == []
    assert action == land(6, squash(6), approvals=(queue.Restored(DRY_RUN_APPROVER, "chli1"),))


def test_dry_run_flags_an_invalid_title_and_hands_over(monkeypatch, gitea, capsys):
    given(monkeypatch, [pull(6), pull(7, behind=True)])
    answer_pull(gitea, 6, title="land six")
    action = run_pass(gitea, "https://gitea", dry_run=True)
    assert writes(gitea) == []
    assert "PR title: subject must read" in capsys.readouterr().out
    assert action == queue.Rebase(7, "branch-7", f"{7:040x}", ("chli1",))


def test_dry_run_flags_an_invalid_config_and_hands_over(monkeypatch, gitea, capsys):
    given(monkeypatch, [pull(6), pull(7, behind=True)])
    answer_pull(gitea, 6, config="[commits]\nmax_line = 0\n")
    action = run_pass(gitea, "https://gitea", dry_run=True)
    assert writes(gitea) == []
    assert "mergeweasel.toml: commits.max_line must be at least 1" in capsys.readouterr().out
    assert action == queue.Rebase(7, "branch-7", f"{7:040x}", ("chli1",))


def test_dry_run_flags_a_bad_kept_commit_and_hands_over(monkeypatch, gitea, capsys):
    given(monkeypatch, [pull(6, labels=("automerge", "keep-commits")), pull(7, behind=True)])
    answer_pull(gitea, 6)
    gitea.pull_commits.return_value = [Commit("a" * 40, "wip", 1)]
    action = run_pass(gitea, "https://gitea", dry_run=True)
    assert writes(gitea) == []
    assert "aaaaaaa: subject must read" in capsys.readouterr().out
    assert action == queue.Rebase(7, "branch-7", f"{7:040x}", ("chli1",))


def test_only_a_merge_is_validated(gitea):
    assert queue.validated(gitea, queue.Wait(3), SETTINGS) == queue.Wait(3)
    assert gitea.mock_calls == []


def test_execute_leaves_message_problems_to_validation(gitea):
    # A Land is validated already: execute reads neither the message nor the config again.
    answer_pull(gitea, 6, title="land six", config="[commits]\nmax_line = 0\n")
    executed = queue.execute(gitea, land(6, squash(6)), Approvers({}), {6: LABEL_IDS}, SETTINGS)
    assert executed == land(6, squash(6))
    gitea.pull.assert_not_called()
    gitea.file.assert_not_called()


def open_pull(number, labels=("automerge",), base="main", poster="author", head_ref="feature"):
    return PullRequest(number, base, head_ref, str(number % 10) * 40,
                       {name: 13 + index for index, name in enumerate(labels)}, False, poster, "t", "", False)


def test_read_state_builds_queued_pulls_from_the_api():
    gitea = mock.create_autospec(Gitea, instance=True)
    gitea.branch.return_value = Branch("f" * 40, tuple(REQUIRED), 2)
    gitea.open_pulls.return_value = [
        open_pull(5), open_pull(6, base="release"), open_pull(7, labels=()),
        open_pull(8, labels=("automerge", "needs-fix"), head_ref="renovate/x"),
        open_pull(9, poster="renovate", head_ref="renovate/y")]
    gitea.statuses.side_effect = {"5" * 40: [status_json()], "8" * 40: [], "9" * 40: []}.__getitem__
    gitea.is_behind.side_effect = lambda head, base: head == "5" * 40 and base == "f" * 40
    gitea.reviews.side_effect = {5: [review(), review(login=QUEUE, official=False, commit_id="5" * 40)],
                                 8: [review(state="REQUEST_CHANGES")], 9: []}.__getitem__
    gitea.timeline.side_effect = {8: [event("label", QUEUE, 10), event("pull_push", "renovate", 11)],
                                  9: [event("label", "renovate", 20, label="automerge")]}.__getitem__
    state = queue.read_state(gitea, "https://gitea", MERGER, SETTINGS)
    gitea.branch.assert_called_once_with("main")
    assert state.base_sha == "f" * 40 and state.required == tuple(REQUIRED) and state.needed == 2
    assert [p.number for p in state.pulls] == [5, 8, 9]
    queued_pull, flagged_pull, renovate_pull = state.pulls
    assert renovate_pull.renovate_trusted and not queued_pull.renovate_trusted and not flagged_pull.renovate_trusted
    assert queued_pull.behind and queued_pull.poster == "author"
    assert queued_pull.approvers == ("chli1",) and queued_pull.carried == ("chli1",)
    assert queued_pull.approved_head == frozenset({QUEUE})
    assert queued_pull.labels == frozenset({"automerge"})
    assert state.label_ids == {5: {"automerge": 13}, 8: {"automerge": 13, "needs-fix": 14}, 9: {"automerge": 13}}
    assert not queued_pull.rejected and not queued_pull.renovate_retried
    assert flagged_pull.rejected and flagged_pull.renovate_retried and not flagged_pull.behind
    assert 5 not in [call.args[0] for call in gitea.timeline.call_args_list]


CUSTOM = Settings("trunk", "merge-me", "broken", "no-squash", "deps-bot")


def test_read_state_uses_the_instances_names():
    gitea = mock.create_autospec(Gitea, instance=True)
    gitea.branch.return_value = Branch("f" * 40, (), 1)
    gitea.open_pulls.return_value = [open_pull(5, labels=("merge-me",), base="trunk", poster="deps-bot"),
                                     open_pull(6, labels=("automerge",), base="trunk", poster="deps-bot"),
                                     open_pull(7, labels=("merge-me",), poster="deps-bot")]
    gitea.statuses.return_value = []
    gitea.is_behind.return_value = False
    gitea.reviews.return_value = []
    gitea.timeline.return_value = [event("label", "deps-bot", 20, label="merge-me")]
    state = queue.read_state(gitea, "https://gitea", MERGER, CUSTOM)
    gitea.branch.assert_called_once_with("trunk")
    assert [p.number for p in state.pulls] == [5]
    assert state.pulls[0].renovate_trusted


def test_without_a_renovate_account_renovate_is_an_ordinary_poster():
    off = Settings("main")
    timeline = [event("label", "renovate", 10, label="automerge"), event("label", QUEUE, 11),
                event("pull_push", "renovate", 12)]
    assert queue.renovate_trusted("renovate", timeline, MERGER, SETTINGS)
    assert queue.renovate_pushed_since_needs_fix(timeline, SETTINGS)
    assert not queue.renovate_trusted("renovate", timeline, MERGER, off)
    assert not queue.renovate_pushed_since_needs_fix(timeline, off)


def test_decide_reads_the_instances_labels():
    pulls = [pull(1, labels=("automerge",)), pull(2, labels=("merge-me", "broken")), pull(3, labels=("merge-me", "no-squash"))]
    action = queue.decide(pulls, REQUIRED, 1, approvers_of(APPROVER), CUSTOM)
    assert action == queue.Merge(3, f"{3:040x}", approvals=(), keep_commits=True)


def test_comments_name_the_instances_labels_and_branch():
    assert "`trunk`" in queue.conflict_comment(CUSTOM) and "remove `broken`" in queue.conflict_comment(CUSTOM)
    invalid = queue.invalid_message_comment(["bad"], True, CUSTOM)
    assert "land on `trunk`" in invalid and "drop `no-squash`" in invalid
    assert queue.text(queue.RENOVATE_APPROVAL, CUSTOM).startswith("Approves deps-bot's update: posted, pushed and labelled `merge-me`")


def test_every_needs_fix_comment_names_mergeweasel():
    red = pull(1, checks=statuses(test="failure", smoke="success"))
    comments = [queue.failure_comment(red, REQUIRED, SETTINGS), queue.conflict_comment(SETTINGS),
                queue.refused_comment("no", SETTINGS), queue.invalid_message_comment(["bad"], False, SETTINGS),
                queue.shortfall_comment(SETTINGS)]
    assert all(comment.startswith("mergeweasel: ") for comment in comments)


# --- trusted Renovate ---

def test_renovate_trust_needs_renovates_pushes_and_label():
    timeline = [event("label", "renovate", 10, label="automerge"), event("pull_push", "renovate", 11),
                event("pull_push", QUEUE, 12)]
    assert queue.renovate_trusted("renovate", timeline, MERGER, SETTINGS)


def test_renovate_trust_lost_by_a_foreign_push():
    timeline = [event("label", "renovate", 10, label="automerge"), event("pull_push", "author", 11)]
    assert not queue.renovate_trusted("renovate", timeline, MERGER, SETTINGS)


def test_renovate_trust_lost_when_a_human_labelled_last():
    timeline = [event("label", "renovate", 10, label="automerge"), event("label", "renovate", 11, label="automerge", body="0"),
                event("label", "chli1", 12, label="automerge")]
    assert not queue.renovate_trusted("renovate", timeline, MERGER, SETTINGS)


def test_renovate_trust_needs_renovate_as_poster():
    assert not queue.renovate_trusted("author", [event("label", "renovate", 10, label="automerge")], MERGER, SETTINGS)


def test_renovate_trust_under_the_dry_run_merger_accepts_other_pushes():
    # A dry run without the merger token: like the carry-over, assumed genuine.
    timeline = [event("label", "renovate", 10, label="automerge"), event("pull_push", "someone", 11)]
    assert queue.renovate_trusted("renovate", timeline, DryRunMerger(), SETTINGS)


def test_trusted_renovate_pull_is_queued_and_approved_by_an_approver():
    trusted = pull(6, approvers=(), poster="renovate", renovate_trusted=True)
    assert decide([trusted], REQUIRED) == merge_action(6, approvals=(queue.RenovateApproval(APPROVER),))


def test_a_renovate_pull_with_a_carry_over_restores_it_then_approves_for_renovate():
    trusted = pull(6, approvers=(), carried=("chli1",), poster="renovate", renovate_trusted=True)
    action = decide([trusted], REQUIRED, needed=2, approvers=approvers_of("a1", "a2"))
    assert action.approvals == (queue.Restored("a1", "chli1"), queue.RenovateApproval("a2"))


def test_each_approval_kind_has_its_own_body():
    assert queue.Restored("a1", "alice").body(SETTINGS) == "Approval of alice, carried over the merge queue's rebase."
    assert queue.RenovateApproval("a1").body(SETTINGS) == queue.text(queue.RENOVATE_APPROVAL, SETTINGS)


def test_untrusted_renovate_pull_waits_for_a_human():
    assert decide([pull(6, approvers=(), poster="renovate")], REQUIRED) == queue.Idle()


def test_trusted_renovate_pull_in_a_dry_run_with_dry_run_approvers():
    trusted = pull(6, approvers=(), poster="renovate", renovate_trusted=True)
    action = decide([trusted], REQUIRED, needed=2, approvers=DryRunApprovers())
    assert action == merge_action(6, approvals=(queue.RenovateApproval(DRY_RUN_APPROVER),) * 2)


def test_trusted_renovate_pull_with_too_few_approvers_flags_needs_fix():
    trusted = pull(6, approvers=(), poster="renovate", renovate_trusted=True)
    assert isinstance(decide([trusted], REQUIRED, needed=2), queue.FlagNeedsFix)


def test_merge_approves_a_trusted_renovate_pull_with_its_reason(monkeypatch, gitea):
    given(monkeypatch, [pull(6, approvers=(), poster="renovate", renovate_trusted=True)])
    log = []
    answer_pull(gitea, 6)
    run_pass(gitea, "https://gitea", dry_run=False, approvers=approver_clients(log, "a1"))
    assert log == [("a1", 6, f"{6:040x}", queue.text(queue.RENOVATE_APPROVAL, SETTINGS))]


def test_trusted_renovate_pull_without_approver_accounts_waits_for_a_human():
    # Approver accounts are optional: without any, Renovate's update is approved by hand, not flagged.
    trusted = pull(6, approvers=(), poster="renovate", renovate_trusted=True)
    assert decide([trusted], REQUIRED, approvers=approvers_of()) == queue.Idle()
