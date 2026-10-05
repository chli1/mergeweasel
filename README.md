# mergeweasel

No more weaseling around a broken main: mergeweasel takes your pull request from rebase and commit
check to merge and a fresh release.

mergeweasel is a merge queue for [Gitea](https://about.gitea.com/) that releases what it lands,
shipped as a container image.
Label an approved pull request `automerge`, and mergeweasel takes it from there: it rebases the pull
request when `main` has moved on, waits for the required checks, and lands it - squashed by default,
fast-forwarded with `keep-commits` - one pull request at a time. A pull request it cannot land gets
the label `needs-fix` and a comment saying why. After a merge it releases: a tag and release
notes, versioned from the commit types.

The logic lives in the image; each repository carries only two small workflows that run it.

## How a pull request travels

Every merge, label change and finished check run wakes the queue for one pass. A pass
picks the next queued pull request and takes one step:

1. Behind `main`: rebase it onto `main`, and keep its approval - Gitea may dismiss it on the rebase.
2. Checks still running: wait for the next pass.
3. A required check failed: flag it `needs-fix` with the failed checks, and move on to the next one.
4. Green: restore the approvals the rebase cost it, merge it, and remove `automerge`.
5. Merged: the next pass tags `main` with the next version and publishes release notes - for
   `feat`, `fix` and breaking changes.

The details, and the Gitea behaviour they rest on: [docs/design.md](docs/design.md).

## Quick start

1. Create the merger and approver accounts and their tokens -
   [the accounts](docs/onboarding.md#the-accounts).
2. Store the tokens as the repository secrets `MERGEWEASEL_MERGER_TOKEN` and
   `MERGEWEASEL_APPROVER_TOKENS` and create the labels - [labels](docs/onboarding.md#labels).
3. Protect `main`: required checks, approvals, only the merger may merge -
   [branch protection](docs/onboarding.md#branch-protection).
4. Copy the two workflows - [the workflows](docs/onboarding.md#the-workflows).
5. Approve a pull request, then label it `automerge` - [usage](docs/usage.md).

## Works with Renovate

mergeweasel lands [Renovate](https://docs.renovatebot.com/)'s pull requests like any other: let
Renovate label them `automerge` (its `addLabels` option) instead of merging them itself. With
approver accounts configured, they approve Renovate's own updates, so no human needs to; without
them, or once anyone else pushed to an update, it waits for a human approval. When an update fails
its checks and gets `needs-fix`, Renovate's next push to it - its rebase, say - removes the label,
so the update is retried without anyone stepping in. The image is on Docker Hub, where Renovate
finds its release dates: a `minimumReleaseAge` applies to mergeweasel like to any other image.

## Documentation

- [Onboarding](docs/onboarding.md) - set up a repository.
- [Usage](docs/usage.md) - for pull request authors: labels and commit messages.
- [Configuration](docs/configuration.md) - the command, its environment, the fixed values.
- [Design](docs/design.md) - how a pass works, the Gitea behaviour it relies on, security.
- [Contributing](CONTRIBUTING.md) - development and releases.

## License

[MIT](LICENSE)
