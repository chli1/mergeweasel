# Configuration

Everything mergeweasel needs to reach Gitea comes from the Gitea Actions environment. Settings come
from two places, split by one rule: a repository's commits must work unchanged on another Gitea.

- [Instance settings](#instance-settings) belong to the Gitea installation - the label names, the
  Renovate account, the runner label. They are Gitea Actions variables, set in the organization's or
  the repository's settings, never in a file of the repository.
- [`mergeweasel.toml`](#the-configuration-file) belongs to the repository's content - the rules for
  its commits and releases. It travels with the commits.

The [fixed values](#fixed-values) are the same everywhere.

Throughout these documents, `main` stands for the repository's default branch, and the labels go by
their default names.

## The command

```
mergeweasel pass [--dry-run]
mergeweasel check-commits --event <path>
```

- `pass` runs one queue pass, which releases `main` first. With `--dry-run` it decides and reports,
  but changes nothing.
- `check-commits` checks what a pull request would land on `main`. `<path>` is the `pull_request`
  event payload (`$GITHUB_EVENT_PATH` in a workflow).

Both commands exit with 0 when they succeed and with 1 when they fail. `pass` also exits with 1
when its release failed, after it has run the rest of the pass.

## Environment

| Variable | Set by | Used by |
|---|---|---|
| `GITHUB_SERVER_URL` | Gitea Actions | both commands: the Gitea server |
| `GITHUB_REPOSITORY` | Gitea Actions | both commands: `owner/repository` |
| `MERGEWEASEL_MERGER_TOKEN` | the workflow, from the secret | `pass`: required, except with `--dry-run`. The merger rebases, writes the carry-over and merges |
| `MERGEWEASEL_APPROVER_TOKENS` | the workflow, from the secret | `pass`: optional, one token per line. The approver accounts restore the approvals a rebase dismissed. Without it, `--dry-run` assumes there are enough |
| `GITHUB_TOKEN` | the workflow, from the run token | `check-commits`, and `pass --dry-run` without `MERGEWEASEL_MERGER_TOKEN`. The merger is then unknown, and any carry-over counts |
| `GITHUB_RUN_ID`, `GITHUB_EVENT_NAME` | Gitea Actions | `pass`: to run one pass at a time. Without `GITHUB_RUN_ID` the pass does not wait for others |
| `MERGEWEASEL_AUTOMERGE_LABEL`, `MERGEWEASEL_NEEDS_FIX_LABEL`, `MERGEWEASEL_KEEP_COMMITS_LABEL`, `MERGEWEASEL_RENOVATE_ACCOUNT` | the workflow, from the Actions variables | both commands: the [instance settings](#instance-settings) |

## Instance settings

Set these as Gitea Actions variables, under *Settings > Actions > Variables* of the organization or
of the repository. A repository's variable overrides the organization's variable of the same name,
so an organization can set its names once for all its repositories. The workflows pass the variables
to mergeweasel unchanged; an unset or empty variable keeps its default.

| Variable | Default | Meaning |
|---|---|---|
| `MERGEWEASEL_AUTOMERGE_LABEL` | `automerge` | the label that queues a pull request |
| `MERGEWEASEL_NEEDS_FIX_LABEL` | `needs-fix` | the label the queue sets on a pull request it cannot land |
| `MERGEWEASEL_KEEP_COMMITS_LABEL` | `keep-commits` | the label that lands the commits as they are instead of squashing them |
| `MERGEWEASEL_RENOVATE_ACCOUNT` | none: the Renovate rule is off | the account whose own updates the approver accounts approve, and whose push removes `needs-fix` - see [Renovate](onboarding.md#renovate) |
| `MERGEWEASEL_RUNNER_LABEL` | `ubuntu-latest` | the runner label both workflows run on; only the workflows read it |

The **base branch** is the repository's default branch, as set in its settings. The queue lands on
it, releases it, and reads its branch protection; pull requests against any other branch are left
alone, and the workflows skip them before a runner starts.

For example, an organization whose labels are called `merge-me`, `broken` and `no-squash`, whose
Renovate runs as `deps-bot` and whose runners carry the label `docker` sets these organization
variables:

| Variable | Value |
|---|---|
| `MERGEWEASEL_AUTOMERGE_LABEL` | `merge-me` |
| `MERGEWEASEL_NEEDS_FIX_LABEL` | `broken` |
| `MERGEWEASEL_KEEP_COMMITS_LABEL` | `no-squash` |
| `MERGEWEASEL_RENOVATE_ACCOUNT` | `deps-bot` |
| `MERGEWEASEL_RUNNER_LABEL` | `docker` |

Every run of either command prints the settings it works with near the start of its log, for
example:

```
settings: base branch main; labels merge-me, broken, no-squash; Renovate account deps-bot
```

Both commands fail when two label variables name the same label. The pass also fails, before the
release, when a required label exists neither in the repository nor in its organization; the
message names the missing labels. `automerge` and `needs-fix` are always required: without them the
queue can neither find nor flag a pull request. `keep-commits` is optional - without it every pull
request is squashed - unless `MERGEWEASEL_KEEP_COMMITS_LABEL` names a label, which then has to
exist. When the pass cannot read the organization's labels - in a repository owned by a user, or in
a dry run with the run token - it prints that it did not check, and goes on.

## The configuration file

An optional file named `mergeweasel.toml` in the root of the repository.

Which version of the file mergeweasel reads depends on what it is doing:

- **The release** reads the version on `main`. A change to `release.tag` therefore takes effect with
  the first release after its pull request is merged.
- **The commit check** reads the version on the pull request's branch. This covers both
  `check-commits` and the check the queue runs right before it merges. So a pull request that
  changes the file is already checked against its own change: one that adds a scope to
  `commits.scopes` may use that scope in its own title.

Because the commit check reads the branch's version, a pull request that breaks the file - with a
typo in a key, say - fails its own check, and the queue flags it `needs-fix` instead of merging it.
An invalid file therefore stays on its branch and never holds up the other pull requests.

Every key is optional; a missing key keeps its default. An unknown key or an invalid value is an
error, and the message names the key.

| Key | Default | Meaning |
|---|---|---|
| `release.tag` | `"v{major}.{minor}.{patch}"` | how versions look - see [the release tag](#the-release-tag) |
| `commits.scopes` | any scope | the scopes a subject may use - see [scopes](#scopes) |

### Scopes

The scope is the optional part in parentheses after the commit type: `ui` in
`feat(ui): add dark mode (#12)`. By default any scope is accepted, as long as it is not empty and
contains no whitespace. That also means a typo goes through unnoticed: `feat(domian): ...` passes
just like `feat(domain): ...`.

A repository with a fixed set of scopes - its modules, for example - can list them in
`commits.scopes`:

```toml
[commits]
scopes = ["domain", "ui", "deps"]
```

With this list, a subject may only use one of these scopes. Any other scope fails `check-commits`,
and the queue flags the pull request `needs-fix` instead of merging it. The message names the
unknown scope and lists the allowed ones:

```
PR title: unknown scope 'domian', expected one of: domain, ui, deps
```

The list restricts only subjects that have a scope. A subject without one, like
`feat: add dark mode (#12)`, is always accepted.

There are three cases to tell apart:

| `mergeweasel.toml` | Effect |
|---|---|
| no `commits.scopes` key | any scope is accepted - the default |
| `scopes = ["domain", "ui"]` | only `domain` and `ui` are accepted |
| `scopes = []` | no scope is accepted; every subject must do without one |

Each entry must itself be a valid scope: a non-empty string without whitespace and without `)`.
Otherwise the file is invalid, and the message names the entry.

**Renovate.** Renovate writes its subjects with the scope `deps`, for example
`fix(deps): update dependency pytest to v8.4.2`. A repository that uses Renovate and sets
`commits.scopes` has to include `deps` in the list, or every Renovate pull request is flagged
`needs-fix`.

### The release tag

`release.tag` is a template that decides how the version of each release looks. mergeweasel
creates the version as a git tag and names the Gitea release after it.

```toml
[release]
tag = "{YY}.{MM}.{count}"
```

| Template | Version |
|---|---|
| `v{major}.{minor}.{patch}` (the default) | `v1.4.2` |
| `{YY}.{MM}.{count}` | `26.10.412` |
| `{YYYY}.{MM}.{DD}` | `2026.10.5` - allows one release a day |
| `{YY}.{MM}.{DD}.{count}+{sha}` | `26.10.5.412+abcdef0` |
| `{major}.{minor}.{patch}+{YYYY}{0M}{0D}{0H}{0m}` | `1.4.2+202610051432` |

#### SemVer and CalVer

A template that contains `{major}`, `{minor}` and `{patch}` is a **SemVer template**. mergeweasel
increases the previous version according to the commit types: a breaking change (`!`) raises the
major version, `feat` the minor version, and `fix`, `perf` and `revert` the patch version. A new
repository therefore needs a first tag, set by hand on a commit of `main`, for example `v0.1.0`.
Until that tag exists, nothing is released.

A template without these three placeholders is a **CalVer template**: it consists of date, time,
`{count}` and `{sha}`. The commit types still decide whether a merge releases, but not the
version. No first tag is needed: the first merge with a releasing commit creates the first
release, and its notes list only that merge's commit.

The two can be mixed: a SemVer template may also contain date, time, `{count}` and `{sha}`, for
example as build metadata after a `+`, like the last row of the table. It still counts as a SemVer
template - the version numbers increase as above, and the first tag is set by hand.

mergeweasel finds the previous release by its tag: the nearest commit in `main`'s history with a
tag that matches the template, searched at most 1000 commits back. Tags in any other format are
ignored. After switching from SemVer to CalVer, the CalVer start applies; after switching to
SemVer, set the first tag by hand again.

#### Placeholders

| Placeholder | Value |
|---|---|
| `{major}` `{minor}` `{patch}` | the version numbers, increased from the previous release |
| `{YYYY}` | year: 2026 |
| `{YY}` `{0Y}` | year since 2000: 26 (6 in 2006); zero-padded 26 (06) |
| `{MM}` `{0M}` | month: 5; zero-padded 05 |
| `{DD}` `{0D}` | day: 3; zero-padded 03 |
| `{HH}` `{0H}` | hour: 7; zero-padded 07 |
| `{mm}` `{0m}` | minute: 4; zero-padded 04 |
| `{count}` | the number of commits on `main` up to and including the released one |
| `{sha}` | the first 7 characters of the released commit's hash |

- Date and time are in UTC, taken at the moment of the release.
- The date placeholders follow [calver.org](https://calver.org). Note the case: `{MM}` is the
  month, `{mm}` the minute, as in Unicode date patterns.
- Tools that parse versions, such as SemVer and PEP 440 tools, reject or rewrite leading zeros.
  Use the unpadded placeholders where a tool reads that part of your versions.

#### Rules

A template is valid when

- it contains at least one placeholder, and each placeholder at most once;
- it contains all of `{major}`, `{minor}` and `{patch}`, or none of them;
- every placeholder whose value varies in length - all except `{YYYY}`, `{0M}`, `{0D}`, `{0H}`,
  `{0m}` and `{sha}` - is followed by a character other than a digit, or ends the template.
  Otherwise the version could not be read back from the tag: `v{major}1{minor}.{patch}` would
  write 11.0.0 and 1.10.0 alike. The fixed-length placeholders may be followed by anything, so
  `{YYYY}{0M}{0D}` works;
- the result is a valid git tag name: no spaces and none of `~ ^ : ? * [ \`.

An invalid template fails the release, and the message names the problem. The queue keeps
merging.

#### Things to keep in mind

- **Unique versions.** A release whose version exists already fails. `{YYYY}.{MM}.{DD}` allows one
  release a day; add `{count}` or `{sha}` to give every merge its own version.
- **Docker tags.** Git tags allow `+`, Docker tags do not. A workflow that publishes an image
  tagged with the version has to replace the `+`, for example with `-`.

## Image

`docker.io/chli1ro/mergeweasel`, tagged `X.Y.Z` and `latest`. Pin `X.Y.Z`.

## Fixed values

| What | Value |
|---|---|
| Releasing types | `!` major; `feat` minor; `fix`, `perf`, `revert`, `Revert "..."` patch |
| Commit types | `feat`, `fix`, `perf`, `refactor`, `style`, `docs`, `test`, `build`, `ci`, `chore`, `revert` (the list of `@commitlint/config-conventional`) |
| Subject | `<type>[(<scope>)][!]: <description> (#<issue>)` or `... (PR #<pr>)` |
| `## Commit` lines | at most 72 characters, except for lines without whitespace |
| Workflows | `mergeweasel-queue.yml` (workflow `mergeweasel-queue`), `mergeweasel-commits.yml` (workflow `mergeweasel-commits`) |
| Carry-over status | context `mergeweasel/approval`; description `approved at <old head>`, then one line `approved by <login>` per reviewer |
| API calls | 30 s timeout; reading a rebased head and writing the carry-over are tried 3 times, 5 s apart |
