"""The release tag template: what a release is called, and which tags are releases.

Placeholders and rules: docs/configuration.md, "The release tag".
"""

import dataclasses
import datetime
import re
from collections.abc import Callable
from typing import Any

SEMVER = ("major", "minor", "patch")
NUMBER = r"0|[1-9]\d*"
TWO_DIGITS = r"\d{2}"
# Placeholder -> (the regex of its value, its value from the release's fields).
PLACEHOLDERS: dict[str, tuple[str, Callable[[Any], str]]] = {
    "major": (NUMBER, lambda fields: str(fields.version[0])),
    "minor": (NUMBER, lambda fields: str(fields.version[1])),
    "patch": (NUMBER, lambda fields: str(fields.version[2])),
    "YYYY": (r"\d{4}", lambda fields: str(fields.now.year)),
    "YY": (NUMBER, lambda fields: str(fields.now.year - 2000)),
    "0Y": (r"\d{2,}", lambda fields: f"{fields.now.year - 2000:02d}"),
    "MM": (NUMBER, lambda fields: str(fields.now.month)),
    "0M": (TWO_DIGITS, lambda fields: f"{fields.now.month:02d}"),
    "DD": (NUMBER, lambda fields: str(fields.now.day)),
    "0D": (TWO_DIGITS, lambda fields: f"{fields.now.day:02d}"),
    "HH": (NUMBER, lambda fields: str(fields.now.hour)),
    "0H": (TWO_DIGITS, lambda fields: f"{fields.now.hour:02d}"),
    "mm": (NUMBER, lambda fields: str(fields.now.minute)),
    "0m": (TWO_DIGITS, lambda fields: f"{fields.now.minute:02d}"),
    "count": (NUMBER, lambda fields: str(fields.count)),
    "sha": (r"[0-9a-f]{7}", lambda fields: fields.sha[:7]),
}
# Their values always have the same number of characters, so the next value's start is known.
FIXED_WIDTH = {"YYYY", "0M", "0D", "0H", "0m", "sha"}
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
# git check-ref-format, for the characters a template's text can bring in.
INVALID_IN_TAG = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]|\.\.|@\{|//|/\.|\.lock(/|$)")


class TemplateError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Fields:
    version: tuple[int, int, int] | None
    now: datetime.datetime | None
    count: int | None
    sha: str | None


@dataclasses.dataclass(frozen=True)
class Template:
    text: str
    # Text and placeholder names alternating, starting and ending with text (possibly empty).
    parts: tuple[str, ...]
    pattern: re.Pattern[str]

    @property
    def names(self) -> tuple[str, ...]:
        return self.parts[1::2]

    @property
    def semver(self) -> bool:
        return "major" in self.names

    @property
    def uses_count(self) -> bool:
        return "count" in self.names

    def matches(self, name: str) -> bool:
        return self.pattern.fullmatch(name) is not None

    def version(self, name: str) -> tuple[int, int, int] | None:
        match = self.pattern.fullmatch(name)
        if match is None or not self.semver:
            return None
        major, minor, patch = (int(match[field]) for field in SEMVER)
        return major, minor, patch

    def render(
        self,
        version: tuple[int, int, int] | None = None,
        now: datetime.datetime | None = None,
        count: int | None = None,
        sha: str | None = None,
    ) -> str:
        fields = Fields(version, now, count, sha)
        return "".join(
            part if index % 2 == 0 else PLACEHOLDERS[part][1](fields) for index, part in enumerate(self.parts)
        )


def valid_tag_name(name: str) -> bool:
    return not (INVALID_IN_TAG.search(name) or name.startswith(("-", "/", ".")) or name.endswith(("/", ".")))


def compile(text: str) -> Template:
    parts: list[str] = []
    position = 0
    for match in PLACEHOLDER.finditer(text):
        parts += [text[position : match.start()], match[1]]
        position = match.end()
    parts.append(text[position:])
    texts, names = parts[0::2], parts[1::2]
    if any("{" in part or "}" in part for part in texts):
        raise TemplateError(f"a {{ or }} outside a placeholder in {text!r}")
    if not names:
        raise TemplateError(f"{text!r} needs at least one placeholder")
    for name in names:
        if name not in PLACEHOLDERS:
            raise TemplateError(f"unknown placeholder {{{name}}}")
        if names.count(name) > 1:
            raise TemplateError(f"{{{name}}} appears twice")
    if 0 < len(set(SEMVER) & set(names)) < len(SEMVER):
        raise TemplateError("needs all of {major}, {minor}, {patch} or none")
    # A value of varying width ends at the first non-digit: a digit right after it would blur where it ends.
    for index in range(1, len(names)):
        before, between = names[index - 1], texts[index]
        if before not in FIXED_WIDTH and (not between or between[0].isdigit()):
            raise TemplateError(
                f"{{{before}}}{between}{{{names[index]}}} cannot be told apart - follow {{{before}}} with a non-digit"
            )
    # Placeholder values are digits and hex letters: a stand-in digit checks the text around them.
    if not valid_tag_name("".join(part if index % 2 == 0 else "0" for index, part in enumerate(parts))):
        raise TemplateError(f"{text!r} is not a valid tag name")
    regex = "".join(
        re.escape(part)
        if index % 2 == 0
        else f"(?P<{part}>{PLACEHOLDERS[part][0]})"
        if part in SEMVER
        else f"(?:{PLACEHOLDERS[part][0]})"
        for index, part in enumerate(parts)
    )
    return Template(text, tuple(parts), re.compile(regex))
