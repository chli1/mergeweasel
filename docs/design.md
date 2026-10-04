# Design

How mergeweasel works, the Gitea behaviour it relies on, and its security model: why it works the
way it does and what to keep intact when changing it. For using it, see the [README](../README.md),
[onboarding](onboarding.md) and [usage](usage.md); every fixed value is listed in
[configuration](configuration.md).

## Overview

mergeweasel lands pull requests on a Gitea repository one at a time: it rebases a pull request that
is behind `main`, waits for its checks, and merges it - squashed by default, fast-forwarded with
`keep-commits`. Setting the `automerge` label on an approved pull request is the last human action.

The split follows Renovate: the logic ships as a container image, and each repository carries only
a thin workflow layer that runs it.

- **Image** `mergeweasel`: a Python package, standard library only, with a CLI of two commands:
  - `mergeweasel pass [--dry-run]` - one queue pass;
  - `mergeweasel check-commits --event <path>` - the check of what a pull request would land.
- **Thin layer** in each repository: `mergeweasel-queue.yml` runs the pass,
  `mergeweasel-commits.yml` runs the check. Both run the image as their job container. This
  repository carries the reference copy and runs on it itself.

The two workflows stay separate files with distinct names:

- The pass ignores every status of its own workflow (`mergeweasel-queue / ...`); a check inside
  that workflow would be ignored too, required or not.
- The pass serializes on the runs of `mergeweasel-queue.yml`; check runs on every push and title
  edit would replace or block passes.
- The queue wakes on `workflow_run` of `mergeweasel-commits`; in one file it would wake itself.
- The check runs on `pull_request` - workflow from the pull request, run token only; the queue runs
  from `main` with the merge token. Separate files keep the two trust contexts apart.

Principles:

- **Gitea API only.** The image holds no git and checks nothing out; Gitea rebases, merges and tags
  server-side.
- **Environment only.** The Gitea server and repository come from the Actions variables
  `GITHUB_SERVER_URL` and `GITHUB_REPOSITORY`; without them the CLI exits with an error.
- **Fixed values first.** Labels, commit types, the Renovate account and the subject format are
  constants. A configuration file arrives with the first consumer that needs a different value,
  and the constants become its defaults.

## The queue pass

Each pass reads the current state, takes at most one step on one candidate and ends; a candidate
flagged `needs-fix` hands over to the next one within the same pass. Every merge, label change and
finished check run wakes the queue, so no schedule is needed. An approval wakes nothing: the label
`automerge` comes after it.

### Definitions

- **Queued**: open, base `main`, not a draft, label `automerge`, no label `needs-fix`, no standing
  request for changes (an official reviewer's latest verdict is an undismissed `REQUEST_CHANGES`),
  and approved.
- **Approved**: a valid official approval (`APPROVED`, not dismissed, not stale), or an approval
  carry-over on the current head. An unapproved pull request with `automerge` waits; it is not
  flagged.
- **Up to date**: `GET compare/{head}...{main}` reports no commits. Not the pull request's
  `merge_base`, which Gitea updates asynchronously after a push.
- **Green, red, pending**: Gitea's own rule over the latest status per context and the required
  contexts of `main`'s branch protection, read from `GET branches/main` (the protection API needs
  repository admin). Contexts are glob patterns. Any `failure`, `error` or `warning` is red; all
  `success` or `skipped` is green; anything else is pending, a required context without any status
  included - unless another one already failed. The queue's own statuses never count. Without
  required contexts the queue merges nothing: no status could tell a tested head from an untested
  one.

### Steps

0. A `needs-fix` pull request whose timeline shows a push by `renovate` after the latest
   `needs-fix` label loses the label; the pass continues. Renovate's rebase retries a failed update
   without a human.
1. Read `main`'s head and the open pull requests.
2. Candidate: the lowest-numbered queued pull request that is up to date, else the lowest-numbered
   queued one; none - stop. Preferring up-to-date ones keeps the pull request in flight in place.
3. Behind `main`: `POST pulls/{n}/update?style=rebase`. A `409` flags `needs-fix` ("conflicts with
   main") and hands over; otherwise the pass sets the carry-over on the new head and stops - the
   check runs on the new head wake the queue.
4. Up to date and red: flag `needs-fix` with a comment listing every failed context and its run,
   hand over. A red pull request behind `main` is never flagged; only checks on an up-to-date head
   decide.
5. Up to date and pending: stop.
6. Up to date and green: validate what would land (see below) - an invalid message flags
   `needs-fix` and hands over. Approve as the queue if the only approval is the carry-over and the
   queue has not approved this head yet, then merge with `head_commit_id` = the head, then remove
   `automerge`. A `405` other than "Please try again later" (Gitea's mergeable check still running)
   flags `needs-fix` with Gitea's message; that and a `409` (the head moved) end the pass.

### Approval carry-over

A rebase can make Gitea dismiss the approval. Approving straight after the rebase races the
asynchronous dismissal, which would dismiss the queue's approval too. Instead the queue records the
approval as a commit status on the rebased head - context `mergeweasel/approval`, state `success`,
description `approved by <reviewer> at <old head>` - and approves only in step 6, after the checks.

The status is bound to that commit: any later push moves the head away from it. Only a status the
queue's own account created counts, since anyone with write access can set a status of that name.
A second rebase before the merge carries the carry-over forward, reviewer included.

### Labels

- `automerge` is the human's intent. The queue removes it only from a pull request it merged;
  removing it withdraws the pull request, mid-check included.
- `needs-fix` is the queue's verdict, always with a comment stating the reason and what to do.
  Removing it queues the pull request again; the queue removes it only in step 0.
- `keep-commits` makes the queue fast-forward the commits as they are instead of squashing.

### Serialized passes

Passes run one at a time, serialized by the pass itself, not by a workflow `concurrency` group:
Gitea reports the runs such a group replaces as failed on their commit. A pass lists the unfinished
runs of `mergeweasel-queue.yml` and finds itself by `GITHUB_RUN_ID`:

- a newer run started by an event exists - this pass ends, the newer one reads a newer state;
- older runs are unfinished - this pass waits for them;
- a manual dispatch (`GITHUB_EVENT_NAME` = `workflow_dispatch`), possibly a dry run, neither
  replaces nor is replaced;
- without `GITHUB_RUN_ID`, a local run, the pass does not wait.

### The queue's own account

The queue reads its login from `MERGEWEASEL_TOKEN` (`GET /user`) at the start of every pass. It
needs it to recognise its own approvals and to trust a carry-over. A pass that cannot read it fails.

The one exception is a dry run without the token, as CI runs one with the run token: the login is
unknown, so the dry run accepts any carry-over as genuine and says so in its output. Its decisions
can then differ from a real pass in whether a merge would approve first.

### Errors

Any unexpected API response fails the pass and leaves labels and pull requests as they are; the
next event or a manual dispatch retries. Every step checks the current state and repeats only what
is missing, so a failed or replaced pass loses nothing. Every API call has a 30-second timeout and
the job a 30-minute one: a hung pass would hold every later pass.

One window remains: after the rebase is pushed, the carry-over must still be written. Reading the
new head and posting the status are retried; a pass that still dies there leaves the pull request
rebased and unapproved until it is approved again.

## What lands on main

`check-commits` and the queue share one rule, so they cannot disagree. The queue validates again
right before the merge: the title, body and commits can change after the check ran.

**Subject**: Conventional Commits with the issue as suffix; format, types and the scope rule are
in [configuration](configuration.md#fixed-values).

- No scope means no parentheses - `feat(): ...` is rejected, as is `feat(my ui): ...`.
- `!` marks a breaking change and is the major bump of a release.
- `(#N)` must be an issue of the repository; `(PR #N)` is for a change without an issue and must be
  this pull request.

**Squash** (the default): the pull request's title is the subject, the `## Commit` section of its
body the commit body - from that heading to the next heading of level 1 or 2. Line endings are
normalised and HTML comments removed first. At most one such section, within the line limit
of [configuration](configuration.md#fixed-values); no `Co-authored-by:` or "Generated with" line. No
section means a subject-only commit. The branch's own commits never land and are not checked.

**`keep-commits`**: every commit of the pull request (`GET pulls/{n}/commits`) lands as it is and is
checked: its subject, its reference, a single parent. `fixup!`, `squash!` and git's
`Revert "..."` subjects are exempt - they inherit the original commit's reference.

**Merged**: removing `automerge` after a merge re-runs the check on the landed pull request. A
merged pull request passes without a check - after a fast-forward its head is `main`'s head, whose
status must not turn red.

## Releases

The image is published to a public registry as `X.Y.Z` and `latest` when a tag `vX.Y.Z` is pushed;
the version exists only as that tag and the image label `org.opencontainers.image.version`.
Consumers pin an exact version.

Release rule (#2): at the start of every pass the queue tags `main`'s head if commits since the
last `vX.Y.Z` tag touched an image input - the `Dockerfile`, the `.dockerignore`, or a path its
allowlist lets through, so a base image bump releases too. The bump comes from their types: `!`
major, `feat` minor, anything else patch. A commit touching only workflows, such as Renovate
bumping the image pin, never releases, so release and pin bump cannot loop. The tags are lightweight; `v0.1.0` is set by hand, and the first `!` change makes `1.0.0`.

## Gitea behaviour relied on

Verified against Gitea v28. The end-to-end test (`e2e/`) exercises stale approvals, the rebase, the
squash and the refs against the Gitea version pinned in `e2e/gitea.Dockerfile` - a version bump that
breaks one of them fails there first.

- **Stale approvals**: with `dismiss_stale_approvals`, a push dismisses approvals only if the pull
  request's diff against the new merge base changed - so a rebase dismisses them exactly when a
  file the pull request touches also changed on `main`. The dismissal runs asynchronously.
- **The Actions bot triggers nothing**: events whose doer is the built-in Actions user start no
  workflow. A rebase pushed with the run token would never start checks, so the queue needs an
  account of its own.
- **Triggers**: `workflow_run` supports `workflows` and `types` filters. On `pull_request_target`,
  `workflow_run` and `workflow_dispatch` Gitea takes the workflow from `main`.
- **`concurrency`** replaces a waiting run with a newly arriving one and reports the replaced run as
  failed - hence the self-serialization above.
- **Rebase**: `POST pulls/{n}/update?style=rebase` answers `409` on a conflict.
- **Squash**: `POST pulls/{n}/merge` with `Do: squash`, `MergeTitleField` and `MergeMessageField`
  produces exactly that message. The author is the pull request's poster, the committer the merging
  account. By default (the server setting `ADD_CO_COMMITTER_TRAILERS`), Gitea appends a
  `Co-authored-by` trailer to every queue merge.
- **Merge refusal**: `405` with "Please try again later" while the mergeable check still runs; `409`
  when `head_commit_id` no longer is the head.
- **Refs**: `GET git/refs/heads/<branch>` matches by prefix and reads the repository directly, unlike
  the pull request's `head.sha`, which can lag after a rebase.
- **Lists** cap at 50 entries per page whatever `limit` asks for.

## Security model

- **`MERGEWEASEL_TOKEN`** has merge rights. It appears only in `mergeweasel-queue.yml`, whose
  triggers all take the workflow - and with it the image pin - from `main`. The pass never checks
  out or executes pull request content, so a pull request cannot run code with the token through
  this path.
- **The run token** serves `check-commits` and the dry run in CI. Keeping the merge token out of
  CI protects against a bug in new code - a dry run that writes - not against an attacker.
- **The trust boundary is write access.** Gitea hands repository secrets to every non-fork pull
  request run, so anyone with write access can read the merge token through a workflow on their own
  branch, and anyone who may push tags can publish an image. The approvals whitelist does not
  narrow that.
- **Carry-over**: trusted only from the queue's own account; a forged status counts in a dry run
  only, which writes nothing.
