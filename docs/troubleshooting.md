# Troubleshooting

What to do when the queue does not behave as expected, for whoever runs mergeweasel in a
repository. Each entry starts from what you see - a queue that stays idle, or a message in the log
of a `mergeweasel-queue` run - and names the cause and the fix. Pull request authors find the
meaning of a `needs-fix` comment in [usage](usage.md#what-a-needs-fix-comment-means).

The first place to look is the log of the latest `mergeweasel-queue` run in the repository's
*Actions* tab. A run started by hand with `dry_run` checked shows the same decisions without
changing anything ([onboarding](onboarding.md#verify)).

## The queue does nothing

- **The jobs never start.** No runner carries the label the workflows ask for. Set
  `MERGEWEASEL_RUNNER_LABEL` to a label your runners have
  ([Actions variables](configuration.md#actions-variables)).
- **The queue waits forever.** Check that `main` has required status checks, that every required
  check reports on the pull request, and that the pull request is approved. Set `automerge` last,
  after the approval: an approval does not wake the queue ([usage](usage.md#the-labels)), so a pull
  request approved after `automerge` was set waits for the next event. Remove `automerge` and set
  it again to wake the queue.
- **The queue ignores a pull request.** Check its base: only pull requests against the default
  branch are queued. Check the label name against the `settings:` line at the top of the log
  ([Actions variables](configuration.md#actions-variables)).
- **`needs-fix` comes back.** The comment names the reason. Checks count only on the up-to-date
  head of the pull request: fix them there, then remove `needs-fix`.

## Messages of the pass

- **"cannot read the account of MERGEWEASEL_MERGER_TOKEN: ..."**, or of a token of
  `MERGEWEASEL_APPROVER_TOKENS`, named by its position. The message gives Gitea's reason: the token
  expired, was revoked, or lacks `read:user`. Create a new token with the scopes listed in
  [permissions](onboarding.md#permissions).
- **"token 2 of MERGEWEASEL_APPROVER_TOKENS belongs to alice again"**. Two approver tokens belong
  to the same account, and Gitea counts only one approval per account. Use a token of another
  account.
- **"labels missing in the repository and its organization: ..."**. A label the settings name does
  not exist. Create it, or correct the variable that names it
  ([Actions variables](configuration.md#actions-variables)).
- **"the account of MERGEWEASEL_MERGER_TOKEN has no login"**, or of an approver token. Gitea
  answered for the token without naming an account. Create a token of a regular user account.
- **"the label variables must name four different labels, not ..."**. Two of the four label
  variables name the same label. Give each role a label of its own.
- **"cannot read the organization's labels - not checked whether the labels exist"**. Not an error:
  the repository belongs to a user, the merger's token lacks `read:organization`, or a dry run used
  the run token. The pass goes on.
- **"merge deferred: Please try again later"**. Gitea was still checking whether the pull request
  can be merged; the next pass retries.
- **"mergeweasel: too few approver accounts to restore the approvals"**, as a `needs-fix` comment.
  A rebase dismissed more approvals than the approver accounts can restore. Add approver accounts
  to `MERGEWEASEL_APPROVER_TOKENS` (and to the approvals whitelist, if you use one), or approve
  again by hand. Then remove `needs-fix`.

## Messages of the release

A failed release fails the job at its end, but the pass runs anyway and lands what it can. The next
pass tries the release again, so nothing is lost once the cause is fixed.

- **"release: no tag matching v{major}.{minor}.{patch} to bump from, nothing released"**. Not a
  failure: a SemVer template needs a first tag set by hand
  ([SemVer and CalVer](configuration.md#semver-and-calver)). Tag a commit on `main`, for example
  `v0.1.0`.
- **"release failed: no tag matching v{major}.{minor}.{patch} on main"**. Tags of the template
  exist, but none on `main`'s history - only on another branch, say. Tag the commit on `main` that
  holds the current version. The variant "within the last 1000 commits of main" means the previous
  release tag lies further back than the search reaches; tag a recent commit with the current
  version.
- **"release failed: 2026.10.5 exists on another commit, 3f2a91c"**. The template gave a version
  that exists already - a second release on the same day with `{YYYY}.{MM}.{DD}`, say. Add `{count}`
  or `{sha}` to the template ([the release tag](configuration.md#the-release-tag)).
- **"release failed: mergeweasel.toml: ..."**. The file on `main` is invalid, and the message names
  the key. Fix it in a pull request - the queue still lands it.
- **"release failed: user should have a permission to write to a repo"**. The merger lacks write
  permission on the Releases unit - typically through a team that grants permissions per unit. Grant
  it as listed in [permissions](onboarding.md#permissions); the next pass releases.
- **"release failed: ..."** with another text. The message gives Gitea's reason.
- **A traceback, then "release failed: SomeError(...)".** A bug in mergeweasel's release step, not
  a problem of your setup. Report the bug with the traceback.
