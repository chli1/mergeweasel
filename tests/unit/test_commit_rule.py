import dataclasses
from collections.abc import Iterable
from typing import Any

import pytest

from mergeweasel import commit_rule, subject_template
from mergeweasel import commit_rule as commit_message
from mergeweasel.commit_rule import (
    Commit,
    check_reference,
    check_subject,
    commit_problems,
    commit_section,
    referenced_issues,
    squash_message,
)
from mergeweasel.subject_template import Parts

ISSUES = {99, 209}


def rules(subject: Iterable[str] | None = None, **changes: Any) -> commit_rule.CommitRules:
    """The default rules with some changed; subject: the template texts."""
    if subject is not None:
        changes["templates"] = tuple(subject_template.compile(text) for text in subject)
    return dataclasses.replace(commit_rule.DEFAULT_COMMIT_RULES, **changes)


def rejection(subject: str, rules: commit_rule.CommitRules = commit_rule.DEFAULT_COMMIT_RULES) -> str:
    """check_subject's problem with the subject; fails the test if it finds none."""
    problem = check_subject(subject, rules)
    assert problem is not None
    return problem


def reference_rejection(
    subject: str,
    known_issues: set[int],
    pr_number: int,
    rules: commit_rule.CommitRules = commit_rule.DEFAULT_COMMIT_RULES,
) -> str:
    """check_reference's problem with the subject; fails the test if it finds none."""
    problem = check_reference(subject, known_issues, pr_number, rules)
    assert problem is not None
    return problem


# mergeweasel's own forms: an issue, or this pull request in its own words; nothing appended.
ISSUE_OR_PULL = rules(
    ["{conventional} {description} (#{issue})", "{conventional} {description} (PR #{pull})"],
    append_pull_reference=False,
)


# --- subject and reference ---


def test_accepts_every_type_without_a_scope() -> None:
    for commit_type in commit_message.TYPES:
        assert check_subject(f"{commit_type}: bind commits to issues (#99)") is None


def test_accepts_any_scope() -> None:
    for scope in ("ui", "domain", "persistence/r2dbc", "ui,rest", "-", "API_v2"):
        assert check_subject(f"fix({scope}): bind commits to issues (#99)") is None


def test_configured_scopes_reject_any_other() -> None:
    domain_ui = rules(scopes=("domain", "ui"))
    assert check_subject("feat(ui): add dark mode (#99)", domain_ui) is None
    assert check_subject("feat(domian): add dark mode (#99)", domain_ui) == (
        "unknown scope 'domian', expected one of: domain, ui"
    )
    assert check_subject("feat: add dark mode (#99)", domain_ui) is None


def test_no_configured_scope_rejects_every_scope() -> None:
    assert check_subject("feat(ui): add dark mode (#99)", rules(scopes=())) == (
        "unknown scope 'ui', this repository allows no scope"
    )
    assert check_subject("feat: add dark mode (#99)", rules(scopes=())) is None


def test_configured_types_reject_any_other() -> None:
    custom = rules(types=("feature", "fix"))
    assert check_subject("feature: add dark mode (#99)", custom) is None
    assert check_subject("feat: add dark mode (#99)", custom) == "unknown type 'feat', expected one of: feature, fix"


def test_without_a_type_list_any_type_passes() -> None:
    assert check_subject("yolo: add dark mode (#99)", rules(types=None)) is None


def test_a_type_free_subject_passes_type_free_forms() -> None:
    free = rules(["{description} (#{pull_or_issue})"])
    assert check_reference("fix the login (#12)", {12}, 80, free) is None
    assert squash_message("fix the login", "", set(), 80, free).problems == ()


def test_configured_scopes_reach_titles_and_commits() -> None:
    message = squash_message("feat(domian): x (#99)", "", ISSUES, 1, rules(scopes=("domain",)))
    assert message.problems == ("PR title: unknown scope 'domian', expected one of: domain",)
    commits = [Commit("a" * 40, "feat(domian): x (#99)", 1)]
    assert commit_problems(commits, ISSUES, 1, rules(scopes=("domain",))) == [
        "aaaaaaa: unknown scope 'domian', expected one of: domain"
    ]


def test_accepts_a_breaking_marker_with_and_without_a_scope() -> None:
    assert check_subject("feat!: build multi-stage rockets (#75)") is None
    assert check_subject("feat(ui)!: introduce a dark theme (#206)") is None


def test_accepts_a_pr_suffix() -> None:
    assert check_subject("chore: scaffold the project (PR #30)") is None


def test_rejects_a_breaking_marker_before_the_scope() -> None:
    assert rejection("feat!(ui): introduce a dark theme (#206)").startswith("subject must read")


def test_rejects_the_old_prefix_form() -> None:
    assert rejection("#99: bind commits to issues").startswith("subject must read")
    assert rejection("PR #30: scaffold the project").startswith("subject must read")


def test_rejects_a_bare_subject() -> None:
    assert rejection("fix typo in README").startswith("subject must read")


def test_rejects_a_missing_suffix() -> None:
    assert rejection("fix: typo in README", ISSUE_OR_PULL).startswith("subject must read")


def test_rejects_a_suffix_without_its_space() -> None:
    assert rejection("fix: typo in README(#99)", ISSUE_OR_PULL).startswith("subject must read")


def test_rejects_an_empty_description() -> None:
    assert rejection("fix: (#99)", ISSUE_OR_PULL).startswith("subject must read")
    assert rejection("fix:  (#99)", ISSUE_OR_PULL).startswith("subject must read")


def test_rejects_an_upper_case_type() -> None:
    assert rejection("Fix: typo in README (#99)").startswith("subject must read")


def test_rejects_an_unknown_type_by_name() -> None:
    problem = rejection("feature: dark theme (#206)")
    assert problem.startswith("unknown type 'feature'")
    assert "feat, fix, perf" in problem


def test_rejects_empty_and_whitespace_scopes() -> None:
    for scope in ("", " ", "my ui", "\tui"):
        problem = rejection(f"fix({scope}): x (#99)")
        assert problem.startswith(f"scope must be non-empty and without whitespace, got {scope!r}")


def test_rejects_xx_going_forward() -> None:
    assert rejection("fix: typo in README (#XX)", ISSUE_OR_PULL).startswith("subject must read")


def test_accepts_fixup_squash_and_revert_subjects() -> None:
    assert check_subject("fixup! fix: bind commits (#99)") is None
    assert check_subject("squash! fix: bind commits (#99)") is None
    assert check_subject('Revert "fix: bind commits (#99)"') is None


def test_reference_accepts_a_known_issue() -> None:
    assert check_reference("ci: bind commits (#99)", {99, 100}, 101, ISSUE_OR_PULL) is None


def test_reference_rejects_an_unknown_issue() -> None:
    assert "not an issue" in reference_rejection("ci: bind commits (#4711)", {99, 100}, 101, ISSUE_OR_PULL)


def test_reference_accepts_this_prs_own_number() -> None:
    assert check_reference("ci: bind commits (PR #101)", {99, 100}, 101, ISSUE_OR_PULL) is None


def test_reference_rejects_a_foreign_pr_number() -> None:
    assert "this pull request" in reference_rejection("ci: bind commits (PR #30)", {99, 100}, 101, ISSUE_OR_PULL)


def test_reference_reports_the_format_before_the_reference() -> None:
    assert reference_rejection("feature: x (#4711)", {99}, 101).startswith("unknown type")


def test_referenced_issues_are_the_distinct_issue_references() -> None:
    subjects = [
        "ci: one (#99)",
        "ci: two (#99)",
        "ci: three (PR #101)",
        "wip #5",
        'Revert "ci: x (#7)"',
        "fixup! ci: y (#8)",
        " fix: four (#209) ",
    ]
    assert referenced_issues(subjects, ISSUE_OR_PULL) == {99, 209}


def test_reference_lets_exempt_subjects_pass() -> None:
    assert check_reference('Revert "ci: bind commits (#4711)"', {99}, 101) is None


# --- keep-commits: every commit ---


def test_commit_problems_accepts_conforming_commits() -> None:
    assert (
        commit_problems(
            [Commit("a" * 40, "ci: one (#99)", 1), Commit("b" * 40, "ci: two (#209)", 1)], ISSUES, 213, ISSUE_OR_PULL
        )
        == []
    )


def test_commit_problems_names_each_bad_commit() -> None:
    problems = commit_problems(
        [Commit("a" * 40, "wip", 1), Commit("b" * 40, "ci: x (#4711)", 1)], ISSUES, 213, ISSUE_OR_PULL
    )
    assert len(problems) == 2
    assert problems[0].startswith("aaaaaaa: ") and "subject must read" in problems[0]
    assert problems[1].startswith("bbbbbbb: ") and "not an issue" in problems[1]


def test_commit_problems_rejects_a_merge_commit_whatever_its_subject() -> None:
    problems = commit_problems([Commit("c" * 40, "ci: merge main (#99)", 2)], ISSUES, 213)
    assert problems == ["ccccccc: " + commit_message.MERGE_REJECTED]


# --- the ## Commit section ---


def test_section_is_the_text_up_to_the_next_level_two_heading() -> None:
    body = "Closes #209\n\n## Commit\n\nFirst line.\nSecond line.\n\n## Test plan\n\n- ran it\n"
    assert commit_section(body) == ("First line.\nSecond line.", [])


def test_section_runs_to_the_end_of_the_body() -> None:
    assert commit_section("## Commit\nOnly line.") == ("Only line.", [])


def test_section_ends_at_a_level_one_heading_but_not_at_a_level_three_one() -> None:
    body = "## Commit\nIntro.\n\n### Details\nMore.\n# Appendix\nNot in it."
    assert commit_section(body) == ("Intro.\n\n### Details\nMore.", [])


def test_no_section_means_no_body() -> None:
    assert commit_section("Closes #209\n\nJust a description.") == ("", [])


def test_empty_or_missing_pr_body_means_no_body() -> None:
    assert commit_section("") == ("", [])
    assert commit_section(None) == ("", [])


def test_similar_headings_are_not_the_section() -> None:
    assert commit_section("## Commits\nNo.\n### Commit\nNo either.") == ("", [])


def test_crlf_from_the_web_editor_becomes_lf() -> None:
    assert commit_section("## Commit\r\nOne.\r\nTwo.\r\n") == ("One.\nTwo.", [])


def test_html_comments_never_land() -> None:
    template = "## Commit\n\n<!-- Becomes the body of the squash commit.\nWrap at 72. -->\n"
    assert commit_section(template) == ("", [])
    assert commit_section("## Commit\nKept. <!-- dropped -->\n") == ("Kept. ", [])


def test_a_heading_inside_an_html_comment_does_not_count() -> None:
    assert commit_section("<!--\n## Commit\nhidden\n-->\n## Commit\nReal.") == ("Real.", [])


def test_two_sections_are_rejected() -> None:
    section, problems = commit_section("## Commit\nOne.\n## Commit\nTwo.")
    assert section == ""
    assert problems == ["the PR body has 2 '## Commit' sections, expected at most one"]


def test_a_line_over_72_characters_is_rejected() -> None:
    long_line = "word " * 15
    _section, problems = commit_section(f"## Commit\nShort.\n{long_line.strip()}")
    assert problems == [f"'## Commit' line 2 is {len(long_line.strip())} characters, wrap at 72"]


def test_a_long_line_without_whitespace_is_allowed() -> None:
    url = "https://git.example.com/org/repo/src/branch/main/" + "x" * 40
    assert commit_section(f"## Commit\nSee:\n{url}")[1] == []


LINE_OF_84 = ("word " * 17).strip()


def test_the_line_limit_is_a_parameter() -> None:
    assert commit_section(f"## Commit\n{LINE_OF_84}", 100)[1] == []
    assert commit_section(f"## Commit\n{LINE_OF_84}", 80)[1] == ["'## Commit' line 1 is 84 characters, wrap at 80"]


def test_the_squash_message_holds_the_body_to_the_rules_line_limit() -> None:
    body = f"## Commit\n{LINE_OF_84}"
    assert squash_message("feat: add a (#99)", body, ISSUES, 1, rules(max_line=100)).problems == ()
    assert squash_message("feat: add a (#99)", body, ISSUES, 1).problems == (
        "'## Commit' line 1 is 84 characters, wrap at 72",
    )


def test_attribution_lines_are_rejected() -> None:
    _section, problems = commit_section(
        "## Commit\nBody.\n\nCo-authored-by: Someone <a@b>\n🤖 Generated with [Claude Code](https://claude.com)"
    )
    assert problems == [
        "'## Commit' line 3 is an attribution line: 'Co-authored-by: Someone <a@b>'",
        "'## Commit' line 4 is an attribution line: '🤖 Generated with [Claude Code](https://claude.com)'",
    ]


def test_generated_with_inside_a_sentence_is_fine() -> None:
    assert commit_section("## Commit\nThe file is generated with Gradle.")[1] == []


# --- the squash message ---


def test_squash_message_takes_title_and_section() -> None:
    message = squash_message("ci: squash by default", "## Commit\nWhy.", ISSUES, 213)
    assert message == commit_message.SquashMessage("ci: squash by default (#213)", "Why.", ())


def test_squash_message_strips_the_title() -> None:
    assert squash_message("  ci: squash (#213)  ", None, ISSUES, 213).subject == "ci: squash (#213)"


def test_squash_message_reports_title_and_section_problems() -> None:
    message = squash_message("squash by default", "## Commit\nA.\n## Commit\nB.", ISSUES, 213)
    assert message.problems[0].startswith("PR title: subject must read")
    assert message.problems[1] == "the PR body has 2 '## Commit' sections, expected at most one"


def test_squash_message_resolves_the_pr_fallback_against_this_pr() -> None:
    assert squash_message("chore: tidy (PR #213)", "", ISSUES, 213, ISSUE_OR_PULL).problems == ()
    assert "this pull request" in squash_message("chore: tidy (PR #30)", "", ISSUES, 213, ISSUE_OR_PULL).problems[0]


# --- final review fixes ---


def test_a_fenced_code_block_does_not_end_the_section() -> None:
    body = "## Commit\nRun:\n```\n# reset the db\nrm x\n```\nAfter.\n## Notes\nno"
    assert commit_section(body) == ("Run:\n```\n# reset the db\nrm x\n```\nAfter.", [])


def test_a_heading_inside_a_fence_is_not_a_second_section() -> None:
    body = "## Commit\nExample:\n~~~\n## Commit\n~~~\nDone."
    assert commit_section(body) == ("Example:\n~~~\n## Commit\n~~~\nDone.", [])


def test_an_unterminated_html_comment_is_rejected() -> None:
    _section, problems = commit_section("## Commit\nA <!-- note\nline two")
    assert problems == ["the PR body has an unterminated HTML comment ('<!--' without '-->')"]


def test_a_title_may_not_use_the_commit_exemptions() -> None:
    for title in ('Revert "anything"', "squash! ci: x (#99)", "fixup! ci: x (#99)"):
        assert squash_message(title, "", ISSUES, 213).problems[0].startswith("PR title: subject must read")


def test_parse_subject_returns_its_parts() -> None:
    assert commit_message.parse_subject("feat(cli)!: drop the old flag (#8)") == Parts(
        "feat", "cli", True, "drop the old flag", pull="8"
    )
    assert commit_message.parse_subject("fix: retry on 409") == Parts("fix", None, False, "retry on 409")


@pytest.mark.parametrize(
    "subject", ["wip", "feat(): x (#1)", "feature: x (#1)", 'Revert "feat: x (#1)"', "fixup! feat: x (#1)"]
)
def test_parse_subject_rejects_what_the_rule_rejects_or_exempts(subject: str) -> None:
    assert commit_message.parse_subject(subject) is None


def test_revert_pattern_captures_the_original_subject() -> None:
    match = commit_message.REVERT.match('Revert "feat: x (#1)"')
    assert match is not None
    assert match["original"] == "feat: x (#1)"


# --- subject templates ---

KEYS = rules(subject=["[{key}] {conventional} {description}", "{conventional} {description} (PR #{pull})"])


def test_the_no_match_message_lists_the_default_forms() -> None:
    assert check_subject("add dark mode") == (
        "subject must read '<type>[(<scope>)][!]: <description> (#<pr>)' or "
        "'<type>[(<scope>)][!]: <description>', got: 'add dark mode'"
    )


def test_the_no_match_message_lists_the_configured_forms() -> None:
    assert check_subject("feat: add x (#1)", KEYS) == (
        "subject must read '[<key>] <type>[(<scope>)][!]: <description>' or "
        "'<type>[(<scope>)][!]: <description> (PR #<pr>)', got: 'feat: add x (#1)'"
    )


def test_a_key_template_accepts_any_project_without_a_list() -> None:
    assert check_subject("[ANY-1] feat: add x", KEYS) is None


def test_configured_projects_reject_any_other() -> None:
    projects = dataclasses.replace(KEYS, projects=("PROJ", "OPS"))
    assert check_subject("[OPS-4] fix: repair", projects) is None
    assert (
        check_subject("[PORJ-4] fix: repair", projects)
        == "unknown project 'PORJ' in PORJ-4, expected one of: PROJ, OPS"
    )


def test_the_first_matching_template_decides() -> None:
    optional = rules(subject=["{conventional} {description} (#{issue})", "{conventional} {description}"])
    assert reference_rejection("feat: add x (#4711)", {99}, 101, optional).startswith("#4711 is not an issue")
    assert check_reference("feat: add x", {99}, 101, optional) is None


def test_a_failing_first_match_is_not_rescued_by_a_later_template() -> None:
    both = rules(subject=["{conventional} {description} (#{issue})", "{conventional} {description}"])
    assert rejection("feature: add x (#1)", both).startswith("unknown type")


def test_an_issue_before_the_type_is_looked_up() -> None:
    prefix = rules(subject=["[#{issue}] {conventional} {description}"])
    assert referenced_issues(["[#99] feat: add x", "[#5] fix: y"], prefix) == {99, 5}
    assert reference_rejection("[#4711] feat: add x", {99}, 101, prefix).startswith("#4711 is not an issue")
    assert check_reference("[#99] feat: add x", {99}, 101, prefix) is None


def test_exempt_subjects_stay_exempt_under_a_custom_template() -> None:
    for subject in ('Revert "[PROJ-1] feat: x"', "fixup! [PROJ-1] feat: x", "squash! feat: x (#1)"):
        assert check_reference(subject, set(), 101, KEYS) is None
        assert commit_message.parse_subject(subject, KEYS) is None
    assert referenced_issues(['Revert "feat: x (#7)"', "fixup! feat: y (#8)"]) == set()


# --- {pull_or_issue} ---

EITHER = rules(["{conventional} {description} (#{pull_or_issue})"])


@pytest.mark.parametrize("subject", ["feat: x (#80)", "feat: x (#12)"])
def test_pull_or_issue_takes_the_pull_request_or_an_issue(subject: str) -> None:
    assert check_reference(subject, {12}, 80, EITHER) is None


def test_pull_or_issue_rejects_anything_else() -> None:
    assert (
        check_reference("feat: x (#4711)", {12}, 80, EITHER)
        == "#4711 is neither this pull request nor an issue of this repository"
    )


def test_pull_or_issue_numbers_are_looked_up() -> None:
    assert referenced_issues(["feat: x (#12)"], EITHER) == {12}


# --- the appended pull reference ---

APPEND = rules(["{conventional} {description} (#{pull})"], append_pull_reference=True)


@pytest.mark.parametrize(
    "title, subject",
    [
        ("feat: x", "feat: x (#80)"),
        ("  feat: x  ", "feat: x (#80)"),
        ("feat: x (#80)", "feat: x (#80)"),
        ("feat: x (#79)", "feat: x (#79) (#80)"),
    ],
)
def test_the_pull_reference_is_appended_once(title: str, subject: str) -> None:
    assert commit_rule.squash_subject(title, 80, APPEND) == subject


def test_without_the_append_the_title_is_the_subject() -> None:
    assert commit_rule.squash_subject(" feat: x ", 80, rules(append_pull_reference=False)) == "feat: x"


def test_the_squash_message_lands_and_checks_the_appended_subject() -> None:
    message = squash_message("feat: x", "", set(), 80, APPEND)
    assert (message.subject, message.problems) == ("feat: x (#80)", ())


def test_a_problem_quotes_the_appended_subject() -> None:
    message = squash_message("land it", "", set(), 80, APPEND)
    assert "got: 'land it (#80)'" in message.problems[0]


def test_by_default_kept_commits_land_with_their_own_pull_reference_or_none() -> None:
    commits = [Commit("a" * 40, "feat: x", 1), Commit("b" * 40, "test: y (#213)", 1)]
    assert commit_problems(commits, ISSUES, 213) == []
    assert "is not this pull request" in commit_problems([Commit("c" * 40, "feat: x (#99)", 1)], ISSUES, 213)[0]
