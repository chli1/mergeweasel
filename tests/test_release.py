import base64
import datetime
import io
import json
import urllib.error
from unittest import mock

import pytest

from mergeweasel import release
from mergeweasel.gitea import Gitea

HISTORY_PATH = "/commits?sha=main&stat=false&verification=false&files=false&limit=50&page={}"


@pytest.mark.parametrize("message, kind, line", [
    ("feat: add a (#1)", release.FEATURE, "add a (#1)"),
    ("feat(cli): add a (#1)", release.FEATURE, "cli: add a (#1)"),
    ("fix: repair b (#2)", release.FIX, "repair b (#2)"),
    ("perf: speed c up (PR #3)", release.FIX, "speed c up (PR #3)"),
    ("revert: drop d (#4)", release.FIX, "drop d (#4)"),
    ('Revert "feat: add a (#1)"', release.FIX, 'Revert "feat: add a (#1)"'),
    ("docs!: rename the e flag (#5)", release.BREAKING, "rename the e flag (#5)"),
])
def test_change_classifies_releasing_commits(message, kind, line):
    assert release.change(message) == release.Change(kind, line, "")


@pytest.mark.parametrize("message", ["docs: explain (#1)", "ci: pin (#1)", "chore: tidy (#1)", "build(deps): bump (#16)",
                                     "test: cover (#1)", "refactor: split (#1)", "style: wrap (#1)", "wip", "fixup! feat: a (#1)"])
def test_other_commits_release_nothing(message):
    assert release.change(message) is None


def test_breaking_body_drops_co_authored_by_trailers():
    message = "feat!: approve differently (#8)\n\n- copy the new workflow\n\nCo-authored-by: a <a@example.com>\n"
    assert release.change(message) == release.Change(release.BREAKING, "approve differently (#8)", "- copy the new workflow")


def test_only_breaking_changes_keep_their_body():
    assert release.change("fix: repair (#2)\n\nwhy it broke").body == ""


@pytest.mark.parametrize("kinds, expected", [
    ([release.FIX], (1, 2, 4)),
    ([release.FIX, release.FEATURE], (1, 3, 0)),
    ([release.FEATURE, release.BREAKING, release.FIX], (2, 0, 0)),
    ([], None),
])
def test_next_version_takes_the_largest_bump(kinds, expected):
    changes = [release.Change(kind, "x", "") for kind in kinds]
    assert release.next_version((1, 2, 3), changes) == expected


def test_breaking_in_zero_major_is_a_major_bump():
    assert release.next_version((0, 2, 0), [release.Change(release.BREAKING, "x", "")]) == (1, 0, 0)


def test_notes_group_the_changes_in_order():
    changes = [release.Change(release.FIX, "repair b (#2)", ""),
               release.Change(release.BREAKING, "approve differently (#8)", "- copy the new workflow\n- then the image"),
               release.Change(release.FEATURE, "add a (#1)", ""),
               release.Change(release.FIX, "speed c up (#3)", "")]
    assert release.notes(changes) == (
        "## Breaking changes\n\n"
        "- approve differently (#8)\n\n"
        "  - copy the new workflow\n"
        "  - then the image\n\n"
        "## Features\n\n"
        "- add a (#1)\n\n"
        "## Fixes\n\n"
        "- repair b (#2)\n"
        "- speed c up (#3)\n")


# --- the release step ---

def sha(number):
    return f"{number:040x}"


def history_commit(number, message):
    return {"sha": sha(number), "commit": {"message": message}}


def tag_ref(name, number, kind="commit"):
    return {"ref": f"refs/tags/{name}", "object": {"type": kind, "sha": sha(number)}}


MISSING = urllib.error.HTTPError("url", 404, "Not Found", hdrs=None, fp=None)
CALVER = '[release]\ntag = "{YY}.{MM}.{DD}.{count}+{sha}"\n'
NOW = datetime.datetime(2026, 10, 5, 14, 0, tzinfo=datetime.timezone.utc)


def fake_gitea(refs=(tag_ref("v1.0.0", 1),), history=(history_commit(1, "feat: start (#1)"),), post=None, answers=None,
               config=None, count=None):
    """refs: GET git/refs/tags; history: main's commits, newest first, served 50 per page;
    config: mergeweasel.toml on main, None for none; count: X-Total-Count of the commits API."""
    gitea = mock.Mock(spec=Gitea)
    pages = {HISTORY_PATH.format(page): list(history[(page - 1) * 50:page * 50]) for page in range(1, len(history) // 50 + 2)}
    config_answer = MISSING if config is None else {"type": "file", "content": base64.b64encode(config.encode()).decode()}
    known = {("GET", "/git/refs/tags"): list(refs), ("GET", "/contents/mergeweasel.toml?ref=main"): config_answer,
             **{("GET", path): batch for path, batch in pages.items()}, **(answers or {})}

    def call(method, path, body=None):
        if method == "POST" and post is not None:
            raise post
        answer = known.get((method, path))
        if isinstance(answer, Exception):
            raise answer
        return answer

    gitea.call.side_effect = call
    gitea.total_count.return_value = count
    return gitea


def posts(gitea):
    return [call.args for call in gitea.call.call_args_list if call.args[0] == "POST"]


def reads(gitea):
    return [call.args[1] for call in gitea.call.call_args_list if call.args[0] == "GET"]


def test_releases_the_commits_since_the_nearest_tag_on_main(capsys):
    gitea = fake_gitea(history=[history_commit(3, "feat: add a (#1)"), history_commit(2, "fix: repair b (#2)"),
                                history_commit(1, "feat: start (#1)")])
    assert release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea) == [("POST", "/releases", {
        "tag_name": "v1.1.0", "target_commitish": sha(3), "name": "v1.1.0",
        "body": "## Features\n\n- add a (#1)\n\n## Fixes\n\n- repair b (#2)\n"})]
    assert "released v1.1.0" in capsys.readouterr().out


def test_notes_list_the_commits_oldest_first():
    gitea = fake_gitea(history=[history_commit(3, "fix: second (#2)"), history_commit(2, "fix: first (#1)"),
                                history_commit(1, "feat: start (#1)")])
    release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea)[0][2]["body"] == "## Fixes\n\n- first (#1)\n- second (#2)\n"


def test_a_higher_tag_off_main_is_not_the_base():
    # A maintenance branch's v2.0.1 does not decide main's next version.
    gitea = fake_gitea(refs=[tag_ref("v1.0.0", 1), tag_ref("v2.0.1", 99)],
                       history=[history_commit(2, "fix: repair (#2)"), history_commit(1, "feat: start (#1)")])
    release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea)[0][2]["tag_name"] == "v1.0.1"


def test_the_highest_of_several_tags_on_one_commit_is_the_base():
    gitea = fake_gitea(refs=[tag_ref("v1.9.0", 1), tag_ref("v1.10.0", 1), tag_ref("latest", 1)],
                       history=[history_commit(2, "fix: repair (#2)"), history_commit(1, "feat: start (#1)")])
    release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea)[0][2]["tag_name"] == "v1.10.1"


def test_the_base_is_found_on_a_later_page():
    history = [history_commit(number, "fix: repair (#2)") for number in range(80, 1, -1)] + [history_commit(1, "feat: start (#1)")]
    gitea = fake_gitea(history=history)
    release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea)[0][2]["tag_name"] == "v1.0.1"
    assert HISTORY_PATH.format(2) in reads(gitea)


def test_an_annotated_tag_is_followed_to_its_commit():
    gitea = fake_gitea(refs=[tag_ref("v1.0.0", 77, kind="tag")],
                       history=[history_commit(2, "fix: repair (#2)"), history_commit(1, "feat: start (#1)")],
                       answers={("GET", f"/git/tags/{sha(77)}"): {"object": {"sha": sha(1)}}})
    release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea)[0][2]["tag_name"] == "v1.0.1"


def test_nothing_releasing_releases_nothing(capsys):
    gitea = fake_gitea(history=[history_commit(2, "docs: explain (#1)"), history_commit(1, "feat: start (#1)")])
    assert release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea) == []
    assert "nothing to release since v1.0.0" in capsys.readouterr().out


def test_tag_on_mains_head_releases_nothing():
    gitea = fake_gitea()
    assert release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea) == []


@pytest.mark.parametrize("refs", [[], [tag_ref("latest", 1)]])
def test_no_version_tag_reads_no_history(capsys, refs):
    gitea = fake_gitea(refs=refs)
    assert release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea) == [] and reads(gitea) == ["/contents/mergeweasel.toml?ref=main", "/git/refs/tags"]
    assert "no tag matching v{major}.{minor}.{patch} to bump from" in capsys.readouterr().out


def test_no_tags_at_all_answers_404():
    missing = urllib.error.HTTPError("url", 404, "Not Found", hdrs=None, fp=None)
    assert release.run(fake_gitea(answers={("GET", "/git/refs/tags"): missing}), dry_run=False, base_branch="main")


def test_no_version_tag_on_main_is_an_error(capsys):
    gitea = fake_gitea(refs=[tag_ref("v1.0.0", 99)], history=[history_commit(2, "fix: repair (#2)")])
    assert not release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea) == []
    assert "no tag matching v{major}.{minor}.{patch} on main" in capsys.readouterr().err


def test_the_walk_stops_at_its_limit(capsys):
    history = [history_commit(number, "fix: repair (#2)") for number in range(release.MAX_COMMITS + 50, 0, -1)]
    gitea = fake_gitea(refs=[tag_ref("v1.0.0", 99999)], history=history)
    assert not release.run(gitea, dry_run=False, base_branch="main")
    assert HISTORY_PATH.format(release.MAX_COMMITS // 50 + 1) not in reads(gitea)
    assert f"within the last {release.MAX_COMMITS} commits" in capsys.readouterr().err


def test_a_next_version_tagged_elsewhere_is_an_error(capsys):
    gitea = fake_gitea(refs=[tag_ref("v1.0.0", 1), tag_ref("v1.0.1", 99)],
                       history=[history_commit(2, "fix: repair (#2)"), history_commit(1, "feat: start (#1)")])
    assert not release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea) == []
    assert "v1.0.1 exists on another commit" in capsys.readouterr().err


def test_dry_run_reports_and_writes_nothing(capsys):
    gitea = fake_gitea(history=[history_commit(2, "fix: repair (#2)"), history_commit(1, "feat: start (#1)")])
    assert release.run(gitea, dry_run=True, base_branch="main")
    assert posts(gitea) == []
    output = capsys.readouterr().out
    assert "would release v1.0.1" in output and "- repair (#2)" in output


def test_the_base_branch_is_released_with_its_own_file(capsys):
    gitea = fake_gitea(answers={
        ("GET", "/contents/mergeweasel.toml?ref=trunk"): MISSING,
        ("GET", HISTORY_PATH.replace("sha=main", "sha=trunk").format(1)): [
            history_commit(2, "fix: repair (#2)"), history_commit(1, "feat: start (#1)")]})
    assert release.run(gitea, dry_run=True, base_branch="trunk")
    assert "would release v1.0.1" in capsys.readouterr().out
    assert not any("main" in path for path in reads(gitea))


def test_a_tag_created_meanwhile_is_not_an_error(capsys):
    conflict = urllib.error.HTTPError("url", 409, "Conflict", hdrs=None,
                                      fp=io.BytesIO(json.dumps({"message": "tag already exists"}).encode()))
    gitea = fake_gitea(history=[history_commit(2, "fix: repair (#2)"), history_commit(1, "feat: start (#1)")], post=conflict)
    assert release.run(gitea, dry_run=False, base_branch="main")
    assert "v1.0.1 exists already" in capsys.readouterr().out


def test_api_error_is_a_release_error(capsys):
    boom = urllib.error.HTTPError("url", 500, "boom", hdrs=None, fp=None)
    gitea = fake_gitea(history=[history_commit(2, "fix: repair (#2)"), history_commit(1, "feat: start (#1)")], post=boom)
    assert not release.run(gitea, dry_run=False, base_branch="main")
    assert "release failed" in capsys.readouterr().err


def test_any_unexpected_answer_is_a_release_error(capsys):
    # A tag ref without its object: whatever breaks, the pass must still run.
    gitea = fake_gitea(refs=[{"ref": "refs/tags/v1.0.0", "object": None}])
    assert not release.run(gitea, dry_run=False, base_branch="main")
    assert "release failed" in capsys.readouterr().err


COUNT_PATH = "/commits?sha={}&stat=false&verification=false&files=false&limit=1"


def test_a_custom_semver_template_bumps_from_its_own_tags():
    gitea = fake_gitea(config='[release]\ntag = "release-{major}.{minor}.{patch}"\n',
                       refs=[tag_ref("release-1.4.0", 1), tag_ref("v9.0.0", 1)],
                       history=[history_commit(2, "fix: repair (#2)"), history_commit(1, "feat: start (#1)")])
    assert release.run(gitea, dry_run=False, base_branch="main")
    assert posts(gitea)[0][2]["tag_name"] == "release-1.4.1"


def test_the_default_template_reads_no_count():
    gitea = fake_gitea(history=[history_commit(2, "fix: repair (#2)"), history_commit(1, "feat: start (#1)")])
    release.run(gitea, dry_run=False, base_branch="main")
    gitea.total_count.assert_not_called()


def test_calver_without_a_tag_of_its_shape_releases_mains_head(capsys):
    # The old v1.0.0 is no release under the new template; the head alone decides.
    gitea = fake_gitea(config=CALVER, count=412,
                       history=[history_commit(3, "fix: repair (#2)"), history_commit(2, "feat: older (#1)"),
                                history_commit(1, "feat: start (#1)")])
    assert release.run(gitea, dry_run=False, base_branch="main", now=NOW)
    assert posts(gitea) == [("POST", "/releases", {
        "tag_name": "26.10.5.412+0000000", "target_commitish": sha(3), "name": "26.10.5.412+0000000",
        "body": "## Fixes\n\n- repair (#2)\n"})]
    gitea.total_count.assert_called_once_with(COUNT_PATH.format(sha(3)))
    assert HISTORY_PATH.format(2) not in reads(gitea)
    assert "released 26.10.5.412+0000000" in capsys.readouterr().out


def test_calver_without_a_tag_waits_for_a_releasing_head(capsys):
    gitea = fake_gitea(config=CALVER, count=3, refs=[],
                       history=[history_commit(3, "ci: switch to CalVer (#1)"), history_commit(2, "feat: older (#1)")])
    assert release.run(gitea, dry_run=False, base_branch="main", now=NOW)
    assert posts(gitea) == []
    assert "nothing to release on 0000000" in capsys.readouterr().out


def test_calver_walks_back_to_its_previous_release():
    gitea = fake_gitea(config=CALVER, count=412, refs=[tag_ref("26.10.4.410+0000001", 1)],
                       history=[history_commit(3, "feat: add a (#1)"), history_commit(2, "fix: repair b (#2)"),
                                history_commit(1, "feat: start (#1)")])
    assert release.run(gitea, dry_run=False, base_branch="main", now=NOW)
    assert posts(gitea)[0][2]["tag_name"] == "26.10.5.412+0000000"
    assert posts(gitea)[0][2]["body"] == "## Features\n\n- add a (#1)\n\n## Fixes\n\n- repair b (#2)\n"


def test_a_calver_name_taken_already_is_an_error(capsys):
    gitea = fake_gitea(config='[release]\ntag = "{YY}.{MM}.{DD}"\n', refs=[tag_ref("26.10.5", 1)],
                       history=[history_commit(2, "fix: repair (#2)"), history_commit(1, "feat: start (#1)")])
    assert not release.run(gitea, dry_run=False, base_branch="main", now=NOW)
    assert posts(gitea) == []
    assert "26.10.5 exists on another commit" in capsys.readouterr().err


def test_dry_run_names_the_calver_release(capsys):
    gitea = fake_gitea(config=CALVER, count=412, history=[history_commit(3, "fix: repair (#2)")])
    assert release.run(gitea, dry_run=True, base_branch="main", now=NOW)
    assert posts(gitea) == []
    assert "would release 26.10.5.412+0000000" in capsys.readouterr().out


def test_an_invalid_configuration_is_a_release_error(capsys):
    gitea = fake_gitea(config='[release]\ntag = "{yy}"\n')
    assert not release.run(gitea, dry_run=False, base_branch="main")
    assert "release failed: mergeweasel.toml: release.tag: unknown placeholder {yy}" in capsys.readouterr().err


def test_without_a_given_time_the_date_is_utc():
    gitea = fake_gitea(config='[release]\ntag = "{YYYY}.{MM}.{DD}.{count}"\n', count=5,
                       history=[history_commit(2, "fix: repair (#2)")])
    before = datetime.datetime.now(datetime.timezone.utc)
    release.run(gitea, dry_run=False, base_branch="main")
    after = datetime.datetime.now(datetime.timezone.utc)
    dates = {f"{moment.year}.{moment.month}.{moment.day}.5" for moment in (before, after)}
    assert posts(gitea)[0][2]["tag_name"] in dates
