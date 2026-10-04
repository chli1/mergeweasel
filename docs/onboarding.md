# Onboarding a repository

How to let mergeweasel land the pull requests of a Gitea repository. The values named here are
listed in [configuration](configuration.md).

## Requirements

- Gitea with Actions enabled, and a runner that runs jobs in containers (`container:` in a job).
- The runner can pull the public image `ghcr.io/chli1/mergeweasel`.

## The accounts

mergeweasel acts through accounts of its own, in two roles:

- the **merger** rebases, records the approvals across a rebase and merges;
- the **approver accounts** restore the approvals a rebase dismissed - as many as the required
  approvals, for the queue to restore them all.

The merger can be one of the approver accounts; one account then fills both roles. An account used
as approver must not open pull requests: Gitea refuses approving one's own pull request.

1. Create the accounts - any names; mergeweasel reads them from the tokens.
2. Give each write access to the repository.
3. Create a token for each: the merger's with the scopes `write:repository`, `write:issue` and
   `read:user`, an approver's with `write:repository` and `read:user`. Every pass reads each login
   with `read:user` at its start.
4. Store the merger's token as the repository secret `MERGEWEASEL_MERGER_TOKEN`, and the approver
   tokens, one per line, as `MERGEWEASEL_APPROVER_TOKENS` - for one account in both roles, its
   token in both.

Without approver tokens the queue restores nothing: a pull request whose approvals a rebase
dismissed gets `needs-fix` and has to be approved again by hand.

Why not the run token: Gitea starts no workflow for pushes by the Actions bot, so the checks would
never run on a pull request the queue rebased.

## Labels

Create `automerge`, `needs-fix` and `keep-commits`, at organization or repository level. Their
meaning: [usage](usage.md#the-labels).

## Merge styles

In the repository settings, allow squash and fast-forward-only merges. The queue uses squash by
default and fast-forward-only for `keep-commits`.

## Branch protection

Protect `main` with these settings:

- **Required status checks**: on, listing the contexts of your CI plus
  `mergeweasel-commits / check (pull_request)`. Without required checks the queue merges nothing -
  no status could tell a tested head from an untested one.
- **Required approvals**: any number, 0 included. With an approvals whitelist, it must include the
  approver accounts; without one, any approval by an account with write access counts.
- **Merge whitelist**: only the merger.
- **Block merge on outdated branch**: on.
- **Dismiss stale approvals**: recommended. The approver accounts restore the approvals a rebase
  dismissed.

## The workflows

Copy `mergeweasel-queue.yml` and `mergeweasel-commits.yml` from this repository's
[`.gitea/workflows/`](../.gitea/workflows/) into yours, unchanged, except:

- the `workflow_run` list in `mergeweasel-queue.yml`, which names the workflows of your CI plus
  `mergeweasel-commits` - their completion wakes the queue;
- the image tag, which pins an exact version.

The file and workflow names stay as they are: the pass finds its own runs and statuses by them.

## Server setting

By default Gitea appends a `Co-authored-by` trailer to every squash the queue makes. mergeweasel
works either way; to keep the commit messages exactly as written, set in Gitea's `app.ini`:

```ini
[repository.pull-request]
ADD_CO_COMMITTER_TRAILERS = false
```

## Verify

Run the workflow `mergeweasel-queue` by hand with `dry_run` checked. Its log shows the decision the
queue would take, and nothing changes.

## Troubleshooting

- **The queue waits forever.** Check that `main` has required status checks, that every required
  check reports on the pull request, and that the pull request is approved. An approval wakes
  nothing: approved after `automerge` was set, the pull request waits for the next event - remove
  and set `automerge` again to wake the queue.
- **`needs-fix` comes back.** The comment names the reason. Checks are judged on the up-to-date
  head only: fix them there, then remove `needs-fix`.
- **"merge deferred: Please try again later".** Gitea's mergeable check was still running; the next
  pass retries.
- **"cannot read the account of MERGEWEASEL_MERGER_TOKEN"** (or of a token of
  `MERGEWEASEL_APPROVER_TOKENS`, named by its position). The message carries Gitea's reason: an
  expired or revoked token, or one without `read:user` - create a new token with the scopes listed
  in [the accounts](#the-accounts).
- **"... belongs to ... again".** Two approver tokens of one account; Gitea counts one approval per
  account. Use a token of another account.
- **"too few approver accounts to restore the approvals".** Add approver accounts to
  `MERGEWEASEL_APPROVER_TOKENS` (and to the approvals whitelist, if set), or approve again by hand;
  then remove `needs-fix`.
