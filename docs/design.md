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
  - `mergeweasel pass [--dry-run]` - one queue pass, which releases first;
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
`automerge` comes after it. Before its step, a pass releases `main` (see [Releases](#releases)).

### Definitions

- **Queued**: open, base `main`, not a draft, label `automerge`, no label `needs-fix`, no standing
  request for changes (an official reviewer's latest verdict is an undismissed `REQUEST_CHANGES`),
  and approved - or a trusted Renovate pull request.
- **Approved**: the reviewers whose approval stands - valid official approvals (`APPROVED`, not
  dismissed, not stale) and the reviewers of an approval carry-over on the current head, each login
  once - reach `required_approvals` of `main`'s branch protection, read from `GET branches/main`.
  With 0 required, `automerge` alone queues a pull request: setting it is the equivalent of
  pressing merge. A pull request with too few waits; it is not flagged.
- **Trusted Renovate pull request**: posted by `renovate`, every push in its timeline by `renovate`
  or the merger, and the latest `automerge` label added by `renovate`. Anyone else's push makes it
  an ordinary pull request; a human who labels it approves it like any other, or labelling alone
  would bypass the review requirement. Without any approver account configured, it is an ordinary
  pull request too.
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
   `needs-fix` label loses the label. Renovate's rebase retries a failed update without a human.
   The removal re-runs `mergeweasel-commits`, so the pass forgets that pull request's statuses: it
   waits (or is rebased, if behind) until that run's completion wakes the queue again.
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
   `needs-fix` and hands over. Restore the missing approvals through the approver accounts (see
   below), or give a trusted Renovate pull request the approvals it is missing - too few usable
   accounts flag `needs-fix` and hand over - then merge with `head_commit_id` =
   the head, then remove `automerge`. A `405` other than "Please try again later" (Gitea's mergeable check still running)
   flags `needs-fix` with Gitea's message; that and a `409` (the head moved) end the pass.

### Approval carry-over

A rebase can make Gitea dismiss the approvals. Approving straight after the rebase races the
asynchronous dismissal, which would dismiss the restored approvals too. Instead the merger records
every standing reviewer in one commit status on the rebased head - context `mergeweasel/approval`,
state `success`, one write - and the approver accounts restore approvals only in step 6, after the
checks:

```
approved at <old head>
approved by alice
approved by bob
```

The status is bound to that commit: any later push moves the head away from it. Only a status the
merger created counts, since anyone with write access can set a status of that name. A second
rebase carries the reviewers forward; approvals by approver accounts on a head that already has a
carry-over are its restorations and are not listed again. Without any standing reviewer nothing is
written.

Step 6 restores only what is missing: `required_approvals` minus the valid approvals on the head.
The approver accounts approve in the order of `MERGEWEASEL_APPROVER_TOKENS`, each naming the
reviewer it restores; an account that already approved the head, or that posted the pull request,
is skipped - Gitea refuses approving one's own pull request. Too few usable accounts, none
configured included, flag `needs-fix`: approved again by hand and the flag removed, the pull
request queues again - an approval alone wakes no pass.

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

### The merger and the approver accounts

The merger rebases, writes the carry-over and merges. Its login comes from
`MERGEWEASEL_MERGER_TOKEN` (`GET /user`) at the start of every pass; it needs it to trust a
carry-over. The approver accounts restore approvals: every pass resolves each token of
`MERGEWEASEL_APPROVER_TOKENS` the same way and prints the logins, never the tokens. A token that
cannot be read, a token without a login, or two tokens of one account fail the pass - Gitea counts
one approval per account.

The approver accounts also give a trusted Renovate pull request the approvals it is missing, in
the same order and with the same skips, naming the reason in the review.

The merger never approves as itself. Its token may be one of the approver tokens; one account then
fills both roles. An account used as approver must not open pull requests: Gitea refuses
approving one's own.

The exception is a dry run. Without the merger token, as CI runs one with the run token, the merger
is unknown and the dry run accepts any carry-over as genuine; without approver tokens it assumes
enough approver accounts. It says both in its output; its decisions can then differ from a real
pass.

### Errors

Any unexpected API response fails the pass and leaves labels and pull requests as they are; the
next event or a manual dispatch retries. Every step checks the current state and repeats only what
is missing, so a failed or replaced pass loses nothing. Every API call has a 30-second timeout and
the job a 30-minute one: a hung pass would hold every later pass.

One window remains: after the rebase is pushed, the carry-over must still be written. Reading the
new head and posting the status are retried; a pass that still dies there leaves the pull request
rebased and unapproved until it is approved again. A pass dying between the restored approvals and
the merge leaves them valid on that head; the next pass only merges.

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

Every pass releases the repository it runs in, before its step: if `main` holds releasing commits
since the previous release, it creates a Gitea release on `main`'s head - the lightweight tag
`vX.Y.Z` and the notes - with one `POST releases`.

The previous release is the nearest commit in `main`'s history that carries a `vX.Y.Z` tag - the
highest one, if it carries several. `main` is linear, so that is its predecessor, whatever other
branches tag: a maintenance branch cut once `main` moved past its line never decides `main`'s next
version. The pass reads every tag with one `GET git/refs/tags` and walks `main` back, 50 commits per
call, as far as the limit in [configuration](configuration.md#fixed-values). The bump comes from the commit types:

- `!` - major, in 0.x too;
- `feat` - minor;
- `fix`, `perf`, `revert` and git's `Revert "..."` - patch;
- every other type releases nothing; neither does a subject outside the rule.

The notes list the releasing commits oldest first under "Breaking changes", "Features" and
"Fixes"; a breaking change carries its commit body, which names what users must change. Without a
`vX.Y.Z` tag nothing is released: there is nothing to bump from.

Each merge wakes a pass, so each merge gets its own release. A release that fails is printed and
fails the job at its end; the pass still runs, so a release problem never holds up the queue. It
fails when no `vX.Y.Z` tag lies on `main` within that limit, when the next version is tagged
on another commit already, or on any unexpected API answer. A tag that appears between reading the
tags and creating the release is another pass's, not an error.

The merger creates the release, so the tag push starts the repository's tag-triggered workflows;
the Actions bot's would start none. mergeweasel's own: the `release` job of `ci.yml` publishes the
image to a public registry as `X.Y.Z` and `latest`; the version exists only as the tag and the
image label `org.opencontainers.image.version`. Consumers pin an exact version.

A change that needs consumers to update their copies of `mergeweasel-*.yml` is breaking (`!`) -
Renovate bumps only the image pin. This repository's Renovate types its updates so that only the
image's base image releases (`fix(deps)`); the pin bump of its own image is `ci(deps)` and releases
nothing, so release and pin bump cannot loop.

## Gitea behaviour relied on

Verified against the Gitea pinned in `e2e/gitea.Dockerfile`. The end-to-end test (`e2e/`)
exercises stale approvals, the rebase, the squash, the refs and the refused self-approval against
it - a version bump that breaks one of them fails there first. Renovate bumps that pin, and a new
major or minor Gitea is a release whose notes say "support Gitea vX.Y".

- **Stale approvals**: with `dismiss_stale_approvals`, a push dismisses approvals only if the pull
  request's diff against the new merge base changed - so a rebase dismisses them exactly when a
  file the pull request touches also changed on `main`. The dismissal runs asynchronously.
- **Own pull requests**: approving one's own pull request is refused with `422`.
- **Branch info**: `GET branches/{branch}` reports `required_approvals` without repository admin.
- **Commit status descriptions** keep several lines unchanged; the carry-over relies on it.
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
- **Releases**: `POST releases` with a new `tag_name` creates the lightweight tag on
  `target_commitish` and fires its push with the calling account as pusher.
- **Tag refs**: `GET git/refs/tags` lists every tag in one answer, unpaged.

## Security model

- **`MERGEWEASEL_MERGER_TOKEN`** has merge rights. It appears only in `mergeweasel-queue.yml`,
  whose triggers all take the workflow - and with it the image pin - from `main`. The pass never
  checks out or executes pull request content, so a pull request cannot run code with the token
  through this path.
- **`MERGEWEASEL_APPROVER_TOKENS`**: every approver token can satisfy the review requirement on its
  own, so each is as sensitive as the merger token. They appear only in `mergeweasel-queue.yml`,
  like the merger token.
- **The run token** serves `check-commits` and the dry run in CI. Keeping the merge token out of
  CI protects against a bug in new code - a dry run that writes - not against an attacker.
- **The trust boundary is write access.** Gitea hands repository secrets to every non-fork pull
  request run, so anyone with write access can read the merge token through a workflow on their own
  branch, and anyone who may push tags can publish an image. The approvals whitelist does not
  narrow that.
- **Releases**: whoever lands a `feat` or `fix` causes a release, and anyone who may push tags can
  publish one - inside the write-access boundary.
- **The `renovate` account** gets its own updates approved: its token is as sensitive as an
  approver token. The required checks remain the gate.
- **Carry-over**: trusted only from the merger; a forged status counts in a dry run only, which
  writes nothing.
