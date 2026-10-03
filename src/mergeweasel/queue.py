"""One queue pass: land the next `automerge` pull request, or move it one step closer.

Each pass reads the current state, takes at most one step on one candidate and ends; a candidate
flagged `needs-fix` hands over to the next one within the same pass. How and why: docs/design.md.
"""
import dataclasses
import fnmatch
import re
import time
import urllib.error
import urllib.parse

from mergeweasel import commit_rule
from mergeweasel.gitea import PAGE_SIZE, error_message, issue_numbers, pull_commits, retried

# The workflow's name: Gitea prefixes its job statuses with it.
QUEUE_WORKFLOW = "mergeweasel-queue"
QUEUE_WORKFLOW_FILE = "mergeweasel-queue.yml"
# A manual dispatch can be a dry run, which must neither replace an event's pass nor be replaced.
DISPATCH_EVENT = "workflow_dispatch"
TURN_POLL_SECONDS = 5
RENOVATE_ACCOUNT = "renovate"
AUTOMERGE = "automerge"
NEEDS_FIX = "needs-fix"
KEEP_COMMITS = "keep-commits"
CARRY_OVER_CONTEXT = "mergeweasel/approval"
BASE_BRANCH = "main"

FAILED = ("error", "failure", "warning")
PASSED = ("success", "skipped")
CARRY_OVER_PATTERN = re.compile(r"^approved by (\S+) at [0-9a-f]+$")
RED_HINT = "Re-run the failed jobs or push a fix, then remove `needs-fix` to queue it again."
CONFLICT_HINT = f"Rebase onto `{BASE_BRANCH}` and resolve the conflict, then remove `needs-fix` to queue it again."
REFUSED_HINT = "Fix the cause, then remove `needs-fix` to queue it again."
MESSAGE_HINT = "Fix the PR title or its `## Commit` section, then remove `needs-fix` to queue it again."
COMMITS_HINT = "Reword the commits, or drop `keep-commits` to squash them, then remove `needs-fix` to queue it again."
# Gitea's 405 while its mergeable check still runs; the next pass retries.
TRANSIENT_MERGE_REFUSAL = "Please try again later"


@dataclasses.dataclass(frozen=True)
class Status:
    context: str
    state: str
    target_url: str


@dataclasses.dataclass(frozen=True)
class Pull:
    number: int
    head_ref: str
    head_sha: str
    labels: dict
    draft: bool
    # True if main holds commits the head lacks.
    behind: bool
    # Reviewer of the latest valid official approval, None if there is none.
    approver: str | None
    # Reviewer named by the queue's carry-over status on the head, None if there is none.
    carried_reviewer: str | None
    # True if an official reviewer's latest verdict is an undismissed request for changes.
    rejected: bool
    # True if the queue already approved the current head.
    queue_approved: bool
    # True if renovate pushed after needs-fix was last set; always False without needs-fix.
    renovate_retried: bool
    statuses: dict


@dataclasses.dataclass(frozen=True)
class Idle:
    pass


@dataclasses.dataclass(frozen=True)
class Wait:
    number: int


@dataclasses.dataclass(frozen=True)
class Rebase:
    number: int
    head_ref: str
    head_sha: str
    reviewer: str


@dataclasses.dataclass(frozen=True)
class FlagNeedsFix:
    number: int
    comment: str


@dataclasses.dataclass(frozen=True)
class Merge:
    number: int
    head_sha: str
    approve_first: bool
    reviewer: str
    # The automerge label's id, removed once the merge has landed.
    label_id: int
    # Fast-forward the commits as they are instead of squashing them.
    keep_commits: bool


@dataclasses.dataclass(frozen=True)
class OwnTurn:
    pass


@dataclasses.dataclass(frozen=True)
class AfterOlder:
    run_id: int


@dataclasses.dataclass(frozen=True)
class Superseded:
    run_id: int


def turn(runs, own_id, own_event):
    """Serialize the passes without a concurrency group, whose replaced runs Gitea reports as failed.

    A newer pass reads a newer state, so an event's pass leaves to a newer event's pass; otherwise
    it waits until every older pass has finished.
    """
    unfinished = [run for run in runs if run["status"] != "completed" and run["id"] != own_id]
    newer = [run["id"] for run in unfinished if run["id"] > own_id and run["event"] != DISPATCH_EVENT]
    if newer and own_event != DISPATCH_EVENT:
        return Superseded(max(newer))
    older = [run["id"] for run in unfinished if run["id"] < own_id]
    if older:
        return AfterOlder(max(older))
    return OwnTurn()


def wait_for_turn(gitea, own_id, own_event):
    """True once this pass may run, False if a newer pass takes over."""
    while True:
        # One page: the newest runs come first, and an unfinished one is never 50 runs old.
        runs = gitea.call("GET", f"/actions/workflows/{QUEUE_WORKFLOW_FILE}/runs?limit={PAGE_SIZE}")["workflow_runs"]
        position = turn(runs, own_id, own_event)
        if isinstance(position, OwnTurn):
            return True
        if isinstance(position, Superseded):
            print(f"superseded by run {position.run_id}")
            return False
        print(f"waiting for run {position.run_id}")
        time.sleep(TURN_POLL_SECONDS)


def combine(states):
    """Gitea's CommitStatusStates.Combine."""
    if any(state in FAILED for state in states):
        return "failure"
    if states and all(state in PASSED for state in states):
        return "success"
    return "pending"


def is_required(context, required):
    return any(fnmatch.fnmatchcase(context, pattern) for pattern in required)


def verdict(statuses, required):
    """Gitea's MergeRequiredContextsCommitStatus over the latest status per context."""
    if not statuses:
        return "pending"
    if not required:
        return combine([status.state for status in statuses.values()])
    matched, all_matched = [], True
    for pattern in required:
        hits = [status.state for context, status in statuses.items() if fnmatch.fnmatchcase(context, pattern)]
        matched.extend(hits)
        all_matched = all_matched and bool(hits)
    if not matched:
        return "pending"
    state = combine(matched)
    if all_matched or state == "failure":
        return state
    return "pending"


def reviewer(pull, account):
    """Who the approval goes back to: a human approval beats the queue's own."""
    if pull.approver and pull.approver != account:
        return pull.approver
    return pull.carried_reviewer or pull.approver


def queued(pull, account):
    return (AUTOMERGE in pull.labels and NEEDS_FIX not in pull.labels and not pull.draft
            and not pull.rejected and reviewer(pull, account) is not None)


def failure_comment(pull, required):
    failed = [status for context, status in sorted(pull.statuses.items())
              if status.state in FAILED and (not required or is_required(context, required))]
    lines = [f"- `{status.context}`: {status.state} - {status.target_url}" for status in failed]
    return "\n".join([f"mergeweasel: required checks failed on {pull.head_sha[:7]}:", *lines, "", RED_HINT])


def conflict_comment():
    return f"mergeweasel: rebasing onto `{BASE_BRANCH}` failed with a conflict.\n\n{CONFLICT_HINT}"


def refused_comment(message):
    return f"mergeweasel: Gitea refused the merge: {message}\n\n{REFUSED_HINT}"


def invalid_message_comment(problems, keep_commits):
    lines = [f"- {problem}" for problem in problems]
    return "\n".join(["mergeweasel: what this pull request would land on main is invalid:", *lines, "",
                      COMMITS_HINT if keep_commits else MESSAGE_HINT])


def carry_over_description(reviewer_login, head_sha):
    return f"approved by {reviewer_login} at {head_sha[:7]}"


def decide(pulls, required, account):
    candidates = sorted((pull for pull in pulls if queued(pull, account)), key=lambda pull: (pull.behind, pull.number))
    if not candidates:
        return Idle()
    pull = candidates[0]
    if pull.behind:
        return Rebase(pull.number, pull.head_ref, pull.head_sha, reviewer(pull, account))
    if not required:
        # Without required checks nothing tells a tested head from an untested one.
        return Wait(pull.number)
    state = verdict(pull.statuses, required)
    if state == "failure":
        return FlagNeedsFix(pull.number, failure_comment(pull, required))
    if state == "pending":
        return Wait(pull.number)
    approve_first = pull.approver is None and not pull.queue_approved
    return Merge(pull.number, pull.head_sha, approve_first=approve_first, reviewer=reviewer(pull, account),
                 label_id=pull.labels[AUTOMERGE], keep_commits=KEEP_COMMITS in pull.labels)


def approver(reviews):
    valid = [review for review in reviews
             if review["state"] == "APPROVED" and review["official"]
             and not review["dismissed"] and not review["stale"]]
    return valid[-1]["user"]["login"] if valid else None


def rejected(reviews):
    latest = {}
    for review in reviews:
        if review["official"] and review["state"] in ("APPROVED", "REQUEST_CHANGES"):
            latest[review["user"]["login"]] = review
    return any(review["state"] == "REQUEST_CHANGES" and not review["dismissed"] for review in latest.values())


def queue_approved(reviews, head_sha, account):
    """Official or not: an approval the whitelist ignores must not be repeated every pass."""
    if account is None:
        return False
    return any(review["state"] == "APPROVED" and review["user"]["login"] == account
               and not review["dismissed"] and review["commit_id"] == head_sha for review in reviews)


def renovate_pushed_since_needs_fix(timeline):
    """Timeline ids are chronological; a Renovate push after the latest flag retries the pull request."""
    flags = [entry["id"] for entry in timeline
             if entry["type"] == "label" and (entry.get("label") or {}).get("name") == NEEDS_FIX and entry.get("body") == "1"]
    if not flags:
        return False
    return any(entry["type"] == "pull_push" and entry["user"]["login"] == RENOVATE_ACCOUNT and entry["id"] > max(flags)
               for entry in timeline)


def carried_reviewer(statuses, account):
    """Only the queue's own carry-over counts; without an account (a dry run) any does."""
    for status in statuses:
        creator = (status.get("creator") or {}).get("login")
        if (status["context"] == CARRY_OVER_CONTEXT and status["status"] == "success"
                and (account is None or creator == account)):
            match = CARRY_OVER_PATTERN.match(status.get("description") or "")
            if match:
                return match.group(1)
    return None


def statuses_by_context(statuses, server):
    """The queue's own statuses never count towards its verdict: the carry-over and its own job runs."""
    return {status["context"]: Status(status["context"], status["status"], absolute(server, status.get("target_url") or ""))
            for status in statuses
            if status["context"] != CARRY_OVER_CONTEXT and not status["context"].startswith(f"{QUEUE_WORKFLOW} / ")}


def absolute(server, url):
    return server + url if url.startswith("/") else url


def needs_fix_to_clear(pulls):
    """Step 0: Renovate pushing to a needs-fix pull request retries it."""
    return [pull for pull in pulls if NEEDS_FIX in pull.labels and pull.renovate_retried]


class Conflict(Exception):
    pass


class MergeRefused(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class MessageInvalid(Exception):
    def __init__(self, problems, keep_commits):
        super().__init__("; ".join(problems))
        self.problems = problems
        self.keep_commits = keep_commits


@dataclasses.dataclass(frozen=True)
class State:
    main_sha: str
    required: list
    pulls: list


def read_state(gitea, server, account):
    # branches/main, not branch_protections: the protection API needs repo admin.
    branch = gitea.call("GET", f"/branches/{BASE_BRANCH}")
    main_sha = branch["commit"]["id"]
    required = branch["status_check_contexts"] if branch["enable_status_check"] else []
    pulls = []
    for raw in gitea.paged("/pulls?state=open"):
        labels = {label["name"]: label["id"] for label in raw["labels"]}
        if raw["base"]["ref"] != BASE_BRANCH or AUTOMERGE not in labels:
            continue
        head_sha = raw["head"]["sha"]
        statuses = gitea.call("GET", f"/commits/{head_sha}/status?limit={PAGE_SIZE}")["statuses"] or []
        # compare, not merge_base: Gitea updates merge_base asynchronously after a push.
        behind = gitea.call("GET", f"/compare/{head_sha}...{main_sha}")["total_commits"] > 0
        reviews = gitea.paged(f"/pulls/{raw['number']}/reviews")
        flagged = NEEDS_FIX in labels
        pulls.append(Pull(
            number=raw["number"], head_ref=raw["head"]["ref"], head_sha=head_sha, labels=labels,
            draft=raw.get("draft", False), behind=behind, approver=approver(reviews),
            carried_reviewer=carried_reviewer(statuses, account), rejected=rejected(reviews),
            queue_approved=queue_approved(reviews, head_sha, account),
            renovate_retried=flagged and renovate_pushed_since_needs_fix(gitea.paged(f"/issues/{raw['number']}/timeline")),
            statuses=statuses_by_context(statuses, server)))
    return State(main_sha, required, pulls)


def rebase(gitea, action):
    try:
        gitea.call("POST", f"/pulls/{action.number}/update?style=rebase")
    except urllib.error.HTTPError as error:
        if error.code == 409:
            raise Conflict() from error
        raise
    # Retried: once the rebase is pushed, Gitea dismisses the approval, and without the carry-over
    # the pull request would wait unapproved.
    new_head = retried(lambda: rebased_head(gitea, action.head_ref))
    retried(lambda: gitea.call("POST", f"/statuses/{new_head}", {
        "state": "success", "context": CARRY_OVER_CONTEXT,
        "description": carry_over_description(action.reviewer, action.head_sha)}))


def rebased_head(gitea, head_ref):
    # The git refs API reads the repository itself; the pull request's head.sha may still lag.
    # It matches by prefix, so renovate/gradle also returns renovate/gradle-8.x.
    ref = f"refs/heads/{head_ref}"
    refs = gitea.call("GET", f"/git/{urllib.parse.quote(ref)}")
    head = next((entry["object"]["sha"] for entry in refs if entry["ref"] == ref), None)
    if head is None:
        raise LookupError(f"{ref} not found")
    return head


def merge_request(gitea, action):
    """What Gitea is asked to land; MessageInvalid if it may not land.

    Read here, not in read_state: the title, body and commits can change after the commit-message
    check ran, and only this read decides what lands.
    """
    issues = issue_numbers(gitea)
    if action.keep_commits:
        commits = pull_commits(gitea, action.number)
        problems = commit_rule.commit_problems(commits, issues, action.number)
        request = {"Do": "fast-forward-only", "head_commit_id": action.head_sha}
    else:
        pull_request = gitea.call("GET", f"/pulls/{action.number}")
        message = commit_rule.squash_message(
            pull_request["title"], pull_request.get("body"), issues, action.number)
        problems = list(message.problems)
        request = {"Do": "squash", "MergeTitleField": message.subject, "MergeMessageField": message.body,
                   "head_commit_id": action.head_sha}
    if problems:
        raise MessageInvalid(problems, action.keep_commits)
    return request


def merge(gitea, action):
    # Before the approval: a pull request that may not land is not approved either.
    request = merge_request(gitea, action)
    if action.approve_first:
        gitea.call("POST", f"/pulls/{action.number}/reviews", {
            "event": "APPROVED", "commit_id": action.head_sha,
            "body": f"Approval of {action.reviewer}, carried over the merge queue's rebase."})
    try:
        gitea.call("POST", f"/pulls/{action.number}/merge", request)
    except urllib.error.HTTPError as error:
        message = error_message(error)
        if error.code == 405 and message != TRANSIENT_MERGE_REFUSAL:
            raise MergeRefused(message) from error
        if error.code in (405, 409):
            # Checks still settling or the head moved: the next pass sees the new state.
            print(f"#{action.number}: merge deferred: {message}")
            return
        raise
    # Only after the merge landed: a deferred or refused pull request stays queued.
    gitea.call("DELETE", f"/issues/{action.number}/labels/{action.label_id}")


def relabel(pulls, number, add=None, remove=None):
    """The pass's own view of a label change, so the next decision sees it without a re-read."""
    changed = []
    for pull in pulls:
        if pull.number == number:
            labels = {name: label_id for name, label_id in pull.labels.items() if name != remove}
            if add:
                labels[add] = 0
            pull = dataclasses.replace(pull, labels=labels)
        changed.append(pull)
    return changed


def execute(gitea, action):
    """Carry out a Rebase or Merge; a conflict, a refused merge or an invalid message becomes a needs-fix hand-over."""
    try:
        if isinstance(action, Rebase):
            rebase(gitea, action)
        elif isinstance(action, Merge):
            merge(gitea, action)
    except Conflict:
        return FlagNeedsFix(action.number, conflict_comment())
    except MergeRefused as refusal:
        return FlagNeedsFix(action.number, refused_comment(refusal.message))
    except MessageInvalid as invalid:
        return FlagNeedsFix(action.number, invalid_message_comment(invalid.problems, invalid.keep_commits))
    return action


def run_pass(gitea, server, dry_run, account):
    state = read_state(gitea, server, account)
    pulls = state.pulls
    for retry in needs_fix_to_clear(pulls):
        print(f"#{retry.number}: {RENOVATE_ACCOUNT} pushed, removing {NEEDS_FIX}")
        if not dry_run:
            gitea.call("DELETE", f"/issues/{retry.number}/labels/{retry.labels[NEEDS_FIX]}")
        pulls = relabel(pulls, retry.number, remove=NEEDS_FIX)
    while True:
        action = decide(pulls, state.required, account)
        print(action)
        if not dry_run:
            executed = execute(gitea, action)
            if executed != action:
                print(executed)
            action = executed
        if isinstance(action, FlagNeedsFix):
            if not dry_run:
                gitea.call("POST", f"/issues/{action.number}/labels", {"labels": [NEEDS_FIX]})
                gitea.call("POST", f"/issues/{action.number}/comments", {"body": action.comment})
            pulls = relabel(pulls, action.number, add=NEEDS_FIX)
            continue
        return action
