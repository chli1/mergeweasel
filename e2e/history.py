"""When a needs_this_commit test may run against the pinned image: once the pinned release contains the
commit that added its marker line, it contains the feature the test needs too.
"""
import inspect
import pathlib
import subprocess

MARKER = "pytest.mark.needs_this_commit"
UNCOMMITTED = "0" * 40


def git(directory, *arguments):
    return subprocess.run(["git", *arguments], cwd=directory, capture_output=True, text=True)


def unreleased(test, revision):
    """Why `test` may not run against the release built from `revision`, or None if it may.

    Unknown counts as unreleased: a skip shows its reason, a false red would block every pull request.
    """
    path = pathlib.Path(inspect.getsourcefile(test))
    lines, start = inspect.getsourcelines(test)
    number = start + next(offset for offset, line in enumerate(lines) if MARKER in line)
    blame = git(path.parent, "blame", "--porcelain", "-L", f"{number},{number}", "--", path.name)
    if blame.returncode != 0:
        return f"no history for {path.name}:{number}: {blame.stderr.strip()}"
    commit = blame.stdout.split()[0]
    if commit == UNCOMMITTED:
        return f"its marker in {path.name}:{number} is not committed yet"
    contained = git(path.parent, "merge-base", "--is-ancestor", commit, revision).returncode
    if contained == 0:
        return None
    if contained == 1:
        return f"the pinned release {revision[:12]} does not contain {commit[:12]}, which added its marker"
    return f"{revision[:12]} or {commit[:12]} is not in this checkout's history"
