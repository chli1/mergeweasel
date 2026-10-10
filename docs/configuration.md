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
  event payload (`$GITHUB_EVENT_PATH` in a workflow). Only the pull request's number is taken from
  it; the title, body, labels and head are read from Gitea when the check runs.

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
| `commits.subject` | `(#<issue>)` or `(PR #<pr>)` at the end | the forms a subject may take - see [the subject](#the-subject) |
| `commits.projects` | any project | the projects a `{key}` may name - see [the subject](#the-subject) |
| `commits.types` | the types of `@commitlint/config-conventional` | the types a subject may use - see [types and releases](#types-and-releases) |
| `commits.max_line` | `72` | the longest line of the `## Commit` section - see [the line length](#the-line-length) |
| `release.minor_types` | `["feat"]` | the types that release a minor version - see [types and releases](#types-and-releases) |
| `release.patch_types` | `["fix", "perf", "revert"]` | the types that release a patch version - see [types and releases](#types-and-releases) |

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

### The subject

Every subject - a pull request's title, and with `keep-commits` every commit - must take one of the
forms in `commits.subject`. A form is a template: literal text with placeholders. By default a
subject ends in a reference to an issue, or to the pull request itself:

```toml
[commits]
subject = ["{conventional} {description} (#{issue})", "{conventional} {description} (PR #{pull})"]
```

| Placeholder | Stands for | Checked |
|---|---|---|
| `{conventional}` | `<type>[(<scope>)][!]:` - the type, the optional scope and breaking marker, and the colon | the type is one of [`commits.types`](#types-and-releases); the scope is not empty, has no whitespace, and is one of `commits.scopes` if the repository sets it |
| `{description}` | the description: everything between its neighbours in the template, starting with a non-space character | - |
| `{issue}` | a number | it is an issue of this repository |
| `{pull}` | a number | it is this pull request's number |
| `{key}` | an issue key of another tracker, such as Jira, Linear or YouTrack: an uppercase letter, then uppercase letters, digits and `_`, a dash and a number - `PROJ-123` | its project - the part before the dash - is one of `commits.projects`, if the repository sets it. mergeweasel cannot look the key up |

Some forms and the subjects they accept:

| `commits.subject` | Accepts |
|---|---|
| the default | `feat(ui): add dark mode (#12)`, `chore: tidy the README (PR #31)` |
| `["{conventional} {description}"]` | `feat(ui): add dark mode` - no reference at all |
| `["{conventional} {description} (#{issue})", "{conventional} {description}"]` | both of the above: the reference is optional |
| `["{conventional} {key} {description}"]` | `feat(ui): PROJ-12 add dark mode` |
| `["[{key}] {conventional} {description}", "{conventional} {description} (PR #{pull})"]` | `[PROJ-12] feat: add dark mode`, `chore: tidy the README (PR #31)` |

**The first form that fits decides.** mergeweasel tries the forms in the order of the list. The
first one the subject fits is the one it is checked against; a later one is not tried, even when a
check fails. A repository that makes the reference optional therefore lists the form without a reference
last: `feat: add dark mode (#4711)` then fits the first form, and #4711 must be an issue. Listed
first, the form without a reference would take `(#4711)` as part of the description, unchecked.

**Projects.** With `{key}`, any project passes by default, and so does a typo in it. A repository
lists its projects to catch that:

```toml
[commits]
subject = ["{conventional} {key} {description}"]
projects = ["PROJ", "OPS"]
```

```
PR title: unknown project 'PORJ' in PORJ-12, expected one of: PROJ, OPS
```

**A form is valid when**

- it contains `{conventional}` and `{description}` once each, `{conventional}` first;
- it contains at most one of `{issue}`, `{pull}` and `{key}`;
- text separates every two placeholders - in `{description}{key}` the description's end is
  guesswork;
- a space follows `{conventional}`, as Conventional Commits requires;
- it neither starts nor ends with whitespace - mergeweasel strips a title before it checks it;
- every `{...}` is one of the placeholders above. There is no way to write a literal brace.

`commits.subject` must not be empty. `commits.projects`, if set, must not be empty either, its
entries must be projects (`PROJ`, `OPS_2`), and some form must contain `{key}`. An invalid form
makes the file invalid, and the message names the form and the broken rule.

A subject that fits no form fails, and the message lists the forms:

```
PR title: subject must read '[<key>] <type>[(<scope>)][!]: <description>' or '<type>[(<scope>)][!]: <description> (PR #<pr>)', got: 'feat: add dark mode (#12)'
```

**Text before the type.** mergeweasel reads any form, also one with text before `{conventional}`
such as `[{key}] {conventional} {description}`. Other tools that read Conventional Commits -
commitlint, changelog generators, Renovate's detection of semantic commits - expect the type at the
start, and find none in `[PROJ-12] feat: add dark mode`. Repositories that use such tools put the
reference after the colon or at the end.

**The release notes.** A release lists each releasing commit as its subject without the type and the
breaking marker, in the commit's own form: `feat(ui): add dark mode (#12)` becomes
`ui: add dark mode (#12)`, `[PROJ-12] feat: add dark mode` becomes `[PROJ-12] add dark mode`.

**Changing the forms.** The commit check reads `commits.subject` from the pull request's branch, the
release from `main`. A commit on `main` that fits none of `main`'s forms releases nothing, and the
release prints it:

```
release: 3f2a91c is outside the subject rule and releases nothing: 'feat: add dark mode (#12)'
```

So a repository that changes its forms does it in two pull requests: the first adds the new forms
next to the old ones, the second removes the old ones. Every pass releases `main` before it merges,
and merges at most one pull request; the pass that merges the second pull request has released the
commits of the old forms already. Two ways lose them: replacing the forms in a single pull request,
and a release that fails between the two merges - then merge the second pull request after a
release has succeeded, or add the old forms back until it has.

### Types and releases

The type is the word at the start of a subject: `feat` in `feat(ui): add dark mode (#12)`. Which
types a subject may use, and which of them release, is set in the file. By default the types are
those of `@commitlint/config-conventional`, the list release tools and changelog generators expect,
and they release as semantic-release does:

```toml
[commits]
types = ["feat", "fix", "perf", "refactor", "style", "docs", "test", "build", "ci", "chore", "revert"]

[release]
minor_types = ["feat"]
patch_types = ["fix", "perf", "revert"]
```

**The accepted types.** `commits.types` lists the types a subject may use. Any other type fails
`check-commits`, and the queue flags the pull request `needs-fix` instead of merging it. The message
names the type and lists the accepted ones:

```
PR title: unknown type 'feature', expected one of: feat, fix, docs, chore
```

The list must not be empty, and each entry must consist of the lowercase letters `a` to `z` only:
the subject's `{conventional}` part reads nothing else as the type.

**What releases.** `release.minor_types` and `release.patch_types` decide which merged commits
release, and with a SemVer template how far the version rises: a type in `minor_types` raises the
minor version, a type in `patch_types` the patch version, and a type in neither list releases
nothing. A breaking change, marked with `!`, always raises the major version, whatever its type.
git's own revert subject, `Revert "..."`, counts as the type `revert`. With a CalVer template the
lists decide only whether a merge releases. The release notes list a type of `minor_types` under
"Features" and a type of `patch_types` under "Fixes", whatever its name: with `build` in
`patch_types`, a dependency update shows under "Fixes".

| `mergeweasel.toml` | Effect |
|---|---|
| `patch_types = ["fix", "perf", "revert", "build"]` | a `build` commit - a dependency update, say - releases a patch |
| `minor_types = []` | no type releases a minor version; `feat` releases nothing unless it is breaking |
| `minor_types = ["feat", "fix"]` | `fix` releases a minor version and leaves the default `patch_types` |
| `types = ["feat", "fix", "docs", "chore"]` | only these four types are accepted; `perf` and `revert` leave the default `patch_types` |

The rules for the two lists:

- Each entry must be one of `commits.types`, and no type may be in both lists. Otherwise the file is
  invalid, and the message names the entry.
- A list may be empty: then no type releases that kind of version.
- A list the file leaves out keeps its default, minus the types that are not in `commits.types` and
  minus the types the other list names. So narrowing `commits.types`, or moving a type to the other
  list, takes one key, as the last two rows of the table show.

**Which version counts.** The commit check reads `commits.types` from the pull request's branch, so
a pull request that adds a type may already use it in its own title. The release reads the lists on
`main`. Each pass releases before it merges, so the release after the merge of a change to the
lists already uses the changed lists - for the commit that changes them, too.

**Removing a type.** A commit on `main` with a type that `commits.types` no longer lists is still
read by the release, and releases nothing - the release lists can only name listed types. Every
pass releases `main` before it merges, so commits merged before the change are released already;
only a release that failed in between leaves them unreleased.

**Renovate.** Renovate takes the type of its subjects from its own configuration - `chore(deps):`
by default, other types through presets or `semanticCommitType`, for example
`ci(deps): update docker.io/chli1ro/mergeweasel docker tag to v2.1.0`. A repository that uses
Renovate and sets `commits.types` has to include every type its Renovate writes, or those pull
requests are flagged `needs-fix`.

### The line length

The `## Commit` section of a pull request becomes the body of its squash commit. Each of its lines
may be at most `commits.max_line` characters long, 72 by default - git's convention, which keeps
`git log` readable in an 80-column terminal. A line without whitespace, a long URL for example, is
exempt, since it cannot be wrapped. A longer line fails `check-commits`:

```
'## Commit' line 3 is 85 characters, wrap at 72
```

A repository with a different convention sets its own limit:

```toml
[commits]
max_line = 100
```

The value must be a whole number of at least 1.

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
major version, a type of `release.minor_types` the minor version, and a type of
`release.patch_types` the patch version ([types and releases](#types-and-releases)). A new
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
| Workflows | `mergeweasel-queue.yml` (workflow `mergeweasel-queue`), `mergeweasel-commits.yml` (workflow `mergeweasel-commits`) |
| Carry-over status | context `mergeweasel/approval`; description `approved at <old head>`, then one line `approved by <login>` per reviewer |
| API calls | 30 s timeout; reading a rebased head and writing the carry-over are tried 3 times, 5 s apart |
