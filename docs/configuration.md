# Configuration

mergeweasel has no configuration file yet. Everything it needs comes from the Gitea Actions
environment, and the values below are fixed; a configuration file arrives with the first repository
that needs a different value, and these values become its defaults.

## The command

```
mergeweasel pass [--dry-run]
mergeweasel check-commits --event <path>
```

- `pass` runs one queue pass. `--dry-run` decides and reports, and changes nothing.
- `check-commits` checks what a pull request would land on `main`; `<path>` is the `pull_request`
  event payload (`$GITHUB_EVENT_PATH` in a workflow).

Both exit with 0 when done or passed, and 1 when they fail.

## Environment

| Variable | Set by | Used by |
|---|---|---|
| `GITHUB_SERVER_URL` | Gitea Actions | both commands - the Gitea server |
| `GITHUB_REPOSITORY` | Gitea Actions | both commands - `owner/repository` |
| `MERGEWEASEL_MERGER_TOKEN` | the workflow, from the secret | `pass` - required, except for `--dry-run`; the merger rebases, writes the carry-over and merges |
| `MERGEWEASEL_APPROVER_TOKENS` | the workflow, from the secret | `pass` - optional, one token per line; the approver accounts restore the approvals a rebase dismissed. `--dry-run` without it assumes enough |
| `GITHUB_TOKEN` | the workflow, from the run token | `check-commits`; `pass --dry-run` without `MERGEWEASEL_MERGER_TOKEN` - the merger is then unknown and any carry-over counts |
| `GITHUB_RUN_ID`, `GITHUB_EVENT_NAME` | Gitea Actions | `pass` - serializes the passes; without `GITHUB_RUN_ID` it does not wait |

## Image

`ghcr.io/chli1/mergeweasel`, tagged `X.Y.Z` and `latest`. Pin `X.Y.Z`.

## Fixed values

| What | Value |
|---|---|
| Labels | `automerge`, `needs-fix`, `keep-commits` |
| Base branch | `main` |
| Renovate account | `renovate` - its push removes `needs-fix` |
| Commit types | `feat`, `fix`, `perf`, `refactor`, `style`, `docs`, `test`, `build`, `ci`, `chore`, `revert` (the list of `@commitlint/config-conventional`) |
| Subject | `<type>[(<scope>)][!]: <description> (#<issue>)` or `... (PR #<pr>)` |
| Scope | optional; non-empty and without whitespace |
| `## Commit` lines | at most 72 characters, unless without whitespace |
| Workflows | `mergeweasel-queue.yml` (workflow `mergeweasel-queue`), `mergeweasel-commits.yml` (workflow `mergeweasel-commits`) |
| Carry-over status | context `mergeweasel/approval`; description `approved at <old head>`, then one line `approved by <login>` per reviewer |
| API calls | 30 s timeout; reading a rebased head and writing the carry-over tried 3 times, 5 s apart |
