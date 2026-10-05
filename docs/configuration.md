# Configuration

Everything mergeweasel needs to reach Gitea comes from the Gitea Actions environment. A repository
can adjust mergeweasel in [`mergeweasel.toml`](#the-configuration-file); the
[fixed values](#fixed-values) are the same for every repository.

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

## The configuration file

An optional file named `mergeweasel.toml` in the root of the repository. mergeweasel always reads
the version on `main`, so a change takes effect once its pull request is merged.

Every key is optional; a missing key keeps its default. An unknown key or an invalid value is an
error, and the message names the key.

| Key | Default | Meaning |
|---|---|---|
| `release.tag` | `"v{major}.{minor}.{patch}"` | how versions look - see [the release tag](#the-release-tag) |

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

Upcoming releases make some of these configurable.

| What | Value |
|---|---|
| Labels | `automerge`, `needs-fix`, `keep-commits` |
| Base branch | `main` |
| Renovate account | `renovate`. Its push removes `needs-fix`. Its own updates get approved by the approver accounts, as long as only it or the merger pushed to them and it added `automerge` last |
| Releasing types | `!` major; `feat` minor; `fix`, `perf`, `revert`, `Revert "..."` patch |
| Commit types | `feat`, `fix`, `perf`, `refactor`, `style`, `docs`, `test`, `build`, `ci`, `chore`, `revert` (the list of `@commitlint/config-conventional`) |
| Subject | `<type>[(<scope>)][!]: <description> (#<issue>)` or `... (PR #<pr>)` |
| Scope | optional; if present, non-empty and without whitespace |
| `## Commit` lines | at most 72 characters, except for lines without whitespace |
| Workflows | `mergeweasel-queue.yml` (workflow `mergeweasel-queue`), `mergeweasel-commits.yml` (workflow `mergeweasel-commits`) |
| Carry-over status | context `mergeweasel/approval`; description `approved at <old head>`, then one line `approved by <login>` per reviewer |
| API calls | 30 s timeout; reading a rebased head and writing the carry-over are tried 3 times, 5 s apart |
