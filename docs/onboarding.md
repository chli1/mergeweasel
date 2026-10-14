# Onboarding a repository

How to let mergeweasel land the pull requests of a Gitea repository. Every value named here is
listed in [configuration](configuration.md). `main` stands for the repository's default branch:
mergeweasel lands on whichever branch the repository's settings name as its default.

## Requirements

- Gitea with Actions enabled, and a runner that runs jobs in containers (`container:` in a job).
  Forgejo works too, as experimental support: see [Forgejo](#forgejo-experimental) for what
  differs.
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
dismissed gets `needs-fix` and has to be approved again by hand. Why the queue needs accounts of
its own instead of the workflow's run token: [design](design.md#gitea-behaviour-relied-on).

### Permissions

A collaborator with *Write* has every repository permission below. A team grants them per unit:
set each unit as listed. An account that fills both roles needs both columns.

| Repository unit | Merger | Approver | What for |
|---|---|---|---|
| Code | write | read | the merger rebases pull requests and sets the carry-over status; both read commits |
| Pull requests | write | write | the merger merges and sets labels and comments; the approvers approve |
| Releases | write | - | the merger creates the release after a releasing merge |
| Issues | read | - | the merger checks that an `{issue}` or `{pull_or_issue}` reference in a subject names an issue; the default forms have none |
| Actions | read | - | the merger reads the queue's workflow runs, to run one pass at a time |

In a public repository everyone can read, so only the write permissions need granting. On `main`'s
branch protection, the merger must be in the merge whitelist, and the approvers in the approvals
whitelist if you use one ([branch protection](#branch-protection)).

| Token scope | Merger | Approver | What for |
|---|---|---|---|
| `write:repository` | yes | yes | everything on code, pull requests, releases and actions |
| `write:issue` | yes | - | labels and comments on pull requests |
| `read:user` | yes | yes | every pass reads each account's login at its start |
| `read:organization` | yes | - | the merger checks that the labels exist, when they are the organization's |

## Labels

Create the labels `automerge`, `needs-fix`, `keep-commits` and `prio`, at organization or
repository level. [Usage](usage.md#the-labels) explains what they mean; `keep-commits` and `prio`
are optional.

To use other names - because the organization already has labels for these roles, say - create
those instead and set the Actions variables `MERGEWEASEL_AUTOMERGE_LABEL`,
`MERGEWEASEL_NEEDS_FIX_LABEL`, `MERGEWEASEL_KEEP_COMMITS_LABEL` and `MERGEWEASEL_PRIO_LABEL` to
their names ([Actions variables](configuration.md#actions-variables)). Set them at organization
level to name the labels once for every repository. The same section lists which labels must
exist.

## Renovate

This step is only for repositories that Renovate updates.

1. Let Renovate label its pull requests `automerge` (its `addLabels` option) instead of merging
   them itself.
2. Set the Actions variable `MERGEWEASEL_RENOVATE_ACCOUNT` to the login of the account Renovate
   runs as, for example `renovate`. Name only the account Renovate really runs as: the approver
   accounts approve its updates, so whoever holds that account's token - in Renovate's own setup -
   gets changes approved, as with an approver token.
3. If the repository restricts `commits.scopes` or `commits.types`, allow what Renovate writes.

[Renovate](configuration.md#renovate) explains what the queue does with Renovate's pull requests,
and what each of these steps is for.

## Configuration

mergeweasel merges without any configuration; the release needs one line, see
[releases](#releases). To change a rule for the repository's commits or releases, add
`mergeweasel.toml` to the root of the repository and merge it to `main`.
[Configuration](configuration.md#the-configuration-file) lists the keys.

## Releases

The release is off until the repository switches it on in its `mergeweasel.toml`:

```toml
[release]
enabled = true
```

Then, with the default SemVer versions, tag a commit on `main` by hand as the first version, for
example `v0.1.0`; mergeweasel counts up from there. With a CalVer template set in `release.tag`, no
first tag is needed. [Switching the release on](configuration.md#switching-the-release-on) covers
both, and [types and releases](configuration.md#types-and-releases) which commits release.

If you keep a maintenance branch for an older version, create it only once `main` has moved past
that version line - otherwise both branches can arrive at the same version. The merger account
creates the tag, so workflows that run on tag pushes start for it.

## Merge styles

In the repository settings, allow squash and fast-forward-only merges. The queue squashes by
default and fast-forwards pull requests labelled `keep-commits`. Also turn on *Delete pull request
branch after merge by default*, so Gitea deletes the branch of every pull request the queue merges.

## The workflows

Every mergeweasel release carries the workflows as assets, each already pinned to that release's
image by version and digest: the queue as `mergeweasel-queue-gitea.yml` and
`mergeweasel-queue-forgejo.yml`, one per forge, and the commit check as `mergeweasel-commits.yml`,
which both forges share. Take them from the release whose image you are going to run - not from
this repository's `main` branch, whose files serve its own installation and can be ahead of the
latest release. For release `vX.Y.Z`, in your repository on Gitea:

```sh
releases=<this repository's URL>/releases/download/vX.Y.Z
curl -fsSL -o .gitea/workflows/mergeweasel-queue.yml "$releases/mergeweasel-queue-gitea.yml"
curl -fsSL -o .gitea/workflows/mergeweasel-commits.yml "$releases/mergeweasel-commits.yml"
```

On Forgejo, take Forgejo's queue instead, into Forgejo's directory - see
[Forgejo](#forgejo-experimental).

Leave them unchanged except for the `workflow_run` list in `mergeweasel-queue.yml`: it names the
workflows of your CI plus `mergeweasel-commits`. When one of them completes, the queue wakes up -
this is also how a label change reaches the queue.

Keep the file names and workflow names as they are: the pass finds its own runs and statuses by
them.

Land the two files with a pull request and merge it by hand: the queue runs from `main`, so it is
not there yet to merge the pull request that brings it. The `mergeweasel-commits` check already
runs on that pull request.

Nothing in the workflows names your Gitea's labels, accounts, runners or default branch: they read
them from [Actions variables](configuration.md#actions-variables). The jobs run on runners labelled
`ubuntu-latest`; if yours carry another label, set the Actions variable `MERGEWEASEL_RUNNER_LABEL`
to it.

To update, let Renovate bump the image pin; that is all a minor or patch release needs. A major
release says in its notes which workflow change it requires, and its image may not work with the
old files, so do not land Renovate's pin bump for it on its own. Take both files from the new
release instead; they carry the new pin as well. Adapt the `workflow_run` list again and land
them. Renovate then closes its pull request, because the pin is already current.

## Branch protection

Protect `main` once the workflows are on it, with these settings:

- **Required status checks:** on, listing the checks of your CI plus
  `mergeweasel-commits / check (pull_request)`. Without required checks the queue merges nothing
  ([why](design.md#definitions)).
- **Required approvals:** any number, 0 included. If you use an approvals whitelist, it must include
  the approver accounts; without one, any approval by an account with write access counts.
- **Merge whitelist:** only the merger.
- **Block merge on outdated branch:** on.
- **Dismiss stale approvals:** recommended. The approver accounts restore the approvals a rebase
  dismissed.

If `main` is protected already, its merge whitelist keeps you from merging the pull request that
adds the workflows. Lift the protection for that one merge: in the branch protection rule, change
the protected branch name pattern to one that matches no branch, merge the pull request, then set
the pattern back to `main`.

## Server setting

By default Gitea appends a `Co-authored-by` trailer to every squash commit the queue makes.
mergeweasel works either way. To keep the commit messages exactly as written, set this in Gitea's
`app.ini`:

```ini
[repository.pull-request]
ADD_CO_COMMITTER_TRAILERS = false
```

## Forgejo (experimental)

mergeweasel runs on Forgejo as well. Forgejo serves the same API and runs the same workflows, so
everything above applies, with these differences:

- **The workflow files.** Take Forgejo's queue from the release, and put both files into
  `.forgejo/workflows/`:

  ```sh
  releases=<this repository's URL>/releases/download/vX.Y.Z
  curl -fsSL -o .forgejo/workflows/mergeweasel-queue.yml "$releases/mergeweasel-queue-forgejo.yml"
  curl -fsSL -o .forgejo/workflows/mergeweasel-commits.yml "$releases/mergeweasel-commits.yml"
  ```

  Forgejo reads `.gitea/workflows/` only when a repository has no `.forgejo/workflows/`, so a
  repository whose CI is in `.forgejo/workflows/` would never run files in `.gitea/workflows/`.
  The files go in unchanged: Forgejo's queue has no `workflow_run` list to adapt.
- **The queue polls.** Forgejo has no `workflow_run`, the trigger that wakes the queue on Gitea when
  a check finishes. Forgejo's queue carries a schedule instead, every 5 minutes. A merge still wakes
  the next pass at once; only a finished check waits for the schedule. So an approved pull request
  whose checks have passed merges up to one interval after its last check or its label. Forgejo
  starts a schedule at most about once a minute, whatever the cron line says. Each interval adds
  one run of `mergeweasel-queue` to the Actions history, most of them passes of a few seconds that
  find nothing to do.
- **Teams grant the lowest unit.** On Forgejo, a team with custom unit access gets as its
  repository permission the lowest unit it does not set to *No access*: a single unit on *Read*
  makes the whole team a reader, and a reader cannot be put into the merge or approvals whitelist.
  *No access* on a unit also holds on public repositories - with Actions on *No access*, the
  merger cannot list the queue's runs. So the per-unit table under
  [permissions](#permissions) does not work as it stands: give a team for mergeweasel's accounts
  *Write* on code, pull requests, issues, releases and actions, or add the accounts as
  collaborators with *Write*.
- **No co-author trailer.** Forgejo appends no `Co-authored-by` trailer to squash commits, so the
  [server setting](#server-setting) is not needed.

To update, the same holds as on Gitea: Renovate bumps the image pin, and for a major release you
take both files from the new release. Which Forgejo and runner versions mergeweasel is tested with:
[design](design.md#forgejo-behaviour-relied-on).

## Verify

Run the workflow `mergeweasel-queue` by hand with `dry_run` checked. Its log starts with the
`settings:` line ([Actions variables](configuration.md#actions-variables)) and then shows what the
queue would do. Nothing changes. If the run fails, or a later pass does not do what you expect, see
[troubleshooting](troubleshooting.md).
