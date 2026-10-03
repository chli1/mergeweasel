# Onboarding a repository

How to let mergeweasel land the pull requests of a Gitea repository. The values named here are
listed in [configuration](configuration.md).

## Requirements

- Gitea with Actions enabled, and a runner that runs jobs in containers (`container:` in a job).
- The runner can pull the public image `ghcr.io/chli1/mergeweasel`.

## The queue account

The queue acts as an account of its own:

1. Create an account for the queue - any name; mergeweasel reads it from the token.
2. Give it write access to the repository.
3. Create a token for it with the scopes `write:repository`, `write:issue` and `read:user`. The
   pass reads its own login with `read:user` at its start.
4. Store the token as the repository secret `MERGEWEASEL_TOKEN`.

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
- **Required approvals**: at least 1, with an approvals whitelist that includes the queue account.
- **Merge whitelist**: only the queue account.
- **Block merge on outdated branch**: on.
- **Dismiss stale approvals**: recommended. The queue carries an approval over its own rebase.

## The workflows

Copy `mergeweasel-queue.yml` and `mergeweasel-commits.yml` from this repository's
[`.gitea/workflows/`](../.gitea/workflows/) into yours, unchanged, except:

- the `workflow_run` list in `mergeweasel-queue.yml`, which names the workflows of your CI plus
  `mergeweasel-commits` - their completion wakes the queue;
- the image tag, which pins an exact version.

The file and workflow names stay as they are: the pass finds its own runs and statuses by them.

## Server setting

With `ADD_CO_COMMITTER_TRAILERS` on, Gitea appends a `Co-authored-by` trailer to every squash the
queue makes. To keep the commit messages as written, set in Gitea's `app.ini`:

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
- **"cannot read the queue account from MERGEWEASEL_TOKEN".** The message carries Gitea's reason:
  an expired or revoked token, or one without `read:user` - create a new token with the scopes
  listed in [the queue account](#the-queue-account).
