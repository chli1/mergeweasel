"""When a marked scenario may run with the newest release. No Gitea needed."""

import importlib.util
import pathlib
import subprocess
from collections.abc import Callable

import history

SCENARIO = "import pytest\n\n\n{marker}def test_new():\n    pass\n"
MARKER = "needs_new_image"
MARKED = SCENARIO.format(marker=f"@pytest.mark.{MARKER}\n")


def git(path: pathlib.Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit(path: pathlib.Path, text: str) -> str:
    (path / "test_scenario.py").write_text(text)
    git(path, "add", "test_scenario.py")
    git(path, "commit", "--quiet", "--message", "change")
    return git(path, "rev-parse", "HEAD")


def scenario(path: pathlib.Path) -> Callable[[], None]:
    spec = importlib.util.spec_from_file_location(f"scenario_{path.name}", path / "test_scenario.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    test: Callable[[], None] = module.test_new
    return test


def skip_reason(path: pathlib.Path, revision: str) -> str:
    """Why the scenario may not run with the release built from revision; fails the test if it may."""
    reason = history.unreleased(scenario(path), revision, MARKER)
    assert reason is not None
    return reason


def repository(tmp_path: pathlib.Path) -> str:
    git(tmp_path, "init", "--quiet")
    return commit(tmp_path, SCENARIO.format(marker=""))


def test_a_release_with_the_markers_commit_runs_it(tmp_path: pathlib.Path) -> None:
    repository(tmp_path)
    commit(tmp_path, MARKED)
    later = commit(tmp_path, MARKED + "\n# later\n")

    assert history.unreleased(scenario(tmp_path), later, MARKER) is None


def test_a_release_before_the_markers_commit_skips_it(tmp_path: pathlib.Path) -> None:
    before = repository(tmp_path)
    marked = commit(tmp_path, MARKED)

    assert marked[:12] in skip_reason(tmp_path, before)


def test_touching_the_marker_line_moves_the_key(tmp_path: pathlib.Path) -> None:
    repository(tmp_path)
    commit(tmp_path, MARKED)
    between = commit(tmp_path, MARKED + "\n# later\n")
    commit(tmp_path, MARKED.replace(MARKER, f"{MARKER}  # touched") + "\n# later\n")

    assert history.unreleased(scenario(tmp_path), between, MARKER) is not None


def test_an_uncommitted_marker_skips_it(tmp_path: pathlib.Path) -> None:
    revision = repository(tmp_path)
    (tmp_path / "test_scenario.py").write_text(MARKED)

    assert "not committed" in skip_reason(tmp_path, revision)


def test_a_revision_outside_the_history_skips_it(tmp_path: pathlib.Path) -> None:
    repository(tmp_path)
    commit(tmp_path, MARKED)

    assert "1234567890ab" in skip_reason(tmp_path, "1234567890ab" * 3 + "1234")


def test_each_marker_counts_from_its_own_commit(tmp_path: pathlib.Path) -> None:
    repository(tmp_path)
    commit(tmp_path, MARKED)
    released = commit(tmp_path, MARKED + "\n# later\n")
    both = SCENARIO.format(marker=f"@pytest.mark.{MARKER}\n@pytest.mark.breaks_old_workflows\n")
    commit(tmp_path, both + "\n# later\n")

    assert history.unreleased(scenario(tmp_path), released, MARKER) is None
    assert history.unreleased(scenario(tmp_path), released, "breaks_old_workflows") is not None
