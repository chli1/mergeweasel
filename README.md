# mergeweasel

No more weaseling around a broken main: mergeweasel takes your pull request from rebase and commit
check to merge and a fresh release.

mergeweasel is a merge queue for [Gitea](https://about.gitea.com/), shipped as a container image.
Label an approved pull request `automerge`, and mergeweasel takes it from there: it rebases the pull
request when `main` has moved on, waits for the required checks, and lands it - squashed by default,
fast-forwarded with `keep-commits` - one pull request at a time. A pull request it cannot land gets
the label `needs-fix` and a comment saying why.

The logic lives in the image; each repository carries only two small workflows that run it.

## How a pull request travels

Every merge, label change and finished check run wakes the queue for one pass. A pass
picks the next queued pull request and takes one step:

1. Behind `main`: rebase it onto `main`, and keep its approval - Gitea may dismiss it on the rebase.
2. Checks still running: wait for the next pass.
3. A required check failed: flag it `needs-fix` with the failed checks, and move on to the next one.
4. Green: approve it if the rebase cost it its approval, merge it, and remove `automerge`.

The details, and the Gitea behaviour they rest on: [docs/design.md](docs/design.md).

## Quick start

1. Create an account for the queue and a token for it -
   [the queue account](docs/onboarding.md#the-queue-account).
2. Store the token as the repository secret `MERGEWEASEL_TOKEN` and create the labels -
   [labels](docs/onboarding.md#labels).
3. Protect `main`: required checks, approvals, only the queue may merge -
   [branch protection](docs/onboarding.md#branch-protection).
4. Copy the two workflows - [the workflows](docs/onboarding.md#the-workflows).
5. Approve a pull request, then label it `automerge` - [usage](docs/usage.md).

## Works with Renovate

mergeweasel lands [Renovate](https://docs.renovatebot.com/)'s pull requests like any other: let
Renovate label them `automerge` (its `addLabels` option) instead of merging them itself, and approve
them as usual. When an update fails its checks and gets `needs-fix`, Renovate's next push to it -
its rebase, say - removes the label, so the update is retried without anyone stepping in.

## Status

mergeweasel is at 0.x: it lands this repository's own pull requests, but it is not ready for other
repositories yet. Version `1.0.0` marks it ready.

## Documentation

- [Onboarding](docs/onboarding.md) - set up a repository.
- [Usage](docs/usage.md) - for pull request authors: labels and commit messages.
- [Configuration](docs/configuration.md) - the command, its environment, the fixed values.
- [Design](docs/design.md) - how a pass works, the Gitea behaviour it relies on, security.
- [Contributing](CONTRIBUTING.md) - development and releases.

## License

[MIT](LICENSE)
