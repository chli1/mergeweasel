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
- **`prio`** puts the pull request ahead of every other in the queue. It is meant for a hotfix that
  must reach `main` at once, and needs `automerge` as well: see [`prio`](#prio).

## The commit message

By default mergeweasel squashes the pull request into one commit. The pull request's **title**
becomes the commit's subject, with the pull request's number appended as Gitea's own squash
suggestion does, and the **`## Commit` section** of its description becomes the commit's body.
[Configuration](configuration.md#the-subject) lists the exact rules.

The scope - `api` in `fix(api): ...` - is optional. Unless the repository says otherwise, any scope
is accepted. A repository can restrict the scopes to a fixed list in its `mergeweasel.toml`
([configuration](configuration.md#scopes)); a title with any other scope is then rejected, and the
message lists the scopes the repository allows.

The examples below use the default forms. A repository can choose other forms in its
`mergeweasel.toml` - with an issue reference, or with a Jira key such as `PROJ-12`
([configuration](configuration.md#the-subject)); the message for a title that fits none lists the
repository's forms.

Valid titles, and what pull request #80 lands as with each:

```
feat: add dark mode                 feat: add dark mode (#80)
fix(api): retry on 409              fix(api): retry on 409 (#80)
feat!: drop the v1 endpoints        feat!: drop the v1 endpoints (#80)
chore: tidy the README (#80)        chore: tidy the README (#80) - the number is not doubled
```

Link the issue a pull request resolves in its description, for example with `Closes #12`.

Rejected titles:

```
feat(): add dark mode               an empty scope - leave out the parentheses
feat(my ui): add dark mode          a scope with whitespace
feat(domian): add dark mode         a scope missing from commits.scopes, if the repository sets it
add dark mode                       no type
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
[configuration](configuration.md#the-line-length) and must not contain a `Co-authored-by:` or
"Generated with" line.

Editing the title, the description or the labels runs the commit check again by itself.
Re-running a failed check job by hand works too: the check reads the pull request as it is when the
job runs, not as it was when the job first started, so the re-run judges the fixed text.

## What releases

After the merge, the queue releases if the repository is set up for it
([onboarding](onboarding.md#releases)). The commit types decide whether a merge releases: breaking
changes (`!`) and the releasing types do, the others do not;
[configuration](configuration.md#types-and-releases) lists the releasing types and how a
repository changes them. With SemVer versions, the default, they also decide the increase: `!`
raises the major version, a type of `release.minor_types` the minor version, and a type of
`release.patch_types` the patch version.

The release notes list the description of each releasing commit. Under a breaking change they also
show its `## Commit` section, so say there what users must change.

## `keep-commits`

Every commit of the pull request lands as it is, without an appended number. So every commit must
follow the subject rule above, and none may be a merge commit. With the default forms, a commit
either has no reference - `feat: add dark mode` - or ends in its pull request's number. A commit
ending in an issue's number, `fix: repair b (#12)`, is rejected with
`PR #12 is not this pull request`: by default `(#N)` names the pull request. A repository whose
commits reference issues uses the placeholder `{pull_or_issue}`
([configuration](configuration.md#the-subject)).

Subjects made by `git commit --fixup`, `git commit --squash` and `git revert` (`fixup! ...`,
`squash! ...`, `Revert "..."`) are exempt, because they refer to an earlier commit.

## `prio`

The queue normally works in a plain order: a pull request that is up to date with `main` - usually
the one whose checks are running - goes first, then the lowest number. When production is broken,
waiting behind that order is pointless. Add `prio` next to `automerge`, and the next pass takes the
`prio` pull request before any other: it rebases it if it is behind, waits for its checks and
merges it.

The pull request that was in flight is not touched. Its checks run on, and once the `prio` pull
request has landed, it is behind `main` and gets rebased and checked again - the price of the
priority.

`prio` changes only the order. The pull request still needs `automerge`, its approvals and green
checks, and gets `needs-fix` like any other when it cannot land. Several `prio` pull requests follow
the plain order among themselves. Set the label at any time, before or after `automerge`; a label
change wakes the queue.

The priority holds for the queue only, not for the runners. The checks of a `prio` pull request get
no priority there: when every runner is busy - with the pull request it overtook, or with jobs of
other repositories - its jobs wait until one is free. If even that wait is too long, cancel the
other runs by hand in the repository's *Actions* tab.

## What a `needs-fix` comment means

- **Required checks failed:** re-run the failed jobs or push a fix.
- **Rebasing onto `main` failed with a conflict:** rebase onto `main` (the default branch the
  comment names) yourself and resolve the conflict.
- **Gitea refused the merge:** fix the cause Gitea names.
- **What this pull request would land on main is invalid:** fix the title or the `## Commit`
  section, or with `keep-commits` the commits. An unknown scope shows up here too: use one of the
  scopes the message lists, or leave the scope out.
- **`mergeweasel.toml` is invalid:** the pull request changes `mergeweasel.toml`, and the changed
  file cannot be read - a value of the wrong type, say. The comment names the
  problem. Fix the file on the branch.

Then remove `needs-fix`.
