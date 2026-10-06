"""What may land on main: the subject rule and the squash commit message.

Shared by `mergeweasel check-commits` and the queue pass, so the two cannot disagree. The rule:
docs/design.md, "What lands on main".
"""
import dataclasses
import re

# @commitlint/config-conventional's types, so release tools read them with their defaults.
TYPES = ("feat", "fix", "perf", "refactor", "style", "docs", "test", "build", "ci", "chore", "revert")
FORMAT = "<type>[(<scope>)][!]: <description> (#<issue>)"
# The shape only: the type is matched loosely and checked against its list afterwards, so that an
# unknown one is named instead of failing the whole pattern.
SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<breaking>!)?: (?P<description>\S.*?) "
                     r"\((?P<reference>#(?P<issue>\d+)|PR #(?P<pull>\d+))\)$")
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


def subject_problem(subject):
    return f"subject must read '{FORMAT}', or end in '(PR #<pr>)' without an issue, got: {subject!r}"


def check_subject(subject, scopes=None):
    """scopes: the allowed ones, None for any (mergeweasel.toml's commits.scopes)."""
    if EXEMPT.match(subject):
        return None
    match = SUBJECT.match(subject)
    if not match:
        return subject_problem(subject)
    if match["type"] not in TYPES:
        return f"unknown type {match['type']!r}, expected one of: {', '.join(TYPES)}"
    scope = match["scope"]
    if scope is not None and (not scope or re.search(r"\s", scope)):
        return f"scope must be non-empty and without whitespace, got {scope!r}; leave out the parentheses for no scope"
    if scope is not None and scopes is not None and scope not in scopes:
        allowed = f"expected one of: {', '.join(scopes)}" if scopes else "this repository allows no scope"
        return f"unknown scope {scope!r}, {allowed}"
    return None


@dataclasses.dataclass(frozen=True)
class Subject:
    type: str
    scope: str | None
    breaking: bool
    description: str
    # "#N" or "PR #N", as written.
    reference: str


def parse_subject(subject):
    """The parts of a subject the rule accepts; None for any other, the exempt ones included."""
    if EXEMPT.match(subject) or check_subject(subject):
        return None
    match = SUBJECT.match(subject)
    return Subject(match["type"], match["scope"], bool(match["breaking"]), match["description"], match["reference"])


def referenced_issues(subjects):
    """The distinct issue numbers the subjects reference - the ones check_reference needs to know.

    Stripped as squash_message strips the title, so a title's reference is looked up as it is checked.
    """
    matches = (SUBJECT.match(subject.strip()) for subject in subjects)
    return {int(match["issue"]) for match in matches if match and match["issue"] is not None}


def known_issues(subjects, is_issue):
    """The referenced numbers that are issues, each looked up once: a lookup costs a request."""
    return {number for number in referenced_issues(subjects) if is_issue(number)}


def check_reference(subject, known_issues, pr_number, scopes=None):
    problem = check_subject(subject, scopes)
    match = SUBJECT.match(subject)
    if problem or not match:
        return problem
    issue, pull = match["issue"], match["pull"]
    if issue is not None and int(issue) not in known_issues:
        # Gitea answers 404 for an issue the token may not read, as for a missing one.
        return f"#{issue} is not an issue of this repository, or the token cannot read its issues"
    if pull is not None and int(pull) != int(pr_number):
        return f"PR #{pull} is not this pull request (#{pr_number})"
    return None


@dataclasses.dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    parent_count: int


def commit_problems(commits, known_issues, pr_number, scopes=None):
    """A keep-commits pull request: every commit lands as it is, so every commit is checked."""
    problems = []
    for commit in commits:
        if commit.parent_count > 1:
            problems.append(f"{commit.sha[:7]}: {MERGE_REJECTED}")
            continue
        problem = check_reference(commit.subject, known_issues, pr_number, scopes)
        if problem:
            problems.append(f"{commit.sha[:7]}: {problem}")
    return problems


def commit_section(pr_body):
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
    headings = [index for index, line in enumerate(lines)
                if outside_fences[index] and SECTION_HEADING.match(line)]
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
    return section, line_problems(section)


def line_problems(section):
    problems = []
    for number, line in enumerate(section.split("\n"), start=1):
        if len(line) > MAX_LINE and re.search(r"\s", line.strip()):
            problems.append(f"'## Commit' line {number} is {len(line)} characters, wrap at {MAX_LINE}")
        if ATTRIBUTION.match(line):
            problems.append(f"'## Commit' line {number} is an attribution line: {line!r}")
    return problems


@dataclasses.dataclass(frozen=True)
class SquashMessage:
    subject: str
    body: str
    # Empty when the message may land.
    problems: tuple


def squash_message(title, pr_body, known_issues, pr_number, scopes=None):
    """The squash commit a pull request lands as: its title, then its `## Commit` section."""
    subject = (title or "").strip()
    body, problems = commit_section(pr_body)
    # No fixup!/squash!/Revert exemption: those inherit a branch commit's reference, a title has none.
    if EXEMPT.match(subject):
        title_problem = subject_problem(subject)
    else:
        title_problem = check_reference(subject, known_issues, pr_number, scopes)
    if title_problem:
        problems = [f"PR title: {title_problem}", *problems]
    return SquashMessage(subject, body, tuple(problems))
