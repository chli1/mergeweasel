# Onboarding a repository

How to let mergeweasel land the pull requests of a Gitea repository. Every value named here is
listed in [configuration](configuration.md). `main` stands for the repository's default branch:
mergeweasel lands on whichever branch the repository's settings name as its default.

## Requirements

- Gitea with Actions enabled, and a runner that runs jobs in containers (`container:` in a job).
- The runner can pull the public image `docker.io/chli1ro/mergeweasel`.

## The accounts

mergeweasel acts through accounts of its own, in two roles:

- The **merger** rebases pull requests, records their approvals across a rebase, and merges them.
- The **approver accounts** restore the approvals that a rebase dismissed. To restore all of them,
  you need as many approver accounts as the number of required approvals.

One account can fill both roles: the merger can also be an approver account. An account used as an
approver must not open pull requests, because Gitea does not let anyone approve their own pull
request.

1. Create the accounts. Any names work; mergeweasel reads them from the tokens.
2. Give each account access to the repository, as listed in
   [permissions](#permissions): as a collaborator with *Write*, or through a team.
3. Create a token for each account with the scopes listed in [permissions](#permissions).
4. Store the merger's token as the repository secret `MERGEWEASEL_MERGER_TOKEN`, and the approver
   tokens, one per line, as `MERGEWEASEL_APPROVER_TOKENS`. If one account fills both roles, its
   token goes into both secrets.

Without approver tokens the queue restores no approvals: a pull request whose approvals a rebase
dismissed gets `needs-fix` and has to be approved again by hand.

Why not the workflow's run token: Gitea starts no workflow for pushes by the Actions bot, so the
checks would never run on a pull request the queue rebased.

### Permissions

A collaborator with *Write* has every repository permission below. A team grants them per unit:
set each unit as listed. An account that fills both roles needs both columns.

| Repository unit | Merger | Approver | What for |
|---|---|---|---|
| Code | write | read | the merger rebases pull requests and sets the carry-over status; both read commits |
| Pull requests | write | write | the merger merges and sets labels and comments; the approvers approve |
| Releases | write | - | the merger creates the release after a releasing merge |
| Issues | read | - | the merger checks that an `{issue}` reference in a title - `(#N)` by default - names an issue |
| Actions | read | - | the merger reads the queue's workflow runs, to run one pass at a time |

In a public repository everyone can read, so only the write permissions need granting. On `main`'s
branch protection, the merger must be in the merge whitelist, and the approvers in the approvals
whitelist if you use one ([branch protection](#branch-protection)).

| Token scope | Merger | Approver | What for |
|---|---|---|---|
| `write:repository` | yes | yes | everything on code, pull requests, releases and actions |
| `write:issue` | yes | - | labels and comments on pull requests |
| `read:user` | yes | yes | every pass reads each account's login at its start |

## Labels

Create the labels `automerge`, `needs-fix` and `keep-commits`, at organization or repository
level. [Usage](usage.md#the-labels) explains what they mean. `keep-commits` is optional: without
it, every pull request is squashed.

To use other names - because the organization already has labels for these roles, say - create
those instead and set the Actions variables `MERGEWEASEL_AUTOMERGE_LABEL`,
`MERGEWEASEL_NEEDS_FIX_LABEL` and `MERGEWEASEL_KEEP_COMMITS_LABEL` to their names
([instance settings](configuration.md#instance-settings)). Set them at organization level to name
the labels once for every repository. The pass fails with a message that names the label when
`automerge` or `needs-fix` is missing, and when `keep-commits` is missing although its variable
names it.

## Renovate

This step is only for repositories that Renovate updates. Set the Actions variable
`MERGEWEASEL_RENOVATE_ACCOUNT` to the login of the account Renovate runs as, for example
`renovate`. The queue then treats that account's pull requests specially:

- **Approval:** its own update gets approved by the approver accounts, so no human has to. That
  holds as long as only Renovate or the merger pushed to it, and Renovate added `automerge` last.
  Once anyone else pushes, or a human sets `automerge`, the update needs a human approval like any
  other pull request.
- **Retry:** a push by Renovate to an update labelled `needs-fix`, for example its rebase, removes
  the label, so a failed update is retried without anyone stepping in.

Without the variable, Renovate's pull requests are ordinary pull requests. The variable has no
default on purpose: whichever account it names gets its updates approved, so name only the account
Renovate really runs as, and keep its token as safe as an approver token.

## Configuration

This step is optional: mergeweasel works without any configuration. To change a rule for the
repository's commits or releases, add `mergeweasel.toml` to the root of the repository and merge it
to `main`. [Configuration](configuration.md#the-configuration-file) lists the keys.

## Releases

After each merge, the queue checks whether the merged commits call for a release: a commit of a
releasing type ([configuration](configuration.md#types-and-releases)), or a breaking change (`!`).
If so, it creates a tag on `main` and a Gitea release with notes. `release.tag` sets the version
format ([configuration](configuration.md#the-release-tag)):

- **SemVer**, `vX.Y.Z`, the default: mergeweasel counts up from the previous `vX.Y.Z` tag on
  `main`. To start, tag a commit on `main` by hand, for example `v0.1.0`. Until that tag exists,
  nothing is released.
- **CalVer**, for example `tag = "{YY}.{MM}.{DD}.{count}+{sha}"`: no first tag is needed; the first
  merge that releases creates it.

A repository that releases otherwise sets `release.enabled = false`
([configuration](configuration.md#switching-the-release-off)); the queue then only merges.

Tags on other branches are ignored. If you keep a maintenance branch for an older version, create
it only once `main` has moved past that version line - otherwise both branches can arrive at the
same version. The merger account creates the tag, so workflows that run on tag pushes start for
it.

## Merge styles

In the repository settings, allow squash and fast-forward-only merges. The queue squashes by
default and fast-forwards pull requests labelled `keep-commits`. Also turn on *Delete pull request
branch after merge by default*, so Gitea deletes the branch of every pull request the queue merges.

## Branch protection

Protect `main` with these settings:

- **Required status checks:** on, listing the checks of your CI plus
  `mergeweasel-commits / check (pull_request)`. Without required checks the queue merges nothing,
  because no status could tell a tested head from an untested one.
- **Required approvals:** any number, 0 included. If you use an approvals whitelist, it must include
  the approver accounts; without one, any approval by an account with write access counts.
- **Merge whitelist:** only the merger.
- **Block merge on outdated branch:** on.
- **Dismiss stale approvals:** recommended. The approver accounts restore the approvals a rebase
  dismissed.

## The workflows

Every mergeweasel release carries the two workflows as assets: `mergeweasel-queue.yml` and
`mergeweasel-commits.yml`, each already pinned to that release's image by version and digest. Take
them from the release whose image you are going to run - not from this repository's `main`
branch, whose files serve its own installation and can be ahead of the latest release. For
release `vX.Y.Z`, in your repository:

```sh
releases=<this repository's URL>/releases/download/vX.Y.Z
curl -fsSL -o .gitea/workflows/mergeweasel-queue.yml "$releases/mergeweasel-queue.yml"
curl -fsSL -o .gitea/workflows/mergeweasel-commits.yml "$releases/mergeweasel-commits.yml"
```

Leave them unchanged except for the `workflow_run` list in `mergeweasel-queue.yml`: it names the
workflows of your CI plus `mergeweasel-commits`. When one of them completes, the queue wakes up -
this is also how a label change reaches the queue.

Keep the file names and workflow names as they are: the pass finds its own runs and statuses by
them.

Nothing in the workflows names your Gitea's labels, accounts, runners or default branch: they read
the [instance settings](configuration.md#instance-settings) from Actions variables. The jobs run on
runners labelled `ubuntu-latest`; if yours carry another label, set the Actions variable
`MERGEWEASEL_RUNNER_LABEL` to it. Pull requests against a branch other than the default branch skip
both jobs, so stacked pull requests cost no runner time.

To update, let Renovate bump the image pin; that is all a minor or patch release needs. A major
release says in its notes which workflow change it requires, and its image may not work with the
old files, so do not land Renovate's pin bump for it on its own. Take both files from the new
release instead; they carry the new pin as well. Adapt the `workflow_run` list again and land
them. Renovate then closes its pull request, because the pin is already current.

## Server setting

By default Gitea appends a `Co-authored-by` trailer to every squash commit the queue makes.
mergeweasel works either way. To keep the commit messages exactly as written, set this in Gitea's
`app.ini`:

```ini
[repository.pull-request]
ADD_CO_COMMITTER_TRAILERS = false
```

## Verify

Run the workflow `mergeweasel-queue` by hand with `dry_run` checked. Its log starts with the
settings it found - base branch, labels and Renovate account - and then shows what the queue would
do. Nothing changes.

## Troubleshooting

- **The queue waits forever.** Check that `main` has required status checks, that every required
  check reports on the pull request, and that the pull request is approved. An approval does not
  wake the queue: a pull request approved after `automerge` was set waits for the next event.
  Remove `automerge` and set it again to wake the queue.
- **`needs-fix` comes back.** The comment names the reason. Checks count only on the up-to-date
  head of the pull request: fix them there, then remove `needs-fix`.
- **"merge deferred: Please try again later".** Gitea was still checking whether the pull request
  can be merged; the next pass retries.
- **"cannot read the account of MERGEWEASEL_MERGER_TOKEN"**, or of a token of
  `MERGEWEASEL_APPROVER_TOKENS`, named by its position. The message gives Gitea's reason: the token
  expired, was revoked, or lacks `read:user`. Create a new token with the scopes listed in
  [the accounts](#the-accounts).
- **"... belongs to ... again".** Two approver tokens belong to the same account, and Gitea counts
  only one approval per account. Use a token of another account.
- **The jobs never start.** No runner carries the label the workflows ask for. Set
  `MERGEWEASEL_RUNNER_LABEL` to a label your runners have.
- **The queue ignores a pull request.** Check its base: only pull requests against the default
  branch are queued. Check the label name against the `settings:` line of the log.
- **"labels missing in the repository and its organization: ...".** A label the settings name does
  not exist. Create it, or correct the variable.
- **"the label variables must name three different labels".** Two of the three label variables name
  the same label. Give each role a label of its own.
- **"release failed: ...".** The pass ran, but the release did not; the message gives Gitea's
  reason. The next pass tries again, so nothing is lost.
- **"release failed: user should have a permission to write to a repo".** The merger lacks write
  permission on the Releases unit - typically through a team that grants permissions per unit. Grant
  it as listed in [permissions](#permissions); the next pass releases.
- **"release failed: mergeweasel.toml: ...".** The file on `main` is invalid, and the message names
  the key. Fix it in a pull request - the queue still lands it.
- **A traceback, then "release failed: SomeError(...)".** A bug in mergeweasel's release step, not
  a problem of your setup. The pass still ran and landed what it could; report the bug with the
  traceback.
- **"too few approver accounts to restore the approvals".** Add approver accounts to
  `MERGEWEASEL_APPROVER_TOKENS` (and to the approvals whitelist, if you use one), or approve again
  by hand. Then remove `needs-fix`.
