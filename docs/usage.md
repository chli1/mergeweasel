# Usage

For pull request authors in a repository that runs mergeweasel.

## The labels

The labels go by these names unless your Gitea setup renames them; the `settings:` line at the top
of every mergeweasel log shows the names in use.

- **`automerge`** queues the pull request. If the repository requires approvals, approve the pull
  request first and set the label afterwards: an approval alone does not wake the queue. The pull
  request lands once its checks pass. Removing the label withdraws it, even while its checks run.
- **`needs-fix`** is set by mergeweasel, always together with a comment that says why. Fix the
  cause, then remove the label to queue the pull request again. If the repository is set up for
  Renovate, a push by Renovate removes the label by itself.
- **`keep-commits`** lands the branch's commits as they are instead of squashing them into one.

## The commit message

By default mergeweasel squashes the pull request into one commit. The pull request's **title**
becomes the commit's subject, and the **`## Commit` section** of its description becomes the
commit's body. [Configuration](configuration.md#fixed-values) lists the exact rules.

The scope - `api` in `fix(api): ...` - is optional. Unless the repository says otherwise, any scope
is accepted. A repository can restrict the scopes to a fixed list in its `mergeweasel.toml`
([configuration](configuration.md#scopes)); a title with any other scope is then rejected, and the
message lists the scopes the repository allows.

Valid titles:

```
feat: add dark mode (#12)
fix(api): retry on 409 (#7)
feat!: drop the v1 endpoints (#30)
chore: tidy the README (PR #31)
```

`(#N)` names an issue of the repository. `(PR #N)` is for a change without an issue and names the
pull request itself.

Rejected titles:

```
feat(): add dark mode (#12)        an empty scope - leave out the parentheses
feat(my ui): add dark mode (#12)   a scope with whitespace
feat(domian): add dark mode (#12)  a scope missing from commits.scopes, if the repository sets it
add dark mode                      no type, no reference
```

A description with a `## Commit` section. Only that section lands; everything from the next `##`
heading on stays in the pull request:

```markdown
## Commit

Dark mode follows the system setting; the toggle overrides it per
browser.

## Notes

Screenshots and the review discussion stay here.
```

The section is optional. It must keep to the line limit in
[configuration](configuration.md#fixed-values) and must not contain a `Co-authored-by:` or
"Generated with" line.

Editing the title, the description or the labels runs the commit check again by itself.
Re-running a failed check job by hand works too: the check reads the pull request as it is when the
job runs, not as it was when the job first started, so the re-run judges the fixed text.

## What releases

After the merge, the queue releases if the repository is set up for it
([onboarding](onboarding.md#releases)). The commit types decide whether a merge releases: `feat`,
`fix`, `perf`, `revert` and breaking changes (`!`) do, the other types do not. With SemVer
versions, the default, they also decide the increase: `!` raises the major version, `feat` the
minor version, and the others the patch version.

The release notes list the description of each releasing commit. Under a breaking change they also
show its `## Commit` section, so say there what users must change.

## `keep-commits`

Every commit of the pull request lands as it is. So every commit must follow the subject rule
above, and none may be a merge commit. Subjects made by `git commit --fixup`, `git commit --squash`
and `git revert` (`fixup! ...`, `squash! ...`, `Revert "..."`) are exempt, because they refer to an
earlier commit.

## What a `needs-fix` comment means

- **Required checks failed:** re-run the failed jobs or push a fix.
- **Rebasing onto `main` failed with a conflict:** rebase onto `main` (the default branch the
  comment names) yourself and resolve the conflict.
- **Gitea refused the merge:** fix the cause Gitea names.
- **What this pull request would land on main is invalid:** fix the title or the `## Commit`
  section, or with `keep-commits` the commits. An unknown scope shows up here too: use one of the
  scopes the message lists, or leave the scope out.
- **`mergeweasel.toml` is invalid:** the pull request changes `mergeweasel.toml`, and the changed
  file cannot be read - an unknown key, say, or a value of the wrong type. The comment names the
  problem. Fix the file on the branch.

Then remove `needs-fix`.
