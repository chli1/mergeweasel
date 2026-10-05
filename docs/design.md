# Design

How mergeweasel works, the Gitea behaviour it relies on, and its security model: why it works the
way it does, and what to keep intact when changing it. For using it, see the
[README](../README.md), [onboarding](onboarding.md) and [usage](usage.md); every fixed value is
listed in [configuration](configuration.md).

## Overview

mergeweasel lands pull requests on a Gitea repository one at a time. It rebases a pull request that
is behind `main`, waits for its checks, and merges it - squashed by default, fast-forwarded with
`keep-commits`. Setting the `automerge` label on an approved pull request is the last human action.

The split follows Renovate: the logic ships as a container image, and each repository carries only
a thin workflow layer that runs it.

- **Image** `mergeweasel`: a Python package, standard library only, with a CLI of two commands:
  - `mergeweasel pass [--dry-run]` - one queue pass, which releases first;
  - `mergeweasel check-commits --event <path>` - checks what a pull request would land.
- **Thin layer** in each repository: `mergeweasel-queue.yml` runs the pass,
  `mergeweasel-commits.yml` runs the check. Both run the image as their job container. This
  repository carries the reference copy and runs on it itself.

The two workflows stay separate files with distinct names, for four reasons:

- The pass ignores every status of its own workflow (`mergeweasel-queue / ...`). A check inside
  that workflow would be ignored too, required or not.
- The pass serializes on the runs of `mergeweasel-queue.yml`. Check runs, which start on every push
  and title edit, would replace or block passes.
- The queue wakes on `workflow_run` of `mergeweasel-commits`. In one file, it would wake itself.
- The check runs on `pull_request`, with the workflow from the pull request and only the run
  token. The queue runs from `main` with the merge token. Separate files keep these two trust
  contexts apart.

Principles:

- **Gitea API only.** The image holds no git and checks nothing out; Gitea rebases, merges and tags
  on the server.
- **Environment only.** The Gitea server and repository come from the Actions variables
  `GITHUB_SERVER_URL` and `GITHUB_REPOSITORY`. Without them the CLI exits with an error.
- **Fixed values first.** Labels, commit types, the Renovate account and the subject format are
  constants; `mergeweasel.toml` holds what a repository configures.

## The queue pass

Each pass reads the current state, takes at most one step on one candidate, and ends. If the
candidate gets flagged `needs-fix`, the pass hands over to the next candidate within the same pass.

Every merge, label change and finished check run wakes the queue, so no schedule is needed. An
approval wakes nothing: the label `automerge` comes after it. Before its step, a pass releases
`main` (see [Releases](#releases)).

### Definitions

- **Queued**: open, base `main`, not a draft, labelled `automerge`, not labelled `needs-fix`,
  without a standing request for changes, and approved - or a trusted Renovate pull request. A
  standing request for changes means that an official reviewer's latest verdict is an undismissed
  `REQUEST_CHANGES`.
- **Approved**: enough reviewers' approvals stand to reach `required_approvals` of `main`'s branch
  protection, read from `GET branches/main`. A standing approval is a valid official approval
  (`APPROVED`, not dismissed, not stale) or a reviewer listed in an approval carry-over on the
  current head; each login counts once. With 0 required approvals, `automerge` alone queues a pull
  request: setting the label is the equivalent of pressing merge. A pull request with too few
  approvals waits; it is not flagged.
- **Trusted Renovate pull request**: posted by `renovate`, with every push in its timeline made by
  `renovate` or the merger, and with the latest `automerge` label added by `renovate`. A push by
  anyone else makes it an ordinary pull request. A human who labels it must approve it like any
  other; otherwise labelling alone would bypass the review requirement. Without any approver account
  configured, it is an ordinary pull request too.
- **Up to date**: `GET compare/{head}...{main}` reports no commits. The pull request's `merge_base`
  is not used, because Gitea updates it asynchronously after a push.
- **Green, red, pending**: Gitea's own rule, applied to the latest status per context and the
  required contexts of `main`'s branch protection. The required contexts come from
  `GET branches/main`, because the protection API needs repository admin. Contexts are glob
  patterns.
  - Any `failure`, `error` or `warning` is red.
  - All `success` or `skipped` is green.
  - Anything else is pending, including a required context without any status - unless another
    context already failed.

  The queue's own statuses never count. Without required contexts the queue merges nothing,
  because no status could tell a tested head from an untested one.

### Steps

0. A `needs-fix` pull request loses the label when its timeline shows a push by `renovate` after the
   latest `needs-fix` label. This way Renovate's rebase retries a failed update without a human.
   The removal re-runs `mergeweasel-commits`, so the pass disregards that pull request's statuses:
   it waits (or rebases, if the pull request is behind) until that run's completion wakes the queue
   again.
1. Read `main`'s head and the open pull requests.
2. Pick the candidate: the lowest-numbered queued pull request that is up to date, otherwise the
   lowest-numbered queued one. If there is none, stop. Preferring up-to-date pull requests keeps
   the one in flight in place.
3. **Behind `main`:** `POST pulls/{n}/update?style=rebase`. A `409` flags `needs-fix` ("conflicts
   with main") and hands over. Otherwise the pass sets the carry-over on the new head and stops;
   the check runs on the new head wake the queue.
4. **Up to date and red:** flag `needs-fix` with a comment that lists every failed context and its
   run, and hand over. A red pull request behind `main` is never flagged; only checks on an
   up-to-date head decide.
5. **Up to date and pending:** stop.
6. **Up to date and green:**
   1. Validate what would land (see [What lands on main](#what-lands-on-main)). An invalid message
      flags `needs-fix` and hands over.
   2. Restore the missing approvals through the approver accounts (see below), or give a trusted
      Renovate pull request the approvals it is missing. Too few usable accounts flag `needs-fix`
      and hand over.
   3. Merge with `head_commit_id` set to the head, then remove `automerge`.

   A `405` other than "Please try again later" (Gitea's mergeable check is still running) flags
   `needs-fix` with Gitea's message. That `405` and a `409` (the head moved) end the pass.

### Approval carry-over

A rebase can make Gitea dismiss the approvals. Approving straight after the rebase would race the
asynchronous dismissal, which would then dismiss the restored approvals too. Instead, the merger
records every standing reviewer in one commit status on the rebased head - context
`mergeweasel/approval`, state `success`, one write. The approver accounts restore approvals only in
step 6, after the checks:

```
approved at <old head>
approved by alice
approved by bob
```

The status is bound to that commit: any later push moves the head away from it. Only a status the
merger created counts, since anyone with write access can set a status of that name. A second
rebase carries the reviewers forward. Approvals by approver accounts on a head that already has a
carry-over are its restorations and are not listed again. Without any standing reviewer, nothing is
written.

Step 6 restores only what is missing: `required_approvals` minus the valid approvals on the head.
The approver accounts approve in the order of `MERGEWEASEL_APPROVER_TOKENS`, and each review names
the reviewer it restores. An account that already approved the head, or that posted the pull
request, is skipped, because Gitea refuses approving one's own pull request. Too few usable
accounts, or none configured, flag `needs-fix`. Once the pull request is approved again by hand and
the flag removed, it queues again - an approval alone wakes no pass.

### Labels

- `automerge` is the human's intent. The queue removes it only from a pull request it merged.
  Removing it withdraws the pull request, even while its checks run.
- `needs-fix` is the queue's verdict, always with a comment that states the reason and what to do.
  Removing it queues the pull request again; the queue itself removes it only in step 0.
- `keep-commits` makes the queue fast-forward the commits as they are instead of squashing them.

### Serialized passes

Passes run one at a time. The pass serializes itself instead of using a workflow `concurrency`
group, because Gitea reports the runs such a group replaces as failed on their commit. A pass lists
the unfinished runs of `mergeweasel-queue.yml` and finds itself by `GITHUB_RUN_ID`:

- If a newer run started by an event exists, this pass ends; the newer one reads a newer state.
- If older runs are unfinished, this pass waits for them.
- A manual dispatch (`GITHUB_EVENT_NAME` = `workflow_dispatch`), possibly a dry run, neither
  replaces another run nor is replaced.
- Without `GITHUB_RUN_ID`, as in a local run, the pass does not wait.

### The merger and the approver accounts

The merger rebases, writes the carry-over and merges. At the start of every pass, its login is read
from `MERGEWEASEL_MERGER_TOKEN` (`GET /user`); the pass needs it to trust a carry-over. The approver
accounts restore approvals. Every pass resolves each token of `MERGEWEASEL_APPROVER_TOKENS` the same
way and prints the logins, never the tokens. The pass fails on a token that cannot be read, a token
without a login, or two tokens of one account - Gitea counts one approval per account.

The approver accounts also give a trusted Renovate pull request the approvals it is missing, in the
same order and with the same skips, and name the reason in the review.

The merger never approves as itself. Its token may be one of the approver tokens; one account then
fills both roles. An account used as an approver must not open pull requests, because Gitea refuses
approving one's own.

A dry run is the exception. Without the merger token - CI runs its dry run with the run token - the
merger is unknown, and the dry run accepts any carry-over as genuine. Without approver tokens it
assumes there are enough approver accounts. It says both in its output, since its decisions can then
differ from a real pass.

### Errors

Any unexpected API response fails the pass and leaves labels and pull requests as they are; the
next event or a manual dispatch retries. Every step checks the current state and repeats only what
is missing, so a failed or replaced pass loses nothing. Every API call has a 30-second timeout and
the job a 30-minute one, because a hung pass would hold up every later pass.

One window remains: after the rebase is pushed, the carry-over must still be written. Reading the
new head and posting the status are retried. A pass that still dies there leaves the pull request
rebased and unapproved until it is approved again. A pass that dies between restoring the approvals
and merging leaves them valid on that head, so the next pass only merges.

## What lands on main

`check-commits` and the queue share one rule, so they cannot disagree. The queue validates again
right before the merge, because the title, body and commits can change after the check ran.

**Subject**: Conventional Commits with the issue as suffix. The format, the types and the scope rule
are in [configuration](configuration.md#fixed-values).

- No scope means no parentheses: `feat(): ...` is rejected, and so is `feat(my ui): ...`.
- `!` marks a breaking change, which makes a release a major one.
- `(#N)` must be an issue of the repository. `(PR #N)` is for a change without an issue and must be
  this pull request.

**Squash** (the default): the pull request's title becomes the subject, and the `## Commit` section
of its body becomes the commit body - from that heading to the next heading of level 1 or 2. Line
endings are normalised and HTML comments removed first. There may be at most one such section, it
must keep to the line limit in [configuration](configuration.md#fixed-values), and it must not
contain a `Co-authored-by:` or "Generated with" line. Without the section, the commit has only a
subject. The branch's own commits never land and are not checked.

**`keep-commits`**: every commit of the pull request (`GET pulls/{n}/commits`) lands as it is, so
each one is checked: its subject, its reference, and that it has a single parent. Subjects starting
with `fixup!` or `squash!`, and git's `Revert "..."` subjects, are exempt - they inherit the
original commit's reference.

**Merged**: removing `automerge` after a merge re-runs the check on the landed pull request. A
merged pull request passes without a check: after a fast-forward its head is `main`'s head, whose
status must not turn red.

## Releases

Every pass releases the repository it runs in, before its step. If `main` holds releasing commits
since the previous release, the pass creates a Gitea release on `main`'s head - a lightweight tag
and the notes - with one `POST releases`.

The template `release.tag` in `mergeweasel.toml` sets the version format
([configuration](configuration.md#the-release-tag)); the default is `v{major}.{minor}.{patch}`. The
pass reads the file from `main` with one `GET contents` before releasing, so a pull request cannot
change the rules it is released under. The template compiles to an anchored regular expression: its
text is escaped, and each placeholder becomes the pattern of its values. A tag is a release exactly
when it matches.

The previous release is the nearest commit in `main`'s history that carries a matching tag; if it
carries several, a SemVer template takes the highest version. Because `main` is linear, that commit
is its predecessor, whatever other branches tag: a maintenance branch cut once `main` moved past its
version line never decides `main`'s next version. The pass reads every tag with one
`GET git/refs/tags` and walks `main` back, 50 commits per call, up to the limit in
[configuration](configuration.md#the-release-tag). The commit types decide the bump:

- `!` - major, in 0.x too;
- `feat` - minor;
- `fix`, `perf`, `revert` and git's `Revert "..."` - patch;
- every other type releases nothing, and neither does a subject outside the rule.

The notes list the releasing commits, oldest first, under "Breaking changes", "Features" and
"Fixes". A breaking change also shows its commit body.

Without a matching tag, a SemVer template releases nothing: there is no previous version to
increase. A CalVer template starts from `main`'s head instead: the head commit alone decides the
first release, and the notes list only that commit.

`{count}` comes from the `X-Total-Count` header of one `GET commits` on the head; date and time come
from the pass's UTC clock.

Each merge wakes a pass, so each merge gets its own release. A failed release is printed and fails
the job at its end, but the pass still runs, so a release problem never holds up the queue. A
release fails when

- no matching tag lies on `main` within the search limit;
- the new version is already tagged on another commit - for example a second CalVer release on the
  same day;
- `mergeweasel.toml` is invalid;
- Gitea gives an unexpected answer.

A tag that appears between reading the tags and creating the release comes from a parallel pass
and is not an error.

The merger creates the release, so the tag push starts the repository's tag-triggered workflows;
a push by the Actions bot would start none. For mergeweasel itself, the `release` job of `ci.yml`
publishes the image to a public registry as `X.Y.Z` and `latest`. The version exists only as the
tag and as the image label `org.opencontainers.image.version`. Consumers pin an exact version.

A change that needs consumers to update their copies of `mergeweasel-*.yml` is breaking (`!`),
because Renovate bumps only the image pin. This repository's Renovate types its updates so that
only an update of the image's base image releases (`fix(deps)`). The pin bump of mergeweasel's own
image is `ci(deps)` and releases nothing, so a release and the pin bump it causes cannot loop.

## Gitea behaviour relied on

Verified against the Gitea pinned in `e2e/gitea.Dockerfile`. The end-to-end test (`e2e/`)
exercises stale approvals, the rebase, the squash, the refs and the refused self-approval against
it, so a Gitea version that breaks one of them fails there first. Renovate bumps that pin, and a
new major or minor Gitea version is a release whose notes say "support Gitea vX.Y".

- **Stale approvals**: with `dismiss_stale_approvals`, a push dismisses approvals only if the pull
  request's diff against the new merge base changed. So a rebase dismisses them exactly when a file
  the pull request touches also changed on `main`. The dismissal runs asynchronously.
- **Own pull requests**: approving one's own pull request is refused with `422`.
- **Branch info**: `GET branches/{branch}` reports `required_approvals` without repository admin.
- **Commit status descriptions** keep several lines unchanged; the carry-over relies on it.
- **The Actions bot triggers nothing**: events whose doer is the built-in Actions user start no
  workflow. A rebase pushed with the run token would never start checks, so the queue needs an
  account of its own.
- **Triggers**: `workflow_run` supports `workflows` and `types` filters. On `pull_request_target`,
  `workflow_run` and `workflow_dispatch`, Gitea takes the workflow from `main`.
- **`concurrency`** replaces a waiting run with a newly arriving one and reports the replaced run as
  failed - hence the self-serialization above.
- **Rebase**: `POST pulls/{n}/update?style=rebase` answers `409` on a conflict.
- **Squash**: `POST pulls/{n}/merge` with `Do: squash`, `MergeTitleField` and `MergeMessageField`
  produces exactly that message. The author is the pull request's poster, the committer the merging
  account. By default (the server setting `ADD_CO_COMMITTER_TRAILERS`), Gitea appends a
  `Co-authored-by` trailer to every queue merge.
- **Merge refusal**: `405` with "Please try again later" while the mergeable check still runs; `409`
  when `head_commit_id` is no longer the head.
- **Refs**: `GET git/refs/heads/<branch>` matches by prefix and reads the repository directly,
  unlike the pull request's `head.sha`, which can lag behind after a rebase.
- **Lists** cap at 50 entries per page, whatever `limit` asks for.
- **Releases**: `POST releases` with a new `tag_name` creates the lightweight tag on
  `target_commitish` and fires its push with the calling account as pusher.
- **Tag refs**: `GET git/refs/tags` lists every tag in one answer, unpaged.
- **Commit count**: `GET commits?sha=<sha>&limit=1` sends the number of commits in the history of
  `<sha>` as `X-Total-Count`.
- **Contents**: `GET contents/<path>?ref=main` answers a file base64-encoded, and `404` without it.
- **Tag names** with `+` pass `POST releases` unchanged.

## Security model

- **`MERGEWEASEL_MERGER_TOKEN`** has merge rights. It appears only in `mergeweasel-queue.yml`,
  whose triggers all take the workflow - and with it the image pin - from `main`. The pass never
  checks out or executes pull request content, so a pull request cannot run code with the token
  through this path.
- **`MERGEWEASEL_APPROVER_TOKENS`**: every approver token can satisfy the review requirement on its
  own, so each is as sensitive as the merger token. Like the merger token, they appear only in
  `mergeweasel-queue.yml`.
- **The run token** serves `check-commits` and the dry run in CI. Keeping the merge token out of
  CI protects against a bug in new code - a dry run that writes - not against an attacker.
- **The trust boundary is write access.** Gitea hands repository secrets to every non-fork pull
  request run. So anyone with write access can read the merge token through a workflow on their own
  branch, and anyone who may push tags can publish an image. The approvals whitelist does not
  narrow that.
- **Releases**: whoever lands a releasing commit causes a release, and anyone who may push tags can
  publish one - both inside the write-access boundary.
- **The `renovate` account** gets its own updates approved, so its token is as sensitive as an
  approver token. The required checks remain the gate.
- **Carry-over**: trusted only from the merger. A forged status counts only in a dry run, which
  writes nothing.
