import pytest

from mergeweasel import commit_rule as commit_message
from mergeweasel.commit_rule import Commit, check_reference, check_subject, commit_problems, commit_section, squash_message

ISSUES = {99, 209}


# --- subject and reference ---

def test_accepts_every_type_without_a_scope():
    for commit_type in commit_message.TYPES:
        assert check_subject(f"{commit_type}: bind commits to issues (#99)") is None


def test_accepts_any_scope():
    for scope in ("ui", "domain", "persistence/r2dbc", "ui,rest", "-", "API_v2"):
        assert check_subject(f"fix({scope}): bind commits to issues (#99)") is None


def test_accepts_a_breaking_marker_with_and_without_a_scope():
    assert check_subject("feat!: build multi-stage rockets (#75)") is None
    assert check_subject("feat(ui)!: introduce a dark theme (#206)") is None


def test_accepts_a_pr_suffix():
    assert check_subject("chore: scaffold the project (PR #30)") is None


def test_rejects_a_breaking_marker_before_the_scope():
    assert check_subject("feat!(ui): introduce a dark theme (#206)").startswith("subject must read")


def test_rejects_the_old_prefix_form():
    assert check_subject("#99: bind commits to issues").startswith("subject must read")
    assert check_subject("PR #30: scaffold the project").startswith("subject must read")


def test_rejects_a_bare_subject():
    assert check_subject("fix typo in README").startswith("subject must read")


def test_rejects_a_missing_suffix():
    assert check_subject("fix: typo in README").startswith("subject must read")


def test_rejects_a_suffix_without_its_space():
    assert check_subject("fix: typo in README(#99)").startswith("subject must read")


def test_rejects_an_empty_description():
    assert check_subject("fix: (#99)").startswith("subject must read")
    assert check_subject("fix:  (#99)").startswith("subject must read")


def test_rejects_an_upper_case_type():
    assert check_subject("Fix: typo in README (#99)").startswith("subject must read")


def test_rejects_an_unknown_type_by_name():
    problem = check_subject("feature: dark theme (#206)")
    assert problem.startswith("unknown type 'feature'")
    assert "feat, fix, perf" in problem


def test_rejects_empty_and_whitespace_scopes():
    for scope in ("", " ", "my ui", "\tui"):
        problem = check_subject(f"fix({scope}): x (#99)")
        assert problem.startswith(f"scope must be non-empty and without whitespace, got {scope!r}")


def test_rejects_xx_going_forward():
    assert check_subject("fix: typo in README (#XX)").startswith("subject must read")


def test_accepts_fixup_squash_and_revert_subjects():
    assert check_subject("fixup! fix: bind commits (#99)") is None
    assert check_subject("squash! fix: bind commits (#99)") is None
    assert check_subject('Revert "fix: bind commits (#99)"') is None


def test_reference_accepts_a_known_issue():
    assert check_reference("ci: bind commits (#99)", {99, 100}, 101) is None


def test_reference_rejects_an_unknown_issue():
    assert "not an issue" in check_reference("ci: bind commits (#4711)", {99, 100}, 101)


def test_reference_accepts_this_prs_own_number():
    assert check_reference("ci: bind commits (PR #101)", {99, 100}, 101) is None


def test_reference_rejects_a_foreign_pr_number():
    assert "this pull request" in check_reference("ci: bind commits (PR #30)", {99, 100}, 101)


def test_reference_reports_the_format_before_the_reference():
    assert check_reference("feature: x (#4711)", {99}, 101).startswith("unknown type")


def test_reference_lets_exempt_subjects_pass():
    assert check_reference('Revert "ci: bind commits (#4711)"', {99}, 101) is None


# --- keep-commits: every commit ---

def test_commit_problems_accepts_conforming_commits():
    assert commit_problems([Commit("a" * 40, "ci: one (#99)", 1), Commit("b" * 40, "ci: two (#209)", 1)], ISSUES, 213) == []


def test_commit_problems_names_each_bad_commit():
    problems = commit_problems([Commit("a" * 40, "wip", 1), Commit("b" * 40, "ci: x (#4711)", 1)], ISSUES, 213)
    assert len(problems) == 2
    assert problems[0].startswith("aaaaaaa: ") and "subject must read" in problems[0]
    assert problems[1].startswith("bbbbbbb: ") and "not an issue" in problems[1]


def test_commit_problems_rejects_a_merge_commit_whatever_its_subject():
    problems = commit_problems([Commit("c" * 40, "ci: merge main (#99)", 2)], ISSUES, 213)
    assert problems == ["ccccccc: " + commit_message.MERGE_REJECTED]


# --- the ## Commit section ---

def test_section_is_the_text_up_to_the_next_level_two_heading():
    body = "Closes #209\n\n## Commit\n\nFirst line.\nSecond line.\n\n## Test plan\n\n- ran it\n"
    assert commit_section(body) == ("First line.\nSecond line.", [])


def test_section_runs_to_the_end_of_the_body():
    assert commit_section("## Commit\nOnly line.") == ("Only line.", [])


def test_section_ends_at_a_level_one_heading_but_not_at_a_level_three_one():
    body = "## Commit\nIntro.\n\n### Details\nMore.\n# Appendix\nNot in it."
    assert commit_section(body) == ("Intro.\n\n### Details\nMore.", [])


def test_no_section_means_no_body():
    assert commit_section("Closes #209\n\nJust a description.") == ("", [])


def test_empty_or_missing_pr_body_means_no_body():
    assert commit_section("") == ("", [])
    assert commit_section(None) == ("", [])


def test_similar_headings_are_not_the_section():
    assert commit_section("## Commits\nNo.\n### Commit\nNo either.") == ("", [])


def test_crlf_from_the_web_editor_becomes_lf():
    assert commit_section("## Commit\r\nOne.\r\nTwo.\r\n") == ("One.\nTwo.", [])


def test_html_comments_never_land():
    template = "## Commit\n\n<!-- Becomes the body of the squash commit.\nWrap at 72. -->\n"
    assert commit_section(template) == ("", [])
    assert commit_section("## Commit\nKept. <!-- dropped -->\n") == ("Kept. ", [])


def test_a_heading_inside_an_html_comment_does_not_count():
    assert commit_section("<!--\n## Commit\nhidden\n-->\n## Commit\nReal.") == ("Real.", [])


def test_two_sections_are_rejected():
    section, problems = commit_section("## Commit\nOne.\n## Commit\nTwo.")
    assert section == ""
    assert problems == ["the PR body has 2 '## Commit' sections, expected at most one"]


def test_a_line_over_72_characters_is_rejected():
    long_line = "word " * 15
    section, problems = commit_section(f"## Commit\nShort.\n{long_line.strip()}")
    assert problems == [f"'## Commit' line 2 is {len(long_line.strip())} characters, wrap at 72"]


def test_a_long_line_without_whitespace_is_allowed():
    url = "https://git.example.com/org/repo/src/branch/main/" + "x" * 40
    assert commit_section(f"## Commit\nSee:\n{url}")[1] == []


def test_attribution_lines_are_rejected():
    section, problems = commit_section(
        "## Commit\nBody.\n\nCo-authored-by: Someone <a@b>\n🤖 Generated with [Claude Code](https://claude.com)")
    assert problems == [
        "'## Commit' line 3 is an attribution line: 'Co-authored-by: Someone <a@b>'",
        "'## Commit' line 4 is an attribution line: '🤖 Generated with [Claude Code](https://claude.com)'",
    ]


def test_generated_with_inside_a_sentence_is_fine():
    assert commit_section("## Commit\nThe file is generated with Gradle.")[1] == []


# --- the squash message ---

def test_squash_message_takes_title_and_section():
    message = squash_message("ci: squash by default (#209)", "## Commit\nWhy.", ISSUES, 213)
    assert message == commit_message.SquashMessage("ci: squash by default (#209)", "Why.", ())


def test_squash_message_strips_the_title():
    assert squash_message("  ci: squash (#209)  ", None, ISSUES, 213).subject == "ci: squash (#209)"


def test_squash_message_reports_title_and_section_problems():
    message = squash_message("squash by default", "## Commit\nA.\n## Commit\nB.", ISSUES, 213)
    assert message.problems[0].startswith("PR title: subject must read")
    assert message.problems[1] == "the PR body has 2 '## Commit' sections, expected at most one"


def test_squash_message_resolves_the_pr_fallback_against_this_pr():
    assert squash_message("chore: tidy (PR #213)", "", ISSUES, 213).problems == ()
    assert "this pull request" in squash_message("chore: tidy (PR #30)", "", ISSUES, 213).problems[0]


# --- final review fixes ---

def test_a_fenced_code_block_does_not_end_the_section():
    body = "## Commit\nRun:\n```\n# reset the db\nrm x\n```\nAfter.\n## Notes\nno"
    assert commit_section(body) == ("Run:\n```\n# reset the db\nrm x\n```\nAfter.", [])


def test_a_heading_inside_a_fence_is_not_a_second_section():
    body = "## Commit\nExample:\n~~~\n## Commit\n~~~\nDone."
    assert commit_section(body) == ("Example:\n~~~\n## Commit\n~~~\nDone.", [])


def test_an_unterminated_html_comment_is_rejected():
    section, problems = commit_section("## Commit\nA <!-- note\nline two")
    assert problems == ["the PR body has an unterminated HTML comment ('<!--' without '-->')"]


def test_a_title_may_not_use_the_commit_exemptions():
    for title in ('Revert "anything"', "squash! ci: x (#99)", "fixup! ci: x (#99)"):
        assert squash_message(title, "", ISSUES, 213).problems[0].startswith("PR title: subject must read")


def test_parse_subject_returns_its_parts():
    assert commit_message.parse_subject("feat(cli)!: drop the old flag (#8)") == commit_message.Subject(
        type="feat", scope="cli", breaking=True, description="drop the old flag", reference="#8")
    assert commit_message.parse_subject("fix: retry on 409 (PR #12)") == commit_message.Subject(
        type="fix", scope=None, breaking=False, description="retry on 409", reference="PR #12")


@pytest.mark.parametrize("subject", ["wip", "feat(): x (#1)", "feature: x (#1)", 'Revert "feat: x (#1)"',
                                     "fixup! feat: x (#1)"])
def test_parse_subject_rejects_what_the_rule_rejects_or_exempts(subject):
    assert commit_message.parse_subject(subject) is None


def test_revert_pattern_captures_the_original_subject():
    assert commit_message.REVERT.match('Revert "feat: x (#1)"')["original"] == "feat: x (#1)"
