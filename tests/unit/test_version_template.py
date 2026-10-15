import datetime
import re

import pytest

from mergeweasel import version_template
from mergeweasel.version_template import TemplateError

MOMENT = datetime.datetime(2026, 5, 3, 7, 4, tzinfo=datetime.UTC)
SHA = "abcdef0123456789abcdef0123456789abcdef01"
DEFAULT = "v{major}.{minor}.{patch}"


def test_default_renders_and_reads_back_its_version() -> None:
    template = version_template.compile(DEFAULT)
    assert template.render(version=(1, 10, 0)) == "v1.10.0"
    assert template.matches("v1.10.0")
    assert template.version("v1.10.0") == (1, 10, 0)
    assert template.semver and not template.uses_count


@pytest.mark.parametrize("name", ["v1.0", "v01.0.0", "v2.0.0-rc.1", "2.0.0", "xv1.2.3", "latest", "v1.2.3\n"])
def test_default_matches_only_its_exact_shape(name: str) -> None:
    template = version_template.compile(DEFAULT)
    assert not template.matches(name)
    assert template.version(name) is None


def test_a_custom_semver_template_reads_its_own_tags_only() -> None:
    template = version_template.compile("release-{major}.{minor}.{patch}")
    assert template.version("release-1.4.0") == (1, 4, 0)
    assert template.version("v1.4.0") is None


def test_unpadded_date_and_time_fields() -> None:
    template = version_template.compile("{YYYY}.{YY}.{MM}.{DD}.{HH}.{mm}")
    assert template.render(now=MOMENT) == "2026.26.5.3.7.4"


def test_padded_date_and_time_fields() -> None:
    template = version_template.compile("{0Y}.{0M}.{0D}.{0H}.{0m}")
    assert template.render(now=MOMENT) == "26.05.03.07.04"


def test_year_fields_count_from_2000() -> None:
    early = datetime.datetime(2006, 1, 1, tzinfo=datetime.UTC)
    assert version_template.compile("{YY}.{0Y}").render(now=early) == "6.06"


def test_count_and_sha() -> None:
    template = version_template.compile("{YY}.{MM}.{DD}.{count}+{sha}")
    name = template.render(now=MOMENT, count=412, sha=SHA)
    assert name == "26.5.3.412+abcdef0"
    assert template.matches(name) and template.uses_count and not template.semver


@pytest.mark.parametrize(
    "template, name",
    [
        ("{YY}.{MM}", "26.05"),  # unpadded fields have no leading zero
        ("{0Y}.{0M}", "26.5"),  # padded ones always two digits
        ("r{sha}", "rabcdef01"),  # 7 characters exactly
        ("r{sha}", "rABCDEF0"),  # lower-case hex
        ("{YYYY}", "26"),
    ],
)
def test_matching_follows_each_fields_shape(template: str, name: str) -> None:
    assert not version_template.compile(template).matches(name)


def test_every_placeholder_round_trips() -> None:
    template = version_template.compile(
        "v{major}.{minor}.{patch}-{YYYY}.{YY}.{0Y}.{MM}.{0M}.{DD}.{0D}.{HH}.{0H}.{mm}.{0m}.{count}+{sha}"
    )
    name = template.render(version=(2, 0, 11), now=MOMENT, count=7, sha=SHA)
    assert name == "v2.0.11-2026.26.26.5.05.3.03.7.07.4.04.7+abcdef0"
    assert template.version(name) == (2, 0, 11)


@pytest.mark.parametrize(
    "text, message",
    [
        ("", "at least one placeholder"),
        ("release", "at least one placeholder"),
        ("{yy}", "unknown placeholder {yy}"),
        ("v{major}.{minor}", "all of {major}, {minor}, {patch} or none"),
        ("v{major}.{minor}.{patch}.{major}", "{major} appears twice"),
        ("{YY}{MM}", "{YY}{MM} cannot be told apart"),
        ("v{major}1{minor}.{patch}", "{major}1{minor} cannot be told apart"),
        ("{count}{0M}", "{count}{0M} cannot be told apart"),
        ("{count}2.{sha}", "{count}2.{sha} cannot be told apart"),
        ("v{major", "a { or } outside a placeholder"),
        ("v}{count}", "a { or } outside a placeholder"),
        ("v {count}", "not a valid tag name"),
        ("-{count}", "not a valid tag name"),
        ("{count}.lock", "not a valid tag name"),
        ("{count}..{sha}", "not a valid tag name"),
        ("{count}:{sha}", "not a valid tag name"),
        ("{count}.", "not a valid tag name"),
    ],
)
def test_invalid_templates_name_their_fault(text: str, message: str) -> None:
    with pytest.raises(TemplateError, match=re.escape(message)):
        version_template.compile(text)


def test_plus_and_slash_are_valid_in_a_tag() -> None:
    assert version_template.compile("release/{YY}.{count}+{sha}").matches("release/26.412+abcdef0")


@pytest.mark.parametrize("text", ["v{major}.{minor}.{patch}-r2.{count}", "v{major}.1{minor}.{patch}"])
def test_digits_after_a_non_digit_are_fine(text: str) -> None:
    template = version_template.compile(text)
    assert template.version(template.render(version=(1, 2, 3), count=40)) == (1, 2, 3)


def test_fixed_width_placeholders_may_be_followed_directly() -> None:
    template = version_template.compile("{major}.{minor}.{patch}+{YYYY}{0M}{0D}{0H}{0m}")
    name = template.render(version=(1, 10, 0), now=MOMENT)
    assert name == "1.10.0+202605030704"
    assert template.version(name) == (1, 10, 0)
    assert version_template.compile("{sha}{count}").matches("abcdef0412")
