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
- **The repository travels, the instance stays outside.** A repository's commits must work
  unchanged on another Gitea, with other account names, other labels and another default branch.
  What belongs to the repository's content - the commit and release rules - lives in
  `mergeweasel.toml`. What belongs to the Gitea instance - the label names, the Renovate account,
  the runner label - comes from Gitea Actions variables, and the base branch is the repository's
  default branch ([instance settings](#instance-settings)). Everything else is a constant.

Throughout this document, `main` stands for the repository's default branch, and the labels go by
their default names.

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
- **Trusted Renovate pull request**: posted by the Renovate account, with every push in its timeline
  made by that account or the merger, and with the latest `automerge` label added by that account. A
  push by anyone else makes it an ordinary pull request. A human who labels it must approve it like
  any other; otherwise labelling alone would bypass the review requirement. Without any approver
  account configured, it is an ordinary pull request too, and so is every pull request while no
  Renovate account is configured.
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

0. A `needs-fix` pull request loses the label when its timeline shows a push by the Renovate
   account after the latest `needs-fix` label. This way Renovate's rebase retries a failed update
   without a human.
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
   1. Validate what would land (see [What lands on main](#what-lands-on-main)). An invalid message,
      or an invalid `mergeweasel.toml` on the branch, flags `needs-fix` and hands over. The
      validation only reads, so a dry run runs it too and flags and hands over as a real pass does.
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

### Instance settings

Every run of either command reads the instance settings first and prints them, so the log shows
which names it worked with:

- **The base branch** is the repository's `default_branch` (`GET repos/{owner}/{repo}`). The
  queue lands on it, releases it and takes its branch protection; a pull request against another
  branch is not a candidate.
- **The label names and the Renovate account** come from the environment variables
  `MERGEWEASEL_AUTOMERGE_LABEL`, `MERGEWEASEL_NEEDS_FIX_LABEL`, `MERGEWEASEL_KEEP_COMMITS_LABEL` and
  `MERGEWEASEL_RENOVATE_ACCOUNT`. The workflows fill them from the Actions variables of the same
  names. An empty value is an unset one: `${{ vars.X }}` of an unset variable is the empty
  string. An unset label keeps its default name; an unset Renovate account turns the Renovate rule
  off, because a trusted account must never be a default.

Two roles on one label are an error, since the queue could no longer tell a verdict from an intent.
The pass then checks that the required labels exist, in the repository or in its organization,
because a misspelt name would otherwise leave the queue idle without a word. A missing one fails
the pass before the release. `automerge` and `needs-fix` are always required. `keep-commits` is only
when its variable names it: a repository without it squashes every pull request, but a name someone
set on purpose has to be right. If the organization's labels cannot be read - a user's repository, or a dry run
with the run token - the pass says so and goes on unchecked.

The workflows cannot filter on the default branch in `on:`, which takes no expressions; a
`branches` filter would have to name the branch literally. Instead each job carries an `if:` that
compares the pull request's base with `github.event.repository.default_branch`. Gitea evaluates it
before any runner takes the job, so a pull request against another branch costs no runner time and
shows the job as skipped. The queue's `if:` applies only to `pull_request_target`: its other
triggers carry no pull request. The runner label is `${{ vars.MERGEWEASEL_RUNNER_LABEL ||
'ubuntu-latest' }}`.

### Errors

Any unexpected API response fails the pass and leaves labels and pull requests as they are; the
next event or a manual dispatch retries. Every step checks the current state and repeats only what
is missing, so a failed or replaced pass loses nothing. Every API call has a 30-second timeout and
the job a 30-minute one, because a hung pass would hold up every later pass.

One window remains: after the rebase is pushed, the carry-over must still be written. Reading the
new head and posting the status are retried after a network error or a server error (5xx); an
answer that waiting does not change, like a branch deleted meanwhile, is not retried. A pass that
still dies there leaves the pull request rebased and unapproved until it is approved again. A pass
that dies between restoring the approvals and merging leaves them valid on that head, so the next
pass only merges.

## What lands on main

`check-commits` and the queue share one rule, so they cannot disagree. The queue validates again
right before the merge, because the title, body and commits can change after the check ran.

**Subject**: one of the forms in `commits.subject` - by default Conventional Commits with the issue
as suffix. The forms and their placeholders are in
[configuration](configuration.md#the-subject), the types in
[configuration](configuration.md#types-and-releases), the scopes in
[configuration](configuration.md#scopes). Each form compiles to an anchored regular expression; the
first form a subject fits decides, so the order of the list is the order of precedence.

- No scope means no parentheses: `feat(): ...` is rejected, and so is `feat(my ui): ...`.
- The scope is free by default. A repository can restrict it to a fixed list with `commits.scopes`
  in `mergeweasel.toml` ([configuration](configuration.md#scopes)).

The commit check reads `mergeweasel.toml` from the pull request's head, not from `main`. Read from
`main`, an invalid file would fail the check of every pull request - including the one that fixes
the file, which could then only be merged by hand. Read from the head, the invalid file fails only
the pull request that introduces it; that one is flagged `needs-fix`, and the file never reaches
`main` through the queue. As a side effect, a pull request that changes the scope list or the
subject forms is checked against what it brings, which is what its reviewers see in the diff.
- `!` marks a breaking change, which makes a release a major one.
- `{issue}` must be an issue of the repository; `{pull}` must be this pull request. `{key}` names
  another tracker's issue, which mergeweasel cannot look up; only its project is checked, against
  `commits.projects`.

**Squash** (the default): the pull request's title becomes the subject, and the `## Commit` section
of its body becomes the commit body - from that heading to the next heading of level 1 or 2. Line
endings are normalised and HTML comments removed first. There may be at most one such section, it
must keep to the line limit in [configuration](configuration.md#the-line-length), and it must not
contain a `Co-authored-by:` or "Generated with" line. Without the section, the commit has only a
subject. The branch's own commits never land and are not checked.

**`keep-commits`**: every commit of the pull request (`GET pulls/{n}/commits`) lands as it is, so
each one is checked: its subject, its reference, and that it has a single parent. Subjects starting
with `fixup!` or `squash!`, and git's `Revert "..."` subjects, are exempt - they inherit the
original commit's reference.

**Merged**: removing `automerge` after a merge re-runs the check on the landed pull request. A
merged pull request passes without a check: after a fast-forward its head is `main`'s head, whose
status must not turn red.

**The pull request as it is now**: the check takes only the pull request's number from the event
and reads the title, body, labels, head and merged state with `GET pulls/{n}` when it runs. A re-run
of a job replays the event it started with, so a check that judged the payload would fail again
after the title was fixed, and a slow run could report on an older title or label set than a run
that started after it. Because the check reads the pull request when the job runs, a re-run judges
the current text and the run that finishes last judges the latest state.

## Releases

Every pass releases the repository it runs in, before its step. If `main` holds releasing commits
since the previous release, the pass creates a Gitea release on `main`'s head - a lightweight tag
and the notes - with one `POST releases`.

`release.enabled = false` in `main`'s `mergeweasel.toml` switches the step off
([configuration](configuration.md#switching-the-release-off)): the pass reads and checks the file,
prints that the release is off, and reads no tags. A switch independent of the tags lets a
repository whose tags come from other tooling opt out.

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
[configuration](configuration.md#the-release-tag). The commit types decide the bump, by the lists in
[configuration](configuration.md#types-and-releases):

- `!` - major, in 0.x too;
- a type in `release.minor_types` - minor;
- a type in `release.patch_types` - patch;
- every other type releases nothing, and neither does a subject outside the rule.

git's `Revert "..."` counts as the type `revert`.

The release reads the subjects with `main`'s `commits.subject`, the same file it takes `release.tag`
from. It reads the types it releases from the same file, and reads a commit's type even when
`commits.types` no longer lists it. A commit outside these forms releases nothing; the release
prints it, so a change of the forms that left unreleased commits behind shows in the log
([configuration](configuration.md#the-subject) describes the change in two steps).

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

The job then attaches `mergeweasel-commits.yml` and `mergeweasel-queue.yml` to the release, as
they stand at the tag but pinned to the image it just pushed (`X.Y.Z@sha256:...`). These assets
are the template consumers copy. A re-run replaces the assets.

The files on `main` are this repository's own installation and run the `latest` image, so this
repository runs each release as soon as it is published and meets its errors before any consumer
does. The runners pull a job's image on every run, so `latest` is never a stale local copy.
Renovate leaves the image alone.

A change that needs consumers to update their copies of `mergeweasel-*.yml` is breaking (`!`),
because Renovate bumps only the image pin. Such a change keeps the workflow files working with the
previous release, because this repository runs the new files on the old image until the release
is published. The end-to-end test checks it: every scenario runs against both the new image and
the newest release, with what the workflow files pass. Consumers take files and pin together from
the release. This repository's Renovate types its updates so that only an update of the image's
base image releases (`fix(deps)`).

## Gitea behaviour relied on

Verified against the Gitea pinned in `e2e/gitea.Dockerfile`. The end-to-end test (`e2e/`)
exercises stale approvals, the rebase, the squash, the refs and the refused self-approval against
it, so a Gitea version that breaks one of them fails there first. Renovate bumps that pin for every
Gitea release. A new major version is a release whose notes say "support Gitea vX"; a minor or patch
version releases nothing, unless it needs a fix.

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
- **Workflow expressions**: `on:` takes none, so `branches: ['${{ vars.X }}']` matches nothing.
  `runs-on` evaluates `vars`, and `||` gives it a default when the variable is unset. A job-level
  `if:` that is false skips the job without a runner ever taking it. The end-to-end test has no
  runner, so these were checked on a Gitea v28 runner by hand.
- **Default branch**: `GET repos/{owner}/{repo}` reports `default_branch` to any account that can
  read the repository.
- **Labels**: `GET labels` lists only the repository's own labels; an organization's labels come
  from `GET orgs/{org}/labels`. Adding a label by name finds both.
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
  whose triggers all take the workflow - and with it the image - from `main`. The pass never
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
- **The Renovate account** gets its own updates approved, so its token is as sensitive as an
  approver token. The required checks remain the gate. It is never a default: on another instance an
  account of the same name could belong to anyone. Its name comes from an Actions variable, which
  only a repository or organization admin can set, so a pull request cannot make its own author
  trusted.
- **Carry-over**: trusted only from the merger. A forged status counts only in a dry run, which
  writes nothing.
