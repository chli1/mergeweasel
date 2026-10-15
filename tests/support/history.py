"""When a marked scenario may run with the newest release: once the release contains the commit that
added its marker line, it contains what the marker stands for too - the new image's feature, or the
break with the old workflow files.
"""

import inspect
import pathlib
import subprocess
from collections.abc import Callable

UNCOMMITTED = "0" * 40


def git(directory: pathlib.Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], cwd=directory, capture_output=True, text=True)


def unreleased(test: Callable[..., object], revision: str, marker: str) -> str | None:
    """Why `test`, marked with `marker`, may not run with the release built from `revision`, or None if it may.

    Unknown counts as unreleased: a skip shows its reason, a false red would block every pull request.
    """
    source = inspect.getsourcefile(test)
    # A test function comes from a source file.
    assert source is not None
    path = pathlib.Path(source)
    lines, start = inspect.getsourcelines(test)
    number = start + next(offset for offset, line in enumerate(lines) if f"pytest.mark.{marker}" in line)
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
        return f"the newest release {revision[:12]} does not contain {commit[:12]}, which added its marker"
    return f"{revision[:12]} or {commit[:12]} is not in this checkout's history"
