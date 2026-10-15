"""What may land on main: the subject rule and the squash commit message.

Shared by `mergeweasel check-commits` and the queue pass, so the two cannot disagree. The rule:
docs/design.md, "What lands on main".
"""

import dataclasses
import re

from mergeweasel import subject_template

# @commitlint/config-conventional's types, so release tools read them with their defaults.
TYPES = ("feat", "fix", "perf", "refactor", "style", "docs", "test", "build", "ci", "chore", "revert")
# The default of mergeweasel.toml's commits.subject: a squash fits the first form through the appended pull
# reference; the second lets keep-commits commits land without one - their number is unknown when they are written.
DEFAULT_SUBJECT = ("{conventional} {description} (#{pull})", "{conventional} {description}")
# `git commit --fixup`/`--autosquash` prepend "fixup!"/"squash!" to the
# original subject, and `git revert` produces `Revert "<original subject>"`.
# None of the three can carry a reference of their own - they inherit the
# original commit's - so they are exempt from the format instead of forcing
# every fixup/revert workflow to hand-edit the generated subject.
EXEMPT = re.compile(r'^(fixup!|squash!)\s+\S|^Revert ".*"$')
REVERT = re.compile(r'^Revert "(?P<original>.*)"$')
# main stays linear. Gitea's fast-forward-only merge would carry a merge commit
# from the PR branch into main unchanged, so it is rejected whatever its subject.
MERGE_REJECTED = "merge commits are not allowed - rebase onto the base branch instead"

SECTION_HEADING = re.compile(r"^## Commit[ \t]*$", re.MULTILINE)
# A heading of level 1 or 2 ends the section; a level 3 heading stays inside it.
SECTION_END = re.compile(r"^#{1,2}\s")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Inside a fenced code block a `#` line is code, not a heading.
FENCE = re.compile(r"^(```|~~~)")
MAX_LINE = 72
# A line that starts with them - after an emoji, say - not a sentence that mentions them.
ATTRIBUTION = re.compile(r"^co-authored-by:|^\W*generated with\b", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class CommitRules:
    """The rule every subject is held to: the forms it may take and what its parts may name."""

    # The subject templates, in the configured order: the first that matches decides.
    templates: tuple
    # None: any scope passes; empty: none does.
    scopes: tuple | None
    # None: any project passes.
    projects: tuple | None
    # None: any type passes.
    types: tuple | None = TYPES
    # The longest `## Commit` line that contains whitespace.
    max_line: int = MAX_LINE
    # Squash only: the subject is the title with " (#<pr>)" appended, unless it ends in it already.
    append_pull_reference: bool = True


DEFAULT_COMMIT_RULES = CommitRules(tuple(subject_template.compile(text) for text in DEFAULT_SUBJECT), None, None)


def subject_problem(subject, rules):
    forms = " or ".join(f"'{template.describe()}'" for template in rules.templates)
    return f"subject must read {forms}, got: {subject!r}"


def first_match(subject, rules=DEFAULT_COMMIT_RULES):
    """The subject's parts by the first template it takes the form of; None for none."""
    for template in rules.templates:
        parts = template.match(subject)
        if parts is not None:
            return parts
    return None


def check_subject(subject, rules=DEFAULT_COMMIT_RULES):
    if EXEMPT.match(subject):
        return None
    parts = first_match(subject, rules)
    if parts is None:
        return subject_problem(subject, rules)
    if parts.type is not None and rules.types is not None and parts.type not in rules.types:
        return f"unknown type {parts.type!r}, expected one of: {', '.join(rules.types)}"
    scope = parts.scope
    if scope is not None and (not scope or re.search(r"\s", scope)):
        return f"scope must be non-empty and without whitespace, got {scope!r}; leave out the parentheses for no scope"
    if scope is not None and rules.scopes is not None and scope not in rules.scopes:
        allowed = f"expected one of: {', '.join(rules.scopes)}" if rules.scopes else "this repository allows no scope"
        return f"unknown scope {scope!r}, {allowed}"
    if parts.key is not None and rules.projects is not None and parts.project not in rules.projects:
        return f"unknown project {parts.project!r} in {parts.key}, expected one of: {', '.join(rules.projects)}"
    return None


def parse_subject(subject, rules=DEFAULT_COMMIT_RULES):
    """The parts of a subject the rule accepts; None for any other, the exempt ones included."""
    if EXEMPT.match(subject) or check_subject(subject, rules):
        return None
    return first_match(subject, rules)


def referenced_issues(subjects, rules=DEFAULT_COMMIT_RULES):
    """The distinct issue numbers the subjects reference - the ones check_reference needs to know.

    Stripped as squash_message strips the title, so a title's reference is looked up as it is checked.
    """
    found = (first_match(subject.strip(), rules) for subject in subjects if not EXEMPT.match(subject.strip()))
    numbers = set()
    for parts in found:
        if parts and parts.issue is not None:
            numbers.add(int(parts.issue))
        if parts and parts.pull_or_issue is not None:
            numbers.add(int(parts.pull_or_issue))
    return numbers


def known_issues(subjects, is_issue, rules=DEFAULT_COMMIT_RULES):
    """The referenced numbers that are issues, each looked up once: a lookup costs a request."""
    return {number for number in referenced_issues(subjects, rules) if is_issue(number)}


def check_reference(subject, known_issues, pr_number, rules=DEFAULT_COMMIT_RULES):
    problem = check_subject(subject, rules)
    if problem or EXEMPT.match(subject):
        return problem
    parts = first_match(subject, rules)
    if parts.issue is not None and int(parts.issue) not in known_issues:
        # Gitea answers 404 for an issue the token may not read, as for a missing one.
        return f"#{parts.issue} is not an issue of this repository, or the token cannot read its issues"
    if parts.pull is not None and int(parts.pull) != int(pr_number):
        return f"PR #{parts.pull} is not this pull request (#{pr_number})"
    if (
        parts.pull_or_issue is not None
        and int(parts.pull_or_issue) != int(pr_number)
        and int(parts.pull_or_issue) not in known_issues
    ):
        return f"#{parts.pull_or_issue} is neither this pull request nor an issue of this repository"
    return None


@dataclasses.dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    parent_count: int


def commit_problems(commits, known_issues, pr_number, rules=DEFAULT_COMMIT_RULES):
    """A keep-commits pull request: every commit lands as it is, so every commit is checked."""
    problems = []
    for commit in commits:
        if commit.parent_count > 1:
            problems.append(f"{commit.sha[:7]}: {MERGE_REJECTED}")
            continue
        problem = check_reference(commit.subject, known_issues, pr_number, rules)
        if problem:
            problems.append(f"{commit.sha[:7]}: {problem}")
    return problems


def commit_section(pr_body, max_line=MAX_LINE):
    """The text of the PR body's `## Commit` section and its problems; "" without a section."""
    text = HTML_COMMENT.sub("", (pr_body or "").replace("\r\n", "\n"))
    if "<!--" in text:
        return "", ["the PR body has an unterminated HTML comment ('<!--' without '-->')"]
    lines = text.split("\n")
    outside_fences = []
    in_fence = False
    for line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
        outside_fences.append(not in_fence and not FENCE.match(line))
    headings = [index for index, line in enumerate(lines) if outside_fences[index] and SECTION_HEADING.match(line)]
    if len(headings) > 1:
        return "", [f"the PR body has {len(headings)} '## Commit' sections, expected at most one"]
    if not headings:
        return "", []
    section_lines = []
    for index in range(headings[0] + 1, len(lines)):
        if outside_fences[index] and SECTION_END.match(lines[index]):
            break
        section_lines.append(lines[index])
    while section_lines and not section_lines[0].strip():
        section_lines.pop(0)
    while section_lines and not section_lines[-1].strip():
        section_lines.pop()
    section = "\n".join(section_lines)
    return section, line_problems(section, max_line)


def line_problems(section, max_line=MAX_LINE):
    problems = []
    for number, line in enumerate(section.split("\n"), start=1):
        if len(line) > max_line and re.search(r"\s", line.strip()):
            problems.append(f"'## Commit' line {number} is {len(line)} characters, wrap at {max_line}")
        if ATTRIBUTION.match(line):
            problems.append(f"'## Commit' line {number} is an attribution line: {line!r}")
    return problems


@dataclasses.dataclass(frozen=True)
class SquashMessage:
    subject: str
    body: str
    # Empty when the message may land.
    problems: tuple


def squash_subject(title, pr_number, rules=DEFAULT_COMMIT_RULES):
    """What a squash lands as its subject: the stripped title, with the pull reference if the rules append it."""
    subject = (title or "").strip()
    reference = f" (#{pr_number})"
    if rules.append_pull_reference and not subject.endswith(reference):
        subject += reference
    return subject


def squash_message(title, pr_body, known_issues, pr_number, rules=DEFAULT_COMMIT_RULES):
    """The squash commit a pull request lands as: its title - with the pull reference appended if the rules
    say so -, then its `## Commit` section."""
    subject = squash_subject(title, pr_number, rules)
    body, problems = commit_section(pr_body, rules.max_line)
    # No fixup!/squash!/Revert exemption: those inherit a branch commit's reference, a title has none.
    if EXEMPT.match(subject):
        title_problem = subject_problem(subject, rules)
    else:
        title_problem = check_reference(subject, known_issues, pr_number, rules)
    if title_problem:
        problems = [f"PR title: {title_problem}", *problems]
    return SquashMessage(subject, body, tuple(problems))
