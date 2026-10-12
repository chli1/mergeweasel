# Configuration

Everything a repository sets for mergeweasel, ordered by where it is set. One rule decides the
place: a repository's commits must work unchanged on another Gitea. What belongs to the Gitea
installation - tokens, label names, accounts, runners - is set in Gitea's settings, and only the
rules for the repository's commits and releases travel with it, in a file.

- [Secrets](#secrets): the tokens of the merger and the approver accounts.
- [Actions variables](#actions-variables): the label names, the Renovate account and the runner
  label.
- [The base branch](#the-base-branch): the repository's default branch.
- [`mergeweasel.toml`](#the-configuration-file): the rules for the repository's commits and
  releases.

The [fixed values](#fixed-values) are the same everywhere. The workflows hand the secrets and
variables to the image; [design](design.md#the-image-and-its-workflows) describes that interface.
What a message in the log means, and how to fix its cause, is in
[troubleshooting](troubleshooting.md).

Throughout these documents, `main` stands for the repository's default branch, and the labels go by
their default names.

## Secrets

Set these as Gitea Actions secrets, under *Settings > Actions > Secrets* of the repository, or of
the organization to share them between its repositories. A repository's secret overrides the
organization's secret of the same name. Only `mergeweasel-queue.yml` reads them.

| Secret | Required | Meaning |
|---|---|---|
| `MERGEWEASEL_MERGER_TOKEN` | yes, except for a dry run | the token of the merger, which rebases, records the approvals across a rebase and merges |
| `MERGEWEASEL_APPROVER_TOKENS` | no | the tokens of the approver accounts, one per line, which restore the approvals a rebase dismissed and approve Renovate's own updates. Without them, no approval is restored, and a dry run assumes there are enough |

[The accounts](onboarding.md#the-accounts) lists the scopes the tokens need and how one account can
fill both roles. Every pass prints the logins behind the tokens, never the tokens.

## Actions variables

Set these as Gitea Actions variables, under *Settings > Actions > Variables* of the organization or
of the repository. A repository's variable overrides the organization's variable of the same name,
so an organization can set its names once for all its repositories. An unset or empty variable
keeps its default.

| Variable | Default | Meaning |
|---|---|---|
| `MERGEWEASEL_AUTOMERGE_LABEL` | `automerge` | the label that queues a pull request |
| `MERGEWEASEL_NEEDS_FIX_LABEL` | `needs-fix` | the label the queue sets on a pull request it cannot land |
| `MERGEWEASEL_KEEP_COMMITS_LABEL` | `keep-commits` | the label that lands the commits as they are instead of squashing them |
| `MERGEWEASEL_PRIO_LABEL` | `prio` | the label that puts a queued pull request ahead of every other |
| `MERGEWEASEL_RENOVATE_ACCOUNT` | none: the Renovate rule is off | the account whose own updates the approver accounts approve, and whose push removes `needs-fix` - see [Renovate](#renovate) |
| `MERGEWEASEL_RUNNER_LABEL` | `ubuntu-latest` | the runner label both workflows run on; only the workflows read it |

For example, an organization whose labels are called `merge-me`, `broken`, `no-squash` and
`hotfix`, whose Renovate runs as `deps-bot` and whose runners carry the label `docker` sets these
organization variables:

| Variable | Value |
|---|---|
| `MERGEWEASEL_AUTOMERGE_LABEL` | `merge-me` |
| `MERGEWEASEL_NEEDS_FIX_LABEL` | `broken` |
| `MERGEWEASEL_KEEP_COMMITS_LABEL` | `no-squash` |
| `MERGEWEASEL_PRIO_LABEL` | `hotfix` |
| `MERGEWEASEL_RENOVATE_ACCOUNT` | `deps-bot` |
| `MERGEWEASEL_RUNNER_LABEL` | `docker` |

Every run of either command prints the settings it works with near the start of its log, for
example:

```
settings: base branch main; labels merge-me, broken, no-squash, hotfix; Renovate account deps-bot
```

Both commands fail when two label variables name the same label. The pass also fails, before the
release, when a required label exists neither in the repository nor in its organization; the
message names the missing labels. `automerge` and `needs-fix` are always required: without them the
queue can neither find nor flag a pull request. `keep-commits` is optional - without it every pull
request is squashed - unless `MERGEWEASEL_KEEP_COMMITS_LABEL` names a label, which then has to
exist. Likewise `prio`: without it no pull request goes first, unless `MERGEWEASEL_PRIO_LABEL`
names a label. When the pass cannot read the organization's labels - in a repository owned by a
user, or in a dry run with the run token - it prints that it did not check, and goes on.

### Renovate

mergeweasel lands [Renovate](https://docs.renovatebot.com/)'s pull requests like any other, once
Renovate labels them `automerge` (its `addLabels` option) instead of merging them itself. With
`MERGEWEASEL_RENOVATE_ACCOUNT` set to the login of the account Renovate runs as, the queue treats
that account's pull requests specially:

- **Approval:** its own update gets approved by the approver accounts, so no human has to. That
  holds as long as only Renovate or the merger pushed to it, and Renovate added `automerge` last.
  Once anyone else pushes, or a human sets `automerge`, the update needs a human approval like any
  other pull request. Without approver accounts, every update waits for a human approval.
- **Retry:** a push by Renovate to an update labelled `needs-fix`, for example its rebase, removes
  the label, so a failed update is retried without anyone stepping in.

Without the variable, Renovate's pull requests are ordinary pull requests. The variable has no
default: whichever account it names gets its updates approved
([security model](design.md#security-model)).

Renovate's subjects have to pass the commit check like any other. Renovate writes them with the
scope `deps` and takes the type from its own configuration - `chore(deps):` by default, other types
through presets or `semanticCommitType`, for example
`ci(deps): update docker.io/chli1ro/mergeweasel docker tag to v2.1.0`. A repository that sets
[`commits.scopes`](#scopes) has to include `deps`, and one that sets
[`commits.types`](#types-and-releases) every type its Renovate writes, or those pull requests are
flagged `needs-fix`. With the default forms, Renovate's titles need no reference:
`chore(deps): update pytest to v8.4.2` from pull request #81 lands as
`chore(deps): update pytest to v8.4.2 (#81)`.

The image is on Docker Hub, where Renovate finds its release dates, so a `minimumReleaseAge`
applies to mergeweasel like to any other image.

## The base branch

The base branch is the repository's default branch, as set in its settings - there is nothing to
set for mergeweasel. The queue lands on it, releases it, and reads its branch protection; pull
requests against any other branch are left alone, and the workflows skip them before a runner
starts.

## The configuration file

An optional file named `mergeweasel.toml` in the root of the repository.

Which version of the file mergeweasel reads depends on what it is doing:

- **The release** reads the version on `main`. A change to a `release` key therefore takes effect
  with the first release after its pull request is merged; each pass releases before it merges, so
  that release already covers the commit that makes the change.
- **The commit check** reads the version on the pull request's branch. This covers both
  `check-commits` and the check the queue runs right before it merges. So a pull request that
  changes the file is already checked against its own change: one that adds a scope to
  `commits.scopes` may use that scope in its own title.

Because the commit check reads the branch's version, a pull request that breaks the file - with a
value of the wrong type, say - fails its own check, and the queue flags it `needs-fix` instead of merging it.
An invalid file therefore stays on its branch and never holds up the other pull requests.

Every key is optional; a missing key keeps its default. An invalid value is an error, and the
message names the key. An unknown table or key is ignored: every run that reads the file prints it,
so a typo shows in the log, and a file that already uses a key of a newer mergeweasel keeps working
with an older one:

```
mergeweasel.toml: unknown key commits.scopse ignored
```

| Key | Default | Meaning |
|---|---|---|
| `commits.types` | the types of `@commitlint/config-conventional` | the types a subject may use - see [types and releases](#types-and-releases) |
| `commits.scopes` | any scope | the scopes a subject may use - see [scopes](#scopes) |
| `commits.subject` | `(#<pr>)` at the end, or no reference | the forms a subject may take - see [the subject](#the-subject) |
| `commits.projects` | any project | the projects a `{key}` may name - see [the subject](#the-subject) |
| `commits.squash_append_pull_reference` | `true` | a squash appends ` (#<pr>)` to the title - see [the appended pull reference](#the-appended-pull-reference) |
| `commits.max_line` | `72` | the longest line of the `## Commit` section - see [the line length](#the-line-length) |
| `release.enabled` | `false` | whether the queue releases - see [switching the release on](#switching-the-release-on) |
| `release.tag` | `"v{major}.{minor}.{patch}"` | how versions look - see [the release tag](#the-release-tag) |
| `release.minor_types` | `["feat"]` | the types that release a minor version - see [types and releases](#types-and-releases) |
| `release.patch_types` | `["fix", "perf", "revert"]` | the types that release a patch version - see [types and releases](#types-and-releases) |

### Scopes

The scope is the optional part in parentheses after the commit type: `ui` in
`feat(ui): add dark mode (#12)`. It is part of `{conventional}`, so `commits.scopes` needs forms
with it - see [subjects without a type](#the-subject). By default any scope is
accepted, as long as it is not empty and contains no whitespace. That also means a typo goes
through unnoticed: `feat(domian): ...` passes just like `feat(domain): ...`.

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

### The subject

Every subject that lands on `main` - a squash's subject, and with `keep-commits` every commit - must
take one of the forms in `commits.subject`. A squash's subject is the pull request's title with the
pull request's number appended ([the appended pull reference](#the-appended-pull-reference)). A form
is a template: literal text with placeholders. By default a subject ends in a reference to the pull
request, or has no reference at all:

```toml
[commits]
subject = ["{conventional} {description} (#{pull})", "{conventional} {description}"]
```

A squash always fits the first form: the title `feat(ui): add dark mode` lands as
`feat(ui): add dark mode (#80)`. The second form is for `keep-commits` commits, which land as they
are: their pull request's number is not known yet when they are written, so they may do without
one.

| Placeholder | Stands for | Checked |
|---|---|---|
| `{conventional}` | `<type>[(<scope>)][!]:` - the type, the optional scope and breaking marker, and the colon | the type is one of [`commits.types`](#types-and-releases); the scope is not empty, has no whitespace, and is one of `commits.scopes` if the repository sets it |
| `{description}` | the description: everything between its neighbours in the template, starting with a non-space character | - |
| `{issue}` | a number | it is an issue of this repository |
| `{pull}` | a number | it is this pull request's number |
| `{pull_or_issue}` | a number | it is this pull request's number, or an issue of this repository |
| `{key}` | an issue key of another tracker, such as Jira, Linear or YouTrack: an uppercase letter, then uppercase letters, digits and `_`, a dash and a number - `PROJ-123` | its project - the part before the dash - is one of `commits.projects`, if the repository sets it. mergeweasel cannot look the key up |

Some forms and the subjects they accept:

| `commits.subject` | Accepts |
|---|---|
| the default | `feat(ui): add dark mode (#80)` from pull request #80, `feat(ui): add dark mode` |
| `["{conventional} {description} (#{issue})", "{conventional} {description} (PR #{pull})"]` | `feat(ui): add dark mode (#12)`, `chore: tidy the README (PR #31)` - with the append switched off |
| `["{conventional} {description} (#{pull_or_issue})"]` | `feat(ui): add dark mode (#12)` for issue #12, `feat(ui): add dark mode (#80)` from pull request #80 |
| `["{conventional} {key} {description}"]` | `feat(ui): PROJ-12 add dark mode` |
| `["[{key}] {conventional} {description}", "{conventional} {description} (PR #{pull})"]` | `[PROJ-12] feat: add dark mode`, `chore: tidy the README (PR #31)` |
| `["{description} (#{pull_or_issue})"]` | `fix the login (#12)` for issue #12, `Add dark mode (#80)` from pull request #80 - with the release off, see [subjects without a type](#the-subject) |

**The first form that fits decides.** mergeweasel tries the forms in the order of the list. The
first one the subject fits is the one it is checked against; a later one is not tried, even when a
check fails. A repository that makes the reference optional therefore lists the form without a reference
last, as the default does: `feat: add dark mode (#4711)` then fits the first form, and #4711 must be
this pull request. Listed first, the form without a reference would take `(#4711)` as part of the
description, unchecked.

`(#{issue})` and `(#{pull})` look the same in a subject, so of two forms that differ only there,
the first always decides. A repository that accepts both kinds of number in one place uses
`{pull_or_issue}`.

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

**Subjects without a type.** A repository that does not use Conventional Commits leaves
`{conventional}` out of its forms. mergeweasel then reads no type, and a subject passes with
whatever its description starts with:

```toml
[commits]
subject = ["{description} (#{pull_or_issue})", "{description}"]
```

The subject `fix the login (#12)` passes for issue #12, and the title `Add dark mode` from pull
request #80 lands as `Add dark mode (#80)`. Such forms come with three rules:

- The release stays off. The release decides from the type whether a merge releases and how far
  the version rises, so `release.enabled = true` with forms without `{conventional}` makes the
  file invalid:

  ```
  mergeweasel.toml: release.enabled needs commits.subject templates with {conventional} - the release reads the commit type; add it, or set release.enabled = false
  ```

- Either every form contains `{conventional}` or none does. In a mixed list, a typed subject with
  a typo in its type, `fx: repair the login (#12)`, would fit the form without a type and pass
  with `fx: repair the login` as its description, so the file is invalid instead.
- The keys that read `{conventional}` - `commits.types`, `commits.scopes`,
  `release.minor_types` and `release.patch_types` - make the file invalid when set, rather than
  being silently ignored:

  ```
  mergeweasel.toml: commits.types needs commits.subject templates with {conventional}
  ```

A subject without a type still has to fit a form, and its reference is checked as with a type:
with the forms above, a `keep-commits` commit `fix the login (#4711)` fails if #4711 is neither
the pull request nor an issue.

**A form is valid when**

- it contains `{description}` once, and `{conventional}` at most once, before `{description}`;
- it contains at most one of `{issue}`, `{pull}`, `{pull_or_issue}` and `{key}`;
- text separates every two placeholders - in `{description}{key}` the description's end is
  guesswork;
- a space follows `{conventional}`, as Conventional Commits requires;
- either every form of the list contains `{conventional}` or none does;
- it neither starts nor ends with whitespace - mergeweasel strips a title before it checks it;
- every `{...}` is one of the placeholders above. There is no way to write a literal brace.

`commits.subject` must not be empty. `commits.projects`, if set, must not be empty either, its
entries must be projects (`PROJ`, `OPS_2`), and some form must contain `{key}`. An invalid form
makes the file invalid, and the message names the form and the broken rule.

A subject that fits no form fails, and the message lists the forms:

```
PR title: subject must read '<type>[(<scope>)][!]: <description> (#<pr>)' or '<type>[(<scope>)][!]: <description>', got: 'add dark mode (#80)'
```

**Text before the type.** mergeweasel reads any form, also one with text before `{conventional}`
such as `[{key}] {conventional} {description}`. Other tools that read Conventional Commits -
commitlint, changelog generators, Renovate's detection of semantic commits - expect the type at the
start, and find none in `[PROJ-12] feat: add dark mode`. Repositories that use such tools put the
reference after the colon or at the end.

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

### The appended pull reference

When Gitea squashes a pull request, it suggests the pull request's title with its number appended:
pull request #80 titled `feat: add dark mode` becomes `feat: add dark mode (#80)`. mergeweasel
squashes the same way, so a title needs no reference of its own:

```toml
[commits]
squash_append_pull_reference = true
```

- The subject is the title, stripped of surrounding whitespace, with ` (#<pr>)` appended.
- A title that already ends in its own number - copied from Gitea's suggestion, say - is taken as it
  is: `feat: add dark mode (#80)` lands as `feat: add dark mode (#80)`, not with the number twice.
- A title that ends in another number still gets its own appended: `feat: add dark mode (#79)` lands
  as `feat: add dark mode (#79) (#80)`, just as Gitea would suggest it. With the default forms,
  `(#79)` is then part of the description.
- The check, its messages and the merge all use the appended subject, and `commits.subject`
  describes it: a form ending in ` (#{pull})` or ` (#{pull_or_issue})` takes it.
- It applies to squash merges only. `keep-commits` commits land as they are; with the default forms
  they may end in their pull request's number or carry no reference.

With the append switched on, two rules hold for `commits.subject`, and a file that breaks one is
invalid:

- Some form ends in ` (#{pull})` or ` (#{pull_or_issue})`, or no squash could land:

  ```
  mergeweasel.toml: commits.squash_append_pull_reference needs a commits.subject form ending in ' (#{pull})' or ' (#{pull_or_issue})', or set commits.squash_append_pull_reference = false
  ```

- No form ends in ` (#{issue})`. Every squash subject would fit it, since the appended number looks
  the same, and then fail, since that number is the pull request's and not an issue's:

  ```
  mergeweasel.toml: commits.subject: '{conventional} {description} (#{issue})' ends like the appended pull reference - use {pull_or_issue}, or set commits.squash_append_pull_reference = false
  ```

A repository that wants its titles to carry their own reference switches the append off and lists
its forms. mergeweasel's own repository, for example, references the issue, or the pull request
where there is no issue:

```toml
[commits]
squash_append_pull_reference = false
subject = ["{conventional} {description} (#{issue})", "{conventional} {description} (PR #{pull})"]
```

A repository whose forms use a Jira key, such as `["[{key}] {conventional} {description}"]`,
switches the append off as well, or adds ` (#{pull})` to the end of a form.

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

The types need forms with `{conventional}`: in a repository whose forms have none, setting
`commits.types`, `release.minor_types` or `release.patch_types` makes the file invalid - see
[subjects without a type](#the-subject).

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
lists decide only whether a merge releases.

**The release notes.** The notes list the releasing commits, oldest first, under "Breaking changes",
"Features" and "Fixes". A type of `minor_types` shows under "Features" and a type of `patch_types`
under "Fixes", whatever its name: with `build` in `patch_types`, a dependency update shows under
"Fixes". Each commit shows as its subject without the type and the breaking marker, in the commit's
own form: `feat(ui): add dark mode (#80)` becomes `ui: add dark mode (#80)`,
`[PROJ-12] feat: add dark mode` becomes `[PROJ-12] add dark mode`. Under a breaking change the notes
also show its commit body.

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

**Removing a type.** A commit on `main` with a type that `commits.types` no longer lists is still
read by the release, and releases nothing - the release lists can only name listed types. Every
pass releases `main` before it merges, so commits merged before the change are released already;
only a release that failed in between leaves them unreleased.

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

### Switching the release on

The release is off by default: the queue merges, but never tags `main` or creates a Gitea release,
whatever the merged commits and whatever tags the repository carries. A repository that has
mergeweasel release switches it on:

```toml
[release]
enabled = true
```

With the default SemVer template, the first release also needs a first tag on `main`, set by hand -
see [SemVer and CalVer](#semver-and-calver). While the release is off, each pass says how to switch
it on:

```
release: to release, set release.enabled = true in mergeweasel.toml
```

With the release on, every form must contain `{conventional}`, so every subject needs a type from
`commits.types`. With the release off, the forms decide: forms with `{conventional}` still require
a type, forms without it require none - see [subjects without a type](#the-subject).
Switching the release on in a repository whose forms have no type makes the file invalid, and the
commit check of that pull request fails.

The other `release` keys stay checked while the release is off: an invalid `release.tag` or an
unknown type in `release.patch_types` makes the file invalid, like any other invalid value. With
forms without a type, `release.minor_types` and `release.patch_types` must not be set at all. Set
them as you want them for the day you switch the release on, or leave them out. Once switched on,
the first release covers every releasing commit since the previous release tag, the ones merged
while the release was off included.

## Image

`docker.io/chli1ro/mergeweasel`, tagged `X.Y.Z` and `latest`. Pin `X.Y.Z`.

## Fixed values

| What | Value |
|---|---|
| Workflows | `mergeweasel-queue.yml` (workflow `mergeweasel-queue`), `mergeweasel-commits.yml` (workflow `mergeweasel-commits`) |
| Carry-over status | context `mergeweasel/approval`; description `approved at <old head>`, then one line `approved by <login>` per reviewer |
| API calls | 30 s timeout; reading a rebased head and writing the carry-over are tried 3 times, 5 s apart |
