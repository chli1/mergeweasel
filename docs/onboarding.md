# Onboarding a repository

How to let mergeweasel land the pull requests of a Gitea repository. Every value named here is
listed in [configuration](configuration.md).

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
2. Give each account write access to the repository.
3. Create a token for each account:
   - the merger's token with the scopes `write:repository`, `write:issue` and `read:user`;
   - an approver's token with the scopes `write:repository` and `read:user`.

   Every pass reads each account's login at its start, which needs `read:user`.
4. Store the merger's token as the repository secret `MERGEWEASEL_MERGER_TOKEN`, and the approver
   tokens, one per line, as `MERGEWEASEL_APPROVER_TOKENS`. If one account fills both roles, its
   token goes into both secrets.

Without approver tokens the queue restores no approvals: a pull request whose approvals a rebase
dismissed gets `needs-fix` and has to be approved again by hand.

Why not the workflow's run token: Gitea starts no workflow for pushes by the Actions bot, so the
checks would never run on a pull request the queue rebased.

## Labels

Create the labels `automerge`, `needs-fix` and `keep-commits`, at organization or repository
level. [Usage](usage.md#the-labels) explains what they mean.

## Configuration

This step is optional: mergeweasel works without any configuration. To change a default, add
`mergeweasel.toml` to the root of the repository and merge it to `main`.
[Configuration](configuration.md#the-configuration-file) lists the keys.

## Releases

After each merge, the queue checks whether the merged commits call for a release: a `feat`, `fix`,
`perf` or `revert` commit, or a breaking change (`!`). If so, it creates a tag on `main` and a
Gitea release with notes. `release.tag` sets the version format
([configuration](configuration.md#the-release-tag)):

- **SemVer**, `vX.Y.Z`, the default: mergeweasel counts up from the previous `vX.Y.Z` tag on
  `main`. To start, tag a commit on `main` by hand, for example `v0.1.0`. Until that tag exists,
  nothing is released.
- **CalVer**, for example `tag = "{YY}.{MM}.{DD}.{count}+{sha}"`: no first tag is needed; the first
  merge that releases creates it.

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

Copy `mergeweasel-queue.yml` and `mergeweasel-commits.yml` from this repository's
[`.gitea/workflows/`](../.gitea/workflows/) into yours. Leave them unchanged except for:

- the `workflow_run` list in `mergeweasel-queue.yml`: it names the workflows of your CI plus
  `mergeweasel-commits`. When one of them completes, the queue wakes up - this is also how a label
  change reaches the queue;
- the image tag, which pins an exact version.

Keep the file names and workflow names as they are: the pass finds its own runs and statuses by
them.

## Server setting

By default Gitea appends a `Co-authored-by` trailer to every squash commit the queue makes.
mergeweasel works either way. To keep the commit messages exactly as written, set this in Gitea's
`app.ini`:

```ini
[repository.pull-request]
ADD_CO_COMMITTER_TRAILERS = false
```

## Verify

Run the workflow `mergeweasel-queue` by hand with `dry_run` checked. Its log shows what the queue
would do, and nothing changes.

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
- **"release failed: ...".** The pass ran, but the release did not; the message gives Gitea's
  reason. The next pass tries again, so nothing is lost.
- **"release failed: mergeweasel.toml: ...".** The file on `main` is invalid, and the message names
  the key. Fix it in a pull request - the queue still lands it.
- **"too few approver accounts to restore the approvals".** Add approver accounts to
  `MERGEWEASEL_APPROVER_TOKENS` (and to the approvals whitelist, if you use one), or approve again
  by hand. Then remove `needs-fix`.
