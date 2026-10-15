"""The subject template: a form a commit's subject may take, and the parts it is read into.

Placeholders and rules: docs/configuration.md, "The subject".
"""

import dataclasses
import re

CONVENTIONAL, DESCRIPTION = "conventional", "description"
REFERENCES = ("issue", "pull", "pull_or_issue", "key")
# Placeholder -> (the regex of its value, how the documentation writes it). The type is matched
# loosely and checked against its list afterwards, so that an unknown one is named instead of
# failing the whole pattern.
PLACEHOLDERS = {
    CONVENTIONAL: (r"(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<breaking>!)?:", "<type>[(<scope>)][!]:"),
    DESCRIPTION: (r"(?P<description>\S.*?)", "<description>"),
    "issue": (r"(?P<issue>\d+)", "<issue>"),
    "pull": (r"(?P<pull>\d+)", "<pr>"),
    "pull_or_issue": (r"(?P<pull_or_issue>\d+)", "<pr-or-issue>"),
    "key": (r"(?P<key>[A-Z][A-Z0-9_]*-\d+)", "<key>"),
}
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


class TemplateError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Parts:
    # None where the template has no {conventional}.
    type: str | None
    scope: str | None
    breaking: bool
    description: str
    # As written; None where the template has no such placeholder.
    issue: str | None = None
    pull: str | None = None
    pull_or_issue: str | None = None
    key: str | None = None
    template: "Template | None" = dataclasses.field(default=None, compare=False, repr=False)

    @property
    def project(self) -> str | None:
        return self.key.rpartition("-")[0] if self.key else None

    def line(self) -> str:
        """The release notes' line: the subject through its template, without the type and `!`."""
        # Template.match sets it; the release notes only read parts it returned.
        assert self.template is not None
        return self.template.render(self)


@dataclasses.dataclass(frozen=True)
class Template:
    text: str
    # Text and placeholder names alternating, starting and ending with text (possibly empty).
    parts: tuple[str, ...]
    pattern: re.Pattern[str]

    @property
    def names(self) -> tuple[str, ...]:
        return self.parts[1::2]

    def match(self, subject: str) -> Parts | None:
        """The subject's parts, None if it does not take this form."""
        match = self.pattern.fullmatch(subject)
        if match is None:
            return None
        values = match.groupdict()
        return Parts(
            values.get("type"),
            values.get("scope"),
            bool(values.get("breaking")),
            values["description"],
            issue=values.get("issue"),
            pull=values.get("pull"),
            pull_or_issue=values.get("pull_or_issue"),
            key=values.get("key"),
            template=self,
        )

    def render(self, parts: Parts) -> str:
        rendered: list[str] = []
        for index, part in enumerate(self.parts):
            if index % 2 == 1:
                rendered.append(
                    (f"{parts.scope}:" if parts.scope else "") if part == CONVENTIONAL else getattr(parts, part)
                )
            elif index and self.parts[index - 1] == CONVENTIONAL and not parts.scope:
                # Without a scope {conventional} leaves nothing, and takes the space after it along.
                rendered.append(part[1:])
            else:
                rendered.append(part)
        return "".join(rendered)

    def describe(self) -> str:
        return "".join(part if index % 2 == 0 else PLACEHOLDERS[part][1] for index, part in enumerate(self.parts))


def compile(text: str) -> Template:
    parts: list[str] = []
    position = 0
    for match in PLACEHOLDER.finditer(text):
        parts += [text[position : match.start()], match[1]]
        position = match.end()
    parts.append(text[position:])
    texts, names = parts[0::2], parts[1::2]
    if any("{" in part or "}" in part for part in texts):
        raise TemplateError("a { or } outside a placeholder")
    for name in names:
        if name not in PLACEHOLDERS:
            raise TemplateError(f"unknown placeholder {{{name}}}")
        if names.count(name) > 1:
            raise TemplateError(f"{{{name}}} appears twice")
    if DESCRIPTION not in names:
        raise TemplateError(f"needs {{{DESCRIPTION}}}")
    if CONVENTIONAL in names and names.index(CONVENTIONAL) > names.index(DESCRIPTION):
        raise TemplateError("{conventional} must come before {description}")
    references = [name for name in names if name in REFERENCES]
    if len(references) > 1:
        raise TemplateError(f"{{{references[0]}}} and {{{references[1]}}} - at most one reference")
    if CONVENTIONAL in names and not texts[names.index(CONVENTIONAL) + 1].startswith(" "):
        raise TemplateError("{conventional} must be followed by a space")
    for index in range(1, len(names)):
        if not texts[index]:
            raise TemplateError(
                f"{{{names[index - 1]}}}{{{names[index]}}} cannot be told apart - put text between them"
            )
    if text != text.strip():
        raise TemplateError("must not start or end with whitespace")
    regex = "".join(re.escape(part) if index % 2 == 0 else PLACEHOLDERS[part][0] for index, part in enumerate(parts))
    return Template(text, tuple(parts), re.compile(regex))
