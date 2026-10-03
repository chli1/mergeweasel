import dataclasses
import io
import json
import urllib.error
from unittest import mock

import pytest

from mergeweasel import queue
from mergeweasel.gitea import Gitea

QUEUE = "weasel-bot"

TEST = "build-and-push / test (pull_request)"
SMOKE = "build-and-push / image-smoke (pull_request)"
REQUIRED = [TEST, SMOKE]


def statuses(**states):
    """statuses(test="success", smoke="failure") - keyword per required context."""
    contexts = {"test": TEST, "smoke": SMOKE}
    return {contexts[key]: queue.Status(contexts[key], state, f"https://ci/{key}")
            for key, state in states.items()}


def green():
    return statuses(test="success", smoke="success")


def pull(number, *, labels=("automerge",), behind=False, approver="chli1", carried=None,
         checks=None, draft=False, rejected=False, queue_approved=False, renovate_retried=False):
    return queue.Pull(
        number=number, head_ref=f"branch-{number}", head_sha=f"{number:040x}",
        labels={name: index for index, name in enumerate(labels)}, draft=draft, behind=behind,
        approver=approver, carried_reviewer=carried, rejected=rejected, queue_approved=queue_approved,
        renovate_retried=renovate_retried, statuses=green() if checks is None else checks)


def decide(pulls, required, account=QUEUE):
    return queue.decide(pulls, required, account)


def run_pass(gitea, server, dry_run, account=QUEUE):
    return queue.run_pass(gitea, server, dry_run, account)


# --- decide: selection ---

def test_idle_without_queued_pulls():
    assert decide([], REQUIRED) == queue.Idle()


def test_lowest_number_first_when_all_are_behind():
    action = decide([pull(5, behind=True), pull(3, behind=True)], REQUIRED)
    assert action == queue.Rebase(3, "branch-3", f"{3:040x}", "chli1")


def test_up_to_date_pull_beats_a_lower_number():
    pending = statuses(test="pending", smoke="pending")
    action = decide([pull(2, behind=True), pull(7, checks=pending)], REQUIRED)
    assert action == queue.Wait(7)


def test_unapproved_pull_is_not_queued():
    assert decide([pull(1, approver=None)], REQUIRED) == queue.Idle()


def test_needs_fix_draft_and_unlabelled_pulls_are_not_queued():
    pulls = [pull(1, labels=("automerge", "needs-fix")), pull(2, draft=True), pull(3, labels=())]
    assert decide(pulls, REQUIRED) == queue.Idle()


def test_pull_with_a_standing_request_for_changes_is_not_queued():
    assert decide([pull(1, carried="chli1", rejected=True)], REQUIRED) == queue.Idle()


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
    action = decide([pull(6)], REQUIRED)
    assert action == queue.Merge(6, f"{6:040x}", approve_first=False, reviewer="chli1", label_id=0, keep_commits=False)


def test_green_pull_with_only_a_carry_over_is_approved_first():
    action = decide([pull(6, approver=None, carried="chli1")], REQUIRED)
    assert action == queue.Merge(6, f"{6:040x}", approve_first=True, reviewer="chli1", label_id=0, keep_commits=False)


def test_queue_approval_on_the_head_is_not_repeated():
    action = decide([pull(6, approver=None, carried="chli1", queue_approved=True)], REQUIRED)
    assert action == queue.Merge(6, f"{6:040x}", approve_first=False, reviewer="chli1", label_id=0, keep_commits=False)


def test_rebase_names_the_human_reviewer_over_the_queue():
    action = decide(
        [pull(8, behind=True, approver=QUEUE, carried="chli1")], REQUIRED)
    assert action.reviewer == "chli1"


# --- parsers ---

def review(state="APPROVED", login="chli1", official=True, dismissed=False, stale=False, commit_id="0" * 40):
    return {"state": state, "user": {"login": login}, "official": official,
            "dismissed": dismissed, "stale": stale, "commit_id": commit_id}


def test_approver_takes_the_latest_valid_official_approval():
    reviews = [review(login="chli1"), review(login="approver-bot")]
    assert queue.approver(reviews) == "approver-bot"


def test_approver_ignores_dismissed_stale_unofficial_and_other_reviews():
    reviews = [review(dismissed=True), review(stale=True), review(official=False),
               review(state="COMMENT"), review(state="REQUEST_CHANGES")]
    assert queue.approver(reviews) is None


def test_request_for_changes_counts_per_reviewers_latest_verdict():
    assert queue.rejected([review(), review(state="REQUEST_CHANGES")])
    assert not queue.rejected([review(state="REQUEST_CHANGES"), review()])
    assert not queue.rejected([review(state="REQUEST_CHANGES"), review(dismissed=True)])
    assert not queue.rejected([review(state="REQUEST_CHANGES", dismissed=True)])
    assert not queue.rejected([review(state="REQUEST_CHANGES", official=False)])
    assert queue.rejected([review(state="REQUEST_CHANGES"), review(state="COMMENT")])


def test_queue_approval_counts_only_on_the_head_and_undismissed():
    head = "a" * 40
    assert queue.queue_approved([review(login=QUEUE, official=False, commit_id=head)], head, QUEUE)
    assert not queue.queue_approved([review(login=QUEUE, commit_id="b" * 40)], head, QUEUE)
    assert not queue.queue_approved([review(login=QUEUE, dismissed=True, commit_id=head)], head, QUEUE)
    assert not queue.queue_approved([review(login="chli1", commit_id=head)], head, QUEUE)


def status_json(context="mergeweasel/approval", state="success", creator=QUEUE,
                description="approved by chli1 at 1234567"):
    return {"context": context, "status": state, "creator": {"login": creator},
            "description": description, "target_url": "/org/repo/actions/runs/1/jobs/2"}


def test_carry_over_names_its_reviewer():
    assert queue.carried_reviewer([status_json()], QUEUE) == "chli1"


def test_carry_over_counts_only_from_the_queue_account():
    assert queue.carried_reviewer([status_json(creator="stranger")], QUEUE) is None
    assert queue.carried_reviewer([status_json(state="pending")], QUEUE) is None
    assert queue.carried_reviewer([status_json(context="other")], QUEUE) is None
    assert queue.carried_reviewer([status_json(description="tampered")], QUEUE) is None


def test_carry_over_description_round_trips():
    description = queue.carry_over_description("chli1", "abcdef0123456789")
    assert queue.carried_reviewer([status_json(description=description)], QUEUE) == "chli1"


def test_without_an_account_any_carry_over_counts():
    assert queue.carried_reviewer([status_json(creator="someone-else")], None) == "chli1"


def test_without_an_account_no_approval_is_the_queues():
    head = "a" * 40
    assert not queue.queue_approved([review(login=QUEUE, commit_id=head)], head, None)


def test_without_an_account_every_approver_counts_as_human():
    action = decide([pull(1, approver=QUEUE)], REQUIRED, account=None)
    assert action.reviewer == QUEUE


def test_the_queues_own_statuses_never_count():
    own = [status_json(), status_json(context="mergeweasel-queue / pass (pull_request_target)", state="failure")]
    assert queue.statuses_by_context(own, "https://gitea.example") == {}


def test_statuses_by_context_makes_links_absolute():
    by_context = queue.statuses_by_context([status_json(context=TEST)], "https://gitea.example")
    assert by_context[TEST] == queue.Status(
        TEST, "success", "https://gitea.example/org/repo/actions/runs/1/jobs/2")


# --- step 0 ---

def event(kind, login, event_id, label="needs-fix", body="1"):
    entry = {"id": event_id, "type": kind, "user": {"login": login}, "body": body}
    if kind == "label":
        entry["label"] = {"name": label}
    return entry


def test_renovate_push_after_the_flag_retries():
    assert queue.renovate_pushed_since_needs_fix([event("label", QUEUE, 10), event("pull_push", "renovate", 12)])


def test_older_or_foreign_pushes_retry_nothing():
    assert not queue.renovate_pushed_since_needs_fix([event("pull_push", "renovate", 8), event("label", QUEUE, 10)])
    assert not queue.renovate_pushed_since_needs_fix([event("label", QUEUE, 10), event("pull_push", "stranger", 12)])
    assert not queue.renovate_pushed_since_needs_fix([event("label", QUEUE, 10), event("label", "chli1", 11, label="automerge"),
                                                            event("pull_push", QUEUE, 12)])
    assert not queue.renovate_pushed_since_needs_fix([event("pull_push", "renovate", 12)])


def test_needs_fix_to_clear_takes_the_retried_flagged_pulls():
    retried = pull(9, labels=("automerge", "needs-fix"), renovate_retried=True)
    assert queue.needs_fix_to_clear([retried, pull(8, labels=("automerge", "needs-fix")), pull(7)]) == [retried]


def http_error(code, message):
    return urllib.error.HTTPError("url", code, message, hdrs=None,
                                  fp=io.BytesIO(json.dumps({"message": message}).encode()))


def conflict():
    return http_error(409, "rebase failed because of conflict")


class FakeResponses:
    """Routes Gitea.call(method, path, body) to canned answers; records every call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        answer = self.answers.get((method, path))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def writes(self):
        return [(method, path, body) for method, path, body in self.calls if method != "GET"]


def pull_answer(number, title=None, body=""):
    """GET /pulls/{n} as the queue reads it right before merging."""
    return {("GET", f"/pulls/{number}"): {"title": title or f"ci: land {number} (#1)", "body": body}}


def pages(issues=(1,), commits=None):
    """Gitea.paged: the issue list, and the pull request's commits for keep-commits."""
    def answer(path):
        if path == "/issues?state=all&type=issues":
            return [{"number": number} for number in issues]
        if commits is not None and "/commits?" in path:
            return commits
        raise AssertionError(f"unexpected paged read {path}")
    return answer


def squash(number, body=""):
    return {"Do": "squash", "MergeTitleField": f"ci: land {number} (#1)", "MergeMessageField": body,
            "head_commit_id": f"{number:040x}"}


@pytest.fixture
def gitea():
    return mock.Mock(spec=Gitea)


def given(monkeypatch, pulls):
    monkeypatch.setattr(queue, "read_state",
                        lambda gitea, server, account: queue.State("f" * 40, REQUIRED, pulls))


def test_rebase_sets_the_carry_over_on_the_new_head(monkeypatch, gitea):
    given(monkeypatch, [pull(3, behind=True)])
    new_head = "a" * 40
    gitea.call.side_effect = FakeResponses({
        ("GET", "/git/refs/heads/branch-3"): [{"ref": "refs/heads/branch-3", "object": {"sha": new_head}}],
    })
    run_pass(gitea, "https://gitea", dry_run=False)
    assert gitea.call.side_effect.writes() == [
        ("POST", "/pulls/3/update?style=rebase", None),
        ("POST", f"/statuses/{new_head}", {"state": "success", "context": "mergeweasel/approval",
                                           "description": "approved by chli1 at " + f"{3:040x}"[:7]}),
    ]


def test_rebase_finds_the_exact_ref_among_prefix_matches(monkeypatch, gitea):
    renovate_pull = dataclasses.replace(pull(4, behind=True, approver="approver-bot"), head_ref="renovate/gradle")
    given(monkeypatch, [renovate_pull])
    gitea.call.side_effect = FakeResponses({
        ("GET", "/git/refs/heads/renovate/gradle"): [
            {"ref": "refs/heads/renovate/gradle-8.x", "object": {"sha": "b" * 40}},
            {"ref": "refs/heads/renovate/gradle", "object": {"sha": "c" * 40}},
        ],
    })
    run_pass(gitea, "https://gitea", dry_run=False)
    assert gitea.call.side_effect.writes()[-1][1] == f"/statuses/{'c' * 40}"


def test_conflict_flags_needs_fix_and_hands_over(monkeypatch, gitea):
    given(monkeypatch, [pull(1, behind=True), pull(2, behind=True)])
    gitea.call.side_effect = FakeResponses({
        ("POST", "/pulls/1/update?style=rebase"): conflict(),
        ("GET", "/git/refs/heads/branch-2"): [{"ref": "refs/heads/branch-2", "object": {"sha": "d" * 40}}],
    })
    action = run_pass(gitea, "https://gitea", dry_run=False)
    writes = gitea.call.side_effect.writes()
    assert writes[1] == ("POST", "/issues/1/labels", {"labels": ["needs-fix"]})
    assert writes[2][1] == "/issues/1/comments" and "conflict" in writes[2][2]["body"]
    assert writes[3] == ("POST", "/pulls/2/update?style=rebase", None)
    assert action == queue.Rebase(2, "branch-2", f"{2:040x}", "chli1")


def test_every_candidate_failing_ends_the_pass(monkeypatch, gitea):
    red = statuses(test="failure", smoke="success")
    given(monkeypatch, [pull(1, checks=red), pull(2, behind=True)])
    gitea.call.side_effect = FakeResponses({("POST", "/pulls/2/update?style=rebase"): conflict()})
    action = run_pass(gitea, "https://gitea", dry_run=False)
    flagged = [path for method, path, body in gitea.call.side_effect.writes() if path.endswith("/labels")]
    assert flagged == ["/issues/1/labels", "/issues/2/labels"]
    assert action == queue.Idle()


def test_carry_over_survives_a_transient_failure(monkeypatch, gitea):
    monkeypatch.setattr(queue.time, "sleep", lambda seconds: None)
    given(monkeypatch, [pull(3, behind=True)])
    refs = [{"ref": "refs/heads/branch-3", "object": {"sha": "a" * 40}}]
    answers = iter([urllib.error.URLError("timed out"), refs])
    responses = FakeResponses({})

    def flaky(method, path, body=None):
        if (method, path) == ("GET", "/git/refs/heads/branch-3"):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return responses(method, path, body)

    gitea.call.side_effect = flaky
    run_pass(gitea, "https://gitea", dry_run=False)
    assert responses.writes()[-1][1] == f"/statuses/{'a' * 40}"


def test_unexpected_api_error_fails_the_pass(monkeypatch, gitea):
    given(monkeypatch, [pull(1, behind=True)])
    gitea.call.side_effect = FakeResponses({
        ("POST", "/pulls/1/update?style=rebase"): urllib.error.HTTPError("url", 500, "boom", hdrs=None, fp=None)})
    with pytest.raises(urllib.error.HTTPError):
        run_pass(gitea, "https://gitea", dry_run=False)


def test_merge_approves_first_then_squashes_with_the_head_guard(monkeypatch, gitea):
    given(monkeypatch, [pull(6, approver=None, carried="chli1")])
    gitea.call.side_effect = FakeResponses(pull_answer(6))
    gitea.paged.side_effect = pages()
    run_pass(gitea, "https://gitea", dry_run=False)
    writes = gitea.call.side_effect.writes()
    assert writes[0][1] == "/pulls/6/reviews"
    assert writes[0][2]["event"] == "APPROVED" and writes[0][2]["commit_id"] == f"{6:040x}"
    assert writes[1] == ("POST", "/pulls/6/merge", squash(6))


def test_merge_removes_the_automerge_label(monkeypatch, gitea):
    given(monkeypatch, [dataclasses.replace(pull(6), labels={"automerge": 13})])
    gitea.call.side_effect = FakeResponses(pull_answer(6))
    gitea.paged.side_effect = pages()
    run_pass(gitea, "https://gitea", dry_run=False)
    writes = gitea.call.side_effect.writes()
    assert writes == [("POST", "/pulls/6/merge", squash(6)),
                      ("DELETE", "/issues/6/labels/13", None)]


def test_keep_commits_label_reaches_the_merge_action():
    action = decide([pull(6, labels=("automerge", "keep-commits"))], REQUIRED)
    assert action == queue.Merge(6, f"{6:040x}", approve_first=False, reviewer="chli1", label_id=0,
                                       keep_commits=True)


def test_squash_body_is_the_commit_section(monkeypatch, gitea):
    given(monkeypatch, [pull(6)])
    gitea.call.side_effect = FakeResponses(pull_answer(6, body="Closes #1\n\n## Commit\nWhy it is so.\n"))
    gitea.paged.side_effect = pages()
    run_pass(gitea, "https://gitea", dry_run=False)
    assert gitea.call.side_effect.writes()[0] == ("POST", "/pulls/6/merge", squash(6, body="Why it is so."))


def test_keep_commits_fast_forwards_after_checking_every_commit(monkeypatch, gitea):
    given(monkeypatch, [pull(6, labels=("automerge", "keep-commits"))])
    gitea.call.side_effect = FakeResponses(pull_answer(6, title="no format needed"))
    gitea.paged.side_effect = pages(commits=[
        {"sha": "a" * 40, "commit": {"message": "ci: move (#1)\n\nbody"}, "parents": [{"sha": "f" * 40}]},
        {"sha": "b" * 40, "commit": {"message": "ci: edit (#1)"}, "parents": [{"sha": "a" * 40}]},
    ])
    run_pass(gitea, "https://gitea", dry_run=False)
    assert gitea.call.side_effect.writes()[0] == (
        "POST", "/pulls/6/merge", {"Do": "fast-forward-only", "head_commit_id": f"{6:040x}"})


def test_invalid_title_read_at_merge_time_flags_needs_fix_and_hands_over(monkeypatch, gitea):
    # The CI check passed earlier; the title was edited afterwards. Only the queue's own read counts.
    given(monkeypatch, [pull(6, approver=None, carried="chli1"), pull(7, behind=True)])
    gitea.call.side_effect = FakeResponses({
        **pull_answer(6, title="land six"),
        ("GET", "/git/refs/heads/branch-7"): [{"ref": "refs/heads/branch-7", "object": {"sha": "e" * 40}}],
    })
    gitea.paged.side_effect = pages()
    action = run_pass(gitea, "https://gitea", dry_run=False)
    writes = gitea.call.side_effect.writes()
    # Neither approved nor merged: the flag comes first.
    assert writes[0] == ("POST", "/issues/6/labels", {"labels": ["needs-fix"]})
    assert "PR title: subject must read" in writes[1][2]["body"]
    assert "`## Commit` section" in writes[1][2]["body"]
    assert action == queue.Rebase(7, "branch-7", f"{7:040x}", "chli1")


def test_keep_commits_with_a_bad_commit_flags_needs_fix(monkeypatch, gitea):
    given(monkeypatch, [pull(6, labels=("automerge", "keep-commits"))])
    gitea.call.side_effect = FakeResponses(pull_answer(6))
    gitea.paged.side_effect = pages(commits=[
        {"sha": "a" * 40, "commit": {"message": "wip"}, "parents": [{"sha": "f" * 40}]}])
    run_pass(gitea, "https://gitea", dry_run=False)
    writes = gitea.call.side_effect.writes()
    assert writes[0] == ("POST", "/issues/6/labels", {"labels": ["needs-fix"]})
    assert "aaaaaaa: subject must read" in writes[1][2]["body"]
    assert "drop `keep-commits`" in writes[1][2]["body"]


def test_refused_merge_flags_needs_fix_with_giteas_reason_and_hands_over(monkeypatch, gitea):
    given(monkeypatch, [pull(6), pull(7, behind=True)])
    gitea.call.side_effect = FakeResponses({
        **pull_answer(6),
        ("POST", "/pulls/6/merge"): http_error(405, "Does not have enough approvals"),
        ("GET", "/git/refs/heads/branch-7"): [{"ref": "refs/heads/branch-7", "object": {"sha": "e" * 40}}],
    })
    gitea.paged.side_effect = pages()
    action = run_pass(gitea, "https://gitea", dry_run=False)
    writes = gitea.call.side_effect.writes()
    assert writes[1] == ("POST", "/issues/6/labels", {"labels": ["needs-fix"]})
    assert "Does not have enough approvals" in writes[2][2]["body"]
    assert action == queue.Rebase(7, "branch-7", f"{7:040x}", "chli1")


@pytest.mark.parametrize("refusal", [http_error(405, "Please try again later"), http_error(409, "head out of date")])
def test_transient_merge_refusal_ends_the_pass_quietly(monkeypatch, gitea, refusal):
    given(monkeypatch, [pull(6), pull(7, behind=True)])
    gitea.call.side_effect = FakeResponses({**pull_answer(6), ("POST", "/pulls/6/merge"): refusal})
    gitea.paged.side_effect = pages()
    action = run_pass(gitea, "https://gitea", dry_run=False)
    assert gitea.call.side_effect.writes() == [("POST", "/pulls/6/merge", squash(6))]
    assert action == queue.Merge(6, f"{6:040x}", approve_first=False, reviewer="chli1", label_id=0,
                                       keep_commits=False)


def test_renovate_push_clears_needs_fix_then_runs_the_pass(monkeypatch, gitea):
    given(monkeypatch, [pull(9, labels=("automerge", "needs-fix"), renovate_retried=True)])
    gitea.call.side_effect = FakeResponses(pull_answer(9))
    gitea.paged.side_effect = pages()
    action = run_pass(gitea, "https://gitea", dry_run=False)
    writes = gitea.call.side_effect.writes()
    assert writes[0] == ("DELETE", "/issues/9/labels/1", None)
    assert action == queue.Merge(9, f"{9:040x}", approve_first=False, reviewer="chli1", label_id=0,
                                       keep_commits=False)


def test_dry_run_writes_nothing(monkeypatch, gitea):
    red = statuses(test="failure", smoke="success")
    given(monkeypatch, [pull(1, checks=red), pull(2, behind=True),
                        pull(9, labels=("automerge", "needs-fix"), renovate_retried=True)])
    gitea.call.side_effect = FakeResponses({})
    action = run_pass(gitea, "https://gitea", dry_run=True)
    assert gitea.call.side_effect.writes() == []
    # 1 is flagged (in memory only), 9 had its needs-fix cleared and is green: it merges before
    # 2, which is behind.
    assert action == queue.Merge(9, f"{9:040x}", approve_first=False, reviewer="chli1", label_id=0, keep_commits=False)


def test_read_state_builds_queued_pulls_from_the_api():
    raw_pull = {"number": 5, "labels": [{"name": "automerge", "id": 13}], "base": {"ref": "main"},
                "head": {"ref": "feature", "sha": "5" * 40}, "draft": False}
    other_base = {**raw_pull, "number": 6, "base": {"ref": "release"}}
    unlabelled = {**raw_pull, "number": 7, "labels": []}
    flagged = {**raw_pull, "number": 8, "labels": [{"name": "automerge", "id": 13}, {"name": "needs-fix", "id": 14}],
               "head": {"ref": "renovate/x", "sha": "8" * 40}}
    gitea = mock.Mock(spec=Gitea)
    gitea.call.side_effect = FakeResponses({
        ("GET", "/branches/main"): {"commit": {"id": "f" * 40}, "status_check_contexts": REQUIRED,
                                    "enable_status_check": True},
        ("GET", f"/commits/{'5' * 40}/status?limit=50"): {"statuses": [status_json()]},
        ("GET", f"/compare/{'5' * 40}...{'f' * 40}"): {"total_commits": 2},
        ("GET", f"/commits/{'8' * 40}/status?limit=50"): {"statuses": []},
        ("GET", f"/compare/{'8' * 40}...{'f' * 40}"): {"total_commits": 0},
    })
    gitea.paged.side_effect = lambda path: {
        "/pulls?state=open": [raw_pull, other_base, unlabelled, flagged],
        "/pulls/5/reviews": [review(), review(login=QUEUE, official=False, commit_id="5" * 40)],
        "/pulls/8/reviews": [review(state="REQUEST_CHANGES")],
        "/issues/8/timeline": [event("label", QUEUE, 10), event("pull_push", "renovate", 11)],
    }[path]
    state = queue.read_state(gitea, "https://gitea", QUEUE)
    assert state.main_sha == "f" * 40 and state.required == REQUIRED
    assert [p.number for p in state.pulls] == [5, 8]
    queued_pull, flagged_pull = state.pulls
    assert queued_pull.behind and queued_pull.approver == "chli1" and queued_pull.carried_reviewer == "chli1"
    assert queued_pull.labels == {"automerge": 13}
    assert queued_pull.queue_approved and not queued_pull.rejected and not queued_pull.renovate_retried
    assert flagged_pull.rejected and flagged_pull.renovate_retried and not flagged_pull.behind
    assert "/issues/5/timeline" not in [call.args[0] for call in gitea.paged.call_args_list]


# --- turn ---

def run(run_id, status="in_progress", event="workflow_run"):
    return {"id": run_id, "status": status, "event": event}


def test_alone_its_the_passes_turn():
    assert queue.turn([run(7), run(6, "completed")], 7, "workflow_run") == queue.OwnTurn()


def test_an_unfinished_older_pass_goes_first():
    runs = [run(7), run(6, "queued"), run(5, "waiting")]
    assert queue.turn(runs, 7, "push") == queue.AfterOlder(6)


def test_a_newer_events_pass_supersedes():
    runs = [run(9, "queued"), run(8), run(7), run(6)]
    assert queue.turn(runs, 7, "pull_request_target") == queue.Superseded(9)


def test_a_finished_newer_pass_supersedes_nothing():
    assert queue.turn([run(8, "completed"), run(7)], 7, "workflow_run") == queue.OwnTurn()


def test_a_dispatch_neither_supersedes_nor_is_superseded():
    assert queue.turn([run(8, event="workflow_dispatch"), run(7)], 7, "workflow_run") == queue.OwnTurn()
    runs = [run(9), run(8, event="workflow_dispatch"), run(7)]
    assert queue.turn(runs, 8, "workflow_dispatch") == queue.AfterOlder(7)


def runs_answers(*pages_of_runs):
    """Successive answers to the queue's run list."""
    return [{"workflow_runs": runs} for runs in pages_of_runs]


def test_wait_for_turn_polls_until_the_older_pass_finished(monkeypatch, gitea):
    sleeps = []
    monkeypatch.setattr(queue.time, "sleep", sleeps.append)
    gitea.call.side_effect = runs_answers([run(7), run(6)], [run(7), run(6, "completed")])
    assert queue.wait_for_turn(gitea, 7, "workflow_run")
    assert sleeps == [queue.TURN_POLL_SECONDS]
    assert gitea.call.call_args.args == ("GET", "/actions/workflows/mergeweasel-queue.yml/runs?limit=50")


def test_wait_for_turn_leaves_to_a_newer_pass_while_waiting(monkeypatch, gitea):
    monkeypatch.setattr(queue.time, "sleep", lambda seconds: None)
    gitea.call.side_effect = runs_answers([run(7), run(6)], [run(8, "queued"), run(7), run(6)])
    assert not queue.wait_for_turn(gitea, 7, "workflow_run")


def test_every_needs_fix_comment_names_mergeweasel():
    red = pull(1, checks=statuses(test="failure", smoke="success"))
    comments = [queue.failure_comment(red, REQUIRED), queue.conflict_comment(),
                queue.refused_comment("no"), queue.invalid_message_comment(["bad"], False)]
    assert all(comment.startswith("mergeweasel: ") for comment in comments)
