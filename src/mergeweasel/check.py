"""`mergeweasel check-commits`: check what a pull request would land on the base branch.

Without `keep-commits` the queue squashes: the title and the `## Commit` section become the commit,
and the branch's own commits never land, so they are not checked. With `keep-commits` every commit
lands as it is and is checked one by one. The rule lives in commit_rule, shared with the queue.
"""
import json
import sys

from mergeweasel import commit_rule, config
from mergeweasel.gitea import ApiError, Unreachable


def run(gitea, event_path, settings):
    try:
        with open(event_path, encoding="utf-8") as handle:
            number = json.load(handle)["pull_request"]["number"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"{event_path} is not a pull_request event: {error!r}", file=sys.stderr)
        return 1
    try:
        # Read now, not taken from the event: a re-run replays the event's payload, which holds the
        # title, body and labels from before a fix.
        pull = gitea.pull(number)
        # The queue's label removal after a merge re-runs this check on a landed pull request; after a
        # fast-forward its head is the base branch's head, which must not turn red.
        if pull.merged:
            print("the pull request is merged - it has already landed")
            return 0
        keep_commits = settings.keep_commits in pull.labels
        # The pull request's own version: one that breaks the file fails here, not on the base branch.
        scopes = config.load(gitea, pull.head_sha).scopes
        commits = gitea.pull_commits(number) if keep_commits else []
        subjects = [commit.subject for commit in commits] if keep_commits else [pull.title]
        issues = commit_rule.known_issues(subjects, gitea.is_issue)
    except Unreachable as error:
        print(f"could not reach the Gitea API: {error.message}", file=sys.stderr)
        return 1
    except ApiError as error:
        status = f"HTTP {error.status} " if error.status else ""
        print(f"could not read from the Gitea API: {status}{error.message} ({error.url})", file=sys.stderr)
        # Only Gitea's refusal can be a missing token; a malformed answer is none.
        if error.status:
            print("hint: a private repository needs GITHUB_TOKEN (the run token) in the step's environment",
                  file=sys.stderr)
        return 1
    except config.ConfigError as error:
        print(error)
        return 1

    if keep_commits:
        if not commits:
            print("the pull request has no commits - refusing to pass vacuously")
            return 1
        problems = commit_rule.commit_problems(commits, issues, number, scopes)
        print(f"{settings.keep_commits}: {len(commits)} commit(s) checked, {len(problems)} problem(s)")
    else:
        message = commit_rule.squash_message(pull.title, pull.body, issues, number, scopes)
        problems = list(message.problems)
        print("squash commit:\n")
        print(f"{message.subject}\n\n{message.body}" if message.body else message.subject)
        print(f"\n{len(problems)} problem(s)")
    for problem in problems:
        print(f"  {problem}")
    return 1 if problems else 0
