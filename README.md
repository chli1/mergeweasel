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

## Features

- **One pull request at a time, always on top of `main`.** The queue rebases a pull request that
  is behind, waits for the required checks, and merges only a tested head.
- **Approvals survive the rebase.** Gitea may dismiss approvals when the queue rebases; approver
  accounts restore them, so nobody has to approve twice.
- **Clean history.** The pull request's title and the `## Commit` section of its description
  become the squash commit. A check on every pull request rejects a message that would not fit
  before it reaches the queue.
- **Labels for the special cases.** `keep-commits` lands the commits as they are, `prio` puts a
  hotfix first, and `needs-fix` with a comment tells the author what went wrong.
- **Releases.** Tags and release notes from the commit types, as SemVer or CalVer versions.
- **Your conventions.** One file, `mergeweasel.toml`, sets the allowed scopes and types, subject
  forms with issue numbers or Jira keys - or no Conventional Commits at all - and the line length.
- **Your Gitea's names.** Label names and the runner label are Actions variables, so the
  repository's files work unchanged on any Gitea.
- **Renovate.** mergeweasel approves Renovate's own updates and retries a failed one after
  Renovate's next rebase.
- **A dry run** shows what the queue would do, without changing anything.

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

A pull request titled `feat(api): add a bulk endpoint` lands as
`feat(api): add a bulk endpoint (#80)`, takes `v1.2.3` to `v1.3.0`, and publishes:

```markdown
## Features

- api: add a bulk endpoint (#80)
```

Which types release, and how far, is configurable, and versions can be dates instead (CalVer):
[types and releases](docs/configuration.md#types-and-releases),
[the release tag](docs/configuration.md#the-release-tag). Automatic releases are off until you
switch them on, so they never collide with a release process already in place.

## Quick start

The shortest way to try mergeweasel on a Gitea repository with Actions enabled. One account does
all of mergeweasel's work here; [onboarding](docs/onboarding.md) covers the full setup.

1. **An account.** Create a Gitea account for mergeweasel, for example `mergeweasel`, that will
   never open pull requests itself. Add it to the repository as a collaborator with *Write*, and
   create a token for it with the scopes `write:repository`, `write:issue` and `read:user`.
2. **Secrets.** Store that token in the repository as both secrets `MERGEWEASEL_MERGER_TOKEN` and
   `MERGEWEASEL_APPROVER_TOKENS`: the account merges, and restores approvals after a rebase. For
   finer-grained permissions, give the two roles distinct accounts
   ([the accounts](docs/onboarding.md#the-accounts)).
3. **Labels.** Create the labels `automerge` and `needs-fix` in the repository.
4. **Merge settings.** In the repository settings, allow squash merges.
5. **Workflows.** Take the two workflows from the latest release `vX.Y.Z`:

   ```sh
   releases=https://github.com/chli1/mergeweasel/releases/download/vX.Y.Z
   curl -fsSL -o .gitea/workflows/mergeweasel-queue.yml "$releases/mergeweasel-queue.yml"
   curl -fsSL -o .gitea/workflows/mergeweasel-commits.yml "$releases/mergeweasel-commits.yml"
   ```

   In `mergeweasel-queue.yml`, list the workflows of your CI under `workflow_run`, next to
   `mergeweasel-commits`, and commit both files to `main`.
6. **Branch protection.** Protect `main`: require status checks - your CI's checks plus
   `mergeweasel-commits / check (pull_request)` - allow only `mergeweasel` to merge, and turn on
   *Block merge on outdated branch*.
7. **Try it.** Open a pull request titled `feat: try mergeweasel`. If `main` requires approvals,
   have someone other than its author approve it. Then label it `automerge` - last, because an
   approval does not wake the queue. Once its checks pass, mergeweasel merges it as
   `feat: try mergeweasel (#<number>)`.

To have it release as well, add `mergeweasel.toml` to the repository and tag a commit on `main`
with the version to count up from, for example `v0.1.0`:

```toml
[release]
enabled = true
```

If something does not work, see [troubleshooting](docs/troubleshooting.md).

## Documentation

- [Onboarding](docs/onboarding.md): set up a repository, step by step.
- [Usage](docs/usage.md): for pull request authors - labels and commit messages.
- [Configuration](docs/configuration.md): every variable, setting and `mergeweasel.toml` key.
- [Troubleshooting](docs/troubleshooting.md): what a message means and how to fix its cause.
- [Design](docs/design.md): how a pass works, the Gitea behaviour it relies on, and security.
- [Contributing](CONTRIBUTING.md): development and releases.

## Source and contact

The source is public at [github.com/chli1/mergeweasel](https://github.com/chli1/mergeweasel), a
read-only mirror of the repository mergeweasel is developed in. It receives `main` and every
release tag, and its [releases](https://github.com/chli1/mergeweasel/releases) carry the release
notes and the two workflow files. The mirror takes no pull requests and has no issue tracker.

For bug reports, questions and ideas, open a thread in the mirror's
[Discussions](https://github.com/chli1/mergeweasel/discussions).

Commit subjects and release notes end in `(#N)`: the issue or pull request of the development
repository the change came from. On GitHub, `#N` links to GitHub's own numbering instead, which
belongs to the Discussions and has nothing to do with the change.

## License

[MIT](LICENSE)
