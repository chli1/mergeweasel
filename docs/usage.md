# Usage

For pull request authors in a repository that runs mergeweasel.

## The labels

- **`automerge`** - queue the pull request: approve it first if the repository requires approvals,
  then set the label - an approval alone does not wake the queue. It lands once its checks pass. Removing the label withdraws it,
  mid-check included.
- **`needs-fix`** - set by mergeweasel, always with a comment that says why. Fix the cause, then
  remove the label to queue the pull request again. A push by Renovate removes it by itself.
- **`keep-commits`** - land the branch's commits as they are instead of squashing them.

## The commit message

By default mergeweasel squashes: the pull request's **title** becomes the commit's subject, and the
**`## Commit` section** of its description becomes the commit's body. The rules are listed in
[configuration](configuration.md#fixed-values).

Valid titles:

```
feat: add dark mode (#12)
fix(api): retry on 409 (#7)
feat!: drop the v1 endpoints (#30)
chore: tidy the README (PR #31)
```

`(#N)` names an issue of the repository; `(PR #N)` is for a change without an issue and names the
pull request itself. Rejected titles:

```
feat(): add dark mode (#12)        an empty scope - leave out the parentheses
feat(my ui): add dark mode (#12)   a scope with whitespace
add dark mode                      no type, no reference
```

A description with a `## Commit` section; only that section lands, everything from the next `##`
heading on stays in the pull request:

```markdown
## Commit

Dark mode follows the system setting; the toggle overrides it per
browser.

## Notes

Screenshots and the review discussion stay here.
```

The section is optional, keeps to the line limit in
[configuration](configuration.md#fixed-values), and carries no `Co-authored-by:` or "Generated with"
line.

## What releases

After the merge, the queue releases if the repository has a `vX.Y.Z` tag: `feat` makes a minor
release, `fix`, `perf` and `revert` a patch release, `!` a major one; the other types release
nothing. The release notes list the description of each releasing commit; a breaking change's
`## Commit` section appears under it - say there what users must change.

## `keep-commits`

Every commit of the pull request lands as it is, so every commit follows the subject rule above, and
none is a merge commit. Subjects made by `git commit --fixup`, `--squash` and `git revert`
(`fixup! ...`, `squash! ...`, `Revert "..."`) are exempt - they refer to an earlier commit.

## What a `needs-fix` comment means

- **Required checks failed** - re-run the failed jobs or push a fix.
- **Rebasing onto `main` failed with a conflict** - rebase onto `main` yourself and resolve it.
- **Gitea refused the merge** - fix the cause Gitea names.
- **What this pull request would land on main is invalid** - fix the title, the `## Commit`
  section, or with `keep-commits` the commits.

Then remove `needs-fix`.
