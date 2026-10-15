import pytest

from mergeweasel import subject_template
from mergeweasel.subject_template import Parts, TemplateError

DEFAULT = ("{conventional} {description} (#{issue})", "{conventional} {description} (PR #{pull})")


def parts(subject: str, text: str = DEFAULT[0]) -> Parts | None:
    return subject_template.compile(text).match(subject)


def matched(subject: str, text: str = DEFAULT[0]) -> Parts:
    """The subject's parts; fails the test if it does not take the template's form."""
    found = parts(subject, text)
    assert found is not None
    return found


# --- matching ---


def test_the_default_reads_type_scope_breaking_description_and_issue() -> None:
    assert parts("feat(ui)!: add dark mode (#12)") == Parts("feat", "ui", True, "add dark mode", issue="12")


def test_the_pr_form_reads_the_pull() -> None:
    assert parts("chore: tidy (PR #31)", DEFAULT[1]) == Parts("chore", None, False, "tidy", pull="31")


def test_the_last_reference_shape_is_the_reference() -> None:
    assert parts("feat: add x (#1) (#12)") == Parts("feat", None, False, "add x (#1)", issue="12")


def test_a_key_between_type_and_description() -> None:
    assert parts("feat(ui): PROJ-12 add dark mode", "{conventional} {key} {description}") == Parts(
        "feat", "ui", False, "add dark mode", key="PROJ-12"
    )


def test_a_key_as_prefix() -> None:
    assert parts("[PROJ_2-7] fix: repair", "[{key}] {conventional} {description}") == Parts(
        "fix", None, False, "repair", key="PROJ_2-7"
    )


def test_the_project_is_the_part_before_the_dash() -> None:
    assert matched("[PROJ_2-7] fix: repair", "[{key}] {conventional} {description}").project == "PROJ_2"
    assert matched("feat: x (#1)").project is None


def test_a_lowercase_key_is_no_key() -> None:
    assert parts("[proj-12] fix: repair", "[{key}] {conventional} {description}") is None


def test_regex_characters_in_the_text_match_literally() -> None:
    template = "{conventional} {description} [*.{key}]"
    assert parts("fix: repair [*.OPS-3]", template) == Parts("fix", None, False, "repair", key="OPS-3")
    assert parts("fix: repair [x.OPS-3]", template) is None


def test_the_bare_template_takes_everything_as_description() -> None:
    assert parts("feat: add x (#12)", "{conventional} {description}") == Parts("feat", None, False, "add x (#12)")


def test_a_type_free_form_reads_no_type() -> None:
    assert parts("fix the login (#12)", "{description} (#{pull_or_issue})") == Parts(
        None, None, False, "fix the login", pull_or_issue="12"
    )


def test_a_type_free_form_takes_a_type_as_description() -> None:
    assert parts("feat!: add x", "{description}") == Parts(None, None, False, "feat!: add x")


@pytest.mark.parametrize(
    "subject",
    [
        "fix: typo(#99)",
        "fix: (#99)",
        "fix:  x (#99)",
        "fix x (#99)",
        " fix: x (#99)",
        "fix: x (#XX)",
        "feat!(ui): x (#1)",
    ],
)
def test_the_default_rejects(subject: str) -> None:
    assert parts(subject) is None


# --- rendering and describing ---


@pytest.mark.parametrize(
    "text, subject, line",
    [
        (DEFAULT[0], "feat(ui): add dark mode (#12)", "ui: add dark mode (#12)"),
        (DEFAULT[1], "fix: retry on 409 (PR #31)", "retry on 409 (PR #31)"),
        ("{conventional} {key} {description}", "feat(ui)!: PROJ-12 add dark mode", "ui: PROJ-12 add dark mode"),
        ("[{key}] {conventional} {description}", "[PROJ-12] feat: add dark mode", "[PROJ-12] add dark mode"),
        ("[{key}] {conventional} {description}", "[PROJ-12] feat(ui): add dark mode", "[PROJ-12] ui: add dark mode"),
        ("[{key}] {description}", "[PROJ-12] add dark mode", "[PROJ-12] add dark mode"),
    ],
)
def test_the_line_is_the_subject_without_type_and_breaking_marker(text: str, subject: str, line: str) -> None:
    assert matched(subject, text).line() == line


def test_describe_writes_the_documentations_notation() -> None:
    assert subject_template.compile(DEFAULT[0]).describe() == "<type>[(<scope>)][!]: <description> (#<issue>)"


def test_describe_a_type_free_form() -> None:
    assert subject_template.compile("{description} (#{issue})").describe() == "<description> (#<issue>)"


def test_describe_names_key_and_pull() -> None:
    assert subject_template.compile("[{key}] {conventional} {description}").describe() == (
        "[<key>] <type>[(<scope>)][!]: <description>"
    )
    assert subject_template.compile(DEFAULT[1]).describe() == "<type>[(<scope>)][!]: <description> (PR #<pr>)"


# --- rules ---


@pytest.mark.parametrize(
    "text, message",
    [
        ("{conventional} {description} {", "a { or } outside a placeholder"),
        ("{conventional} {description} ({ticket})", "unknown placeholder {ticket}"),
        ("{conventional} {description} {description}", "{description} appears twice"),
        ("{conventional} (#{issue})", "needs {description}"),
        ("{description} {conventional} x", "{conventional} must come before {description}"),
        ("{conventional} {description} (#{issue}) [{key}]", "{issue} and {key} - at most one reference"),
        ("{conventional}{description}", "{conventional} must be followed by a space"),
        ("{conventional} {description}{key}", "{description}{key} cannot be told apart - put text between them"),
        (" {conventional} {description}", "must not start or end with whitespace"),
        ("{conventional} {description} ", "must not start or end with whitespace"),
    ],
)
def test_invalid_templates(text: str, message: str) -> None:
    with pytest.raises(TemplateError) as error:
        subject_template.compile(text)
    assert str(error.value) == message


def test_pull_or_issue_is_read() -> None:
    read = matched("feat: x (#80)", "{conventional} {description} (#{pull_or_issue})")
    assert read.pull_or_issue == "80"
    assert read.issue is None and read.pull is None


def test_pull_or_issue_counts_as_a_reference() -> None:
    with pytest.raises(TemplateError, match="at most one reference"):
        subject_template.compile("{conventional} {description} (#{pull_or_issue}) (#{issue})")


def test_pull_or_issue_is_described() -> None:
    assert (
        subject_template.compile("{conventional} {description} (#{pull_or_issue})").describe()
        == "<type>[(<scope>)][!]: <description> (#<pr-or-issue>)"
    )
