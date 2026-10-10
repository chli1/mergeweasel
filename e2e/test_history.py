"""When a needs_this_commit test may run against the released image. No Gitea needed."""
import importlib.util
import subprocess

import history

SCENARIO = "import pytest\n\n\n{marker}def test_new():\n    pass\n"
MARKED = SCENARIO.format(marker="@pytest.mark.needs_this_commit\n")


def git(path, *arguments):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *arguments], cwd=path,
                          check=True, capture_output=True, text=True).stdout.strip()


def commit(path, text):
    (path / "test_scenario.py").write_text(text)
    git(path, "add", "test_scenario.py")
    git(path, "commit", "--quiet", "--message", "change")
    return git(path, "rev-parse", "HEAD")


def scenario(path):
    spec = importlib.util.spec_from_file_location(f"scenario_{path.name}", path / "test_scenario.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.test_new


def repository(tmp_path):
    git(tmp_path, "init", "--quiet")
    return commit(tmp_path, SCENARIO.format(marker=""))


def test_a_release_with_the_markers_commit_runs_it(tmp_path):
    repository(tmp_path)
    commit(tmp_path, MARKED)
    later = commit(tmp_path, MARKED + "\n# later\n")

    assert history.unreleased(scenario(tmp_path), later) is None


def test_a_release_before_the_markers_commit_skips_it(tmp_path):
    before = repository(tmp_path)
    marked = commit(tmp_path, MARKED)

    assert marked[:12] in history.unreleased(scenario(tmp_path), before)


def test_touching_the_marker_line_moves_the_key(tmp_path):
    repository(tmp_path)
    commit(tmp_path, MARKED)
    between = commit(tmp_path, MARKED + "\n# later\n")
    commit(tmp_path, MARKED.replace("needs_this_commit", "needs_this_commit  # touched") + "\n# later\n")

    assert history.unreleased(scenario(tmp_path), between) is not None


def test_an_uncommitted_marker_skips_it(tmp_path):
    revision = repository(tmp_path)
    (tmp_path / "test_scenario.py").write_text(MARKED)

    assert "not committed" in history.unreleased(scenario(tmp_path), revision)


def test_a_revision_outside_the_history_skips_it(tmp_path):
    repository(tmp_path)
    commit(tmp_path, MARKED)

    assert "1234567890ab" in history.unreleased(scenario(tmp_path), "1234567890ab" * 3 + "1234")
