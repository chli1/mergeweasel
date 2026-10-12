# mergeweasel

No more weaseling around a broken main: mergeweasel takes your pull request from rebase and commit
check to merge and a fresh release.

mergeweasel is a merge queue for [Gitea](https://about.gitea.com/) that releases what it lands. It
ships as a container image.

Label an approved pull request `automerge`, and mergeweasel takes it from there. It rebases the
pull request when `main` has moved on, waits for the required checks, and lands it, one pull
request at a time. By default it squashes the pull request into one commit, titled as Gitea
suggests it: the pull request's title with its number appended. With the label `keep-commits` it
lands the commits as they are, and with `prio` it goes ahead of every other pull request - for a
hotfix that cannot wait. A pull request it cannot land gets the label `needs-fix` and a
comment that explains why. With automatic releases switched on, it also releases what it lands: a
tag and release notes, with the version derived from the commit types.

The logic lives in the image; each repository carries only two small workflows that run it.

## How a pull request travels

Every merge, label change and finished check run wakes the queue for one pass. A pass picks the
next queued pull request - one labelled `prio` first - and takes one step:

1. **Behind `main`:** rebase it onto `main` and keep its approvals, which Gitea may dismiss on the
   rebase.
2. **Checks still running:** wait for the next pass.
3. **A required check failed:** flag it `needs-fix`, list the failed checks, and move on to the next
   pull request.
4. **All checks green:** restore the approvals the rebase cost it, merge it, and remove
   `automerge`.
5. **Merged:** with automatic releases switched on, the next pass tags `main` with the next version
   and publishes release notes, provided the merge contains a commit of a releasing type or a
   breaking change.

The details, and the Gitea behaviour they rest on, are in [docs/design.md](docs/design.md).

## Releases

Write [Conventional Commits](https://www.conventionalcommits.org/) and mergeweasel releases for you.
After each merge it works out the next version from the commit types, tags `main`, and publishes a
Gitea release with notes. No release workflow, no version bumps by hand.

- `feat` raises the minor version, `fix` the patch version, `!` the major version.
- `docs`, `chore`, `ci` and the other types release nothing.
- A breaking change carries its `## Commit` section into the notes, so users read what to change.

A pull request titled `feat(api): add a bulk endpoint` lands as
`feat(api): add a bulk endpoint (#80)`, takes `v1.2.3` to `v1.3.0`, and publishes:

```markdown
## Features

- api: add a bulk endpoint (#80)
```

Automatic releases are off until you switch them on, so they never collide with a release process
already in place. Add one line to `mergeweasel.toml` and tag a first version, such as `v0.1.0`:

```toml
[release]
enabled = true
```

Versions can be dates instead (CalVer), and which types release is configurable:
[releases](docs/onboarding.md#releases), [configuration](docs/configuration.md#the-release-tag).

## Quick start

1. Create the merger and approver accounts and their tokens:
   [the accounts](docs/onboarding.md#the-accounts).
2. Store the tokens as the repository secrets `MERGEWEASEL_MERGER_TOKEN` and
   `MERGEWEASEL_APPROVER_TOKENS`, and create the labels: [labels](docs/onboarding.md#labels). Your
   Gitea's own label names, runner label and Renovate account are Actions variables:
   [instance settings](docs/configuration.md#instance-settings).
3. Protect `main` with required checks and approvals, and allow only the merger to merge:
   [branch protection](docs/onboarding.md#branch-protection).
4. Copy the two workflows: [the workflows](docs/onboarding.md#the-workflows).
5. Approve a pull request, then label it `automerge`: [usage](docs/usage.md).
6. For automatic releases, switch them on: [releases](#releases).

## Works with Renovate

mergeweasel lands [Renovate](https://docs.renovatebot.com/)'s pull requests like any other. Let
Renovate label them `automerge` (its `addLabels` option) instead of merging them itself, and name
Renovate's account in the Actions variable `MERGEWEASEL_RENOVATE_ACCOUNT`
([Renovate](docs/onboarding.md#renovate)) for the two extras below.

- **Approvals:** with approver accounts configured, mergeweasel approves Renovate's own updates, so
  no human has to. Without approver accounts, or once someone else has pushed to an update, the
  update waits for a human approval.
- **Retries:** when an update fails its checks and gets `needs-fix`, Renovate's next push to it,
  for example its rebase, removes the label. The update is then retried without anyone stepping in.
- **Release age:** the image is on Docker Hub, where Renovate finds its release dates, so a
  `minimumReleaseAge` applies to mergeweasel like to any other image.

## Documentation

- [Onboarding](docs/onboarding.md): set up a repository.
- [Usage](docs/usage.md): for pull request authors - labels and commit messages.
- [Configuration](docs/configuration.md): the command, its environment, the instance settings,
  `mergeweasel.toml` and the fixed values.
- [Design](docs/design.md): how a pass works, the Gitea behaviour it relies on, and security.
- [Contributing](CONTRIBUTING.md): development and releases.

## License

[MIT](LICENSE)
