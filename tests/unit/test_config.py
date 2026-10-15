from typing import Any
from unittest import mock

import pytest

from mergeweasel import commit_rule, config
from mergeweasel.api import ApiError, NotAFile
from mergeweasel.gitea import Gitea


def gitea_with(answer: bytes | Exception | None) -> Any:
    """answer: the file's bytes, None for no file, or the exception file() raises."""
    gitea = mock.create_autospec(Gitea, instance=True)
    if isinstance(answer, Exception):
        gitea.file.side_effect = answer
    else:
        gitea.file.return_value = answer
    return gitea


def contents(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


def test_the_file_is_read_from_a_branch() -> None:
    gitea = gitea_with(contents('[release]\ntag = "{YY}.{count}"\n'))
    assert config.load(gitea, "main").tag.text == "{YY}.{count}"
    gitea.file.assert_called_once_with("mergeweasel.toml", "main")


def test_the_file_is_read_at_a_given_commit() -> None:
    gitea = gitea_with(contents(""))
    config.load(gitea, "a" * 40)
    gitea.file.assert_called_once_with("mergeweasel.toml", "a" * 40)


def test_without_the_file_everything_is_default() -> None:
    assert config.load(gitea_with(None), "main").tag.text == config.DEFAULT_TAG


def test_another_api_error_is_raised() -> None:
    boom = ApiError("boom", 500, "url")
    with pytest.raises(ApiError):
        config.load(gitea_with(boom), "main")


def test_a_byte_order_mark_is_ignored() -> None:
    assert (
        config.load(gitea_with(contents('[release]\ntag = "{YY}.{count}"\n', "utf-8-sig")), "main").tag.text
        == "{YY}.{count}"
    )


@pytest.mark.parametrize("text", ["", "# only a comment\n", "[release]\n"])
def test_missing_keys_take_their_default(text: str) -> None:
    assert config.parse(text).tag.text == config.DEFAULT_TAG


def test_without_scopes_any_scope_passes() -> None:
    assert config.parse("").commits.scopes is None


@pytest.mark.parametrize(
    "text, scopes",
    [
        ('[commits]\nscopes = ["domain", "ui"]\n', ("domain", "ui")),
        ("[commits]\nscopes = []\n", ()),
    ],
)
def test_scopes_are_read(text: str, scopes: tuple[str, ...]) -> None:
    assert config.parse(text).commits.scopes == scopes


def test_without_the_keys_the_subject_is_the_default() -> None:
    commits = config.parse("").commits
    assert tuple(template.text for template in commits.templates) == commit_rule.DEFAULT_SUBJECT
    assert commits.projects is None
    assert config.parse("") == config.Config(config.parse("").tag, commit_rule.DEFAULT_COMMIT_RULES)


def test_the_subject_templates_are_read_in_order() -> None:
    text = (
        '[commits]\nsquash_append_pull_reference = false\nsubject = ["[{key}] {conventional} {description}", "{conventional} {description}"]\n'
        'projects = ["PROJ", "OPS"]\n'
    )
    commits = config.parse(text).commits
    assert [template.text for template in commits.templates] == [
        "[{key}] {conventional} {description}",
        "{conventional} {description}",
    ]
    assert commits.projects == ("PROJ", "OPS")


def test_without_the_keys_types_line_limit_and_release_lists_are_the_defaults() -> None:
    configuration = config.parse("")
    assert configuration.commits.types == commit_rule.TYPES
    assert configuration.commits.max_line == commit_rule.MAX_LINE
    assert configuration.minor_types == config.DEFAULT_MINOR_TYPES
    assert configuration.patch_types == config.DEFAULT_PATCH_TYPES


def test_types_line_limit_and_release_lists_are_read() -> None:
    configuration = config.parse(
        '[commits]\ntypes = ["feature", "fix", "ci"]\nmax_line = 100\n'
        '[release]\nminor_types = ["feature"]\npatch_types = ["fix"]\n'
    )
    assert configuration.commits.types == ("feature", "fix", "ci")
    assert configuration.commits.max_line == 100
    assert (configuration.minor_types, configuration.patch_types) == (("feature",), ("fix",))


def test_an_empty_release_list_releases_nothing_of_its_kind() -> None:
    assert config.parse("[release]\nminor_types = []\n").minor_types == ()


@pytest.mark.parametrize(
    "text, minor, patch",
    [
        ('[commits]\ntypes = ["feat", "fix", "chore"]\n', ("feat",), ("fix",)),
        ('[commits]\ntypes = ["fix", "chore"]\n', (), ("fix",)),
        ('[release]\nminor_types = ["feat", "fix"]\n', ("feat", "fix"), ("perf", "revert")),
        ('[release]\npatch_types = ["feat"]\n', (), ("feat",)),
    ],
)
def test_a_default_release_list_keeps_only_what_the_file_allows(
    text: str, minor: tuple[str, ...], patch: tuple[str, ...]
) -> None:
    configuration = config.parse(text)
    assert (configuration.minor_types, configuration.patch_types) == (minor, patch)


@pytest.mark.parametrize(
    "text, enabled",
    [
        ("", False),
        ("[release]\nenabled = true\n", True),
        ("[release]\nenabled = false\n", False),
    ],
)
def test_the_release_switch(text: str, enabled: bool) -> None:
    assert config.parse(text).release_enabled is enabled


def test_by_default_the_pull_reference_is_appended() -> None:
    commits = config.parse("").commits
    assert commits.append_pull_reference is True
    assert [template.text for template in commits.templates] == [
        "{conventional} {description} (#{pull})",
        "{conventional} {description}",
    ]


def test_crlf_line_ends_parse() -> None:
    assert config.parse('[release]\r\ntag = "{YY}.{count}"\r\n').tag.text == "{YY}.{count}"


@pytest.mark.parametrize(
    "text, unknown",
    [
        ("[relase]\n", ("relase",)),
        ('relase = "x"\n', ("relase",)),
        ('[release]\ntags = "{YY}.{count}"\n', ("release.tags",)),
        ('[commits]\nscopse = ["ui"]\n[x]\ny = 1\n', ("commits.scopse", "x")),
    ],
)
def test_unknown_keys_are_collected_not_rejected(text: str, unknown: tuple[str, ...]) -> None:
    assert config.parse(text).unknown_keys == unknown


def test_unknown_keys_keep_the_known_ones() -> None:
    configuration = config.parse('[release]\ntags = "x"\ntag = "{YY}.{count}"\n')
    assert configuration.tag.text == "{YY}.{count}"


def test_load_prints_each_unknown_key(capsys: pytest.CaptureFixture[str]) -> None:
    config.load(gitea_with(contents('[comits]\n[release]\ntags = "x"\n')), "main")
    assert capsys.readouterr().out == (
        "mergeweasel.toml: unknown key comits ignored\nmergeweasel.toml: unknown key release.tags ignored\n"
    )


def test_strict_load_rejects_unknown_keys_naming_each() -> None:
    with pytest.raises(config.ConfigError, match=r"^mergeweasel.toml: unknown key comits, release.tags$"):
        config.load(gitea_with(contents('[comits]\n[release]\ntags = "x"\n')), "main", strict=True)


def test_strict_load_accepts_a_file_of_known_keys() -> None:
    assert (
        config.load(gitea_with(contents('[release]\ntag = "{YY}.{count}"\n')), "main", strict=True).tag.text
        == "{YY}.{count}"
    )


def test_a_file_without_unknown_keys_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    config.load(gitea_with(contents('[release]\ntag = "{YY}.{count}"\n')), "main")
    assert capsys.readouterr().out == ""


PULL = '"{conventional} {description} (#{pull})"'
ISSUE = '"{conventional} {description} (#{issue})"'
FREE = '"{description} (#{pull_or_issue})"'


@pytest.mark.parametrize(
    "text, append",
    [
        ("[commits]\nsquash_append_pull_reference = false\n", False),
        (f"[commits]\nsquash_append_pull_reference = true\nsubject = [{PULL}]\n", True),
    ],
)
def test_the_append_switch_is_read(text: str, append: bool) -> None:
    assert config.parse(text).commits.append_pull_reference is append


def test_without_the_append_any_forms_pass() -> None:
    text = (
        "[commits]\nsquash_append_pull_reference = false\nsubject = ["
        + ISSUE
        + ', "{conventional} {description} (PR #{pull})"]\n'
    )
    assert config.parse(text).commits.append_pull_reference is False


def test_type_free_forms_with_the_release_off() -> None:
    commits = config.parse(f'[commits]\nsubject = [{FREE}, "{{description}}"]\n[release]\nenabled = false\n').commits
    assert [template.text for template in commits.templates] == ["{description} (#{pull_or_issue})", "{description}"]
    assert commit_rule.check_subject("fix the login (#12)", commits) is None


@pytest.mark.parametrize(
    "text, message",
    [
        (
            '[commits]\nsquash_append_pull_reference = "yes"\n',
            "mergeweasel.toml: commits.squash_append_pull_reference must be true or false",
        ),
        (
            '[commits]\nsquash_append_pull_reference = true\nsubject = ["{conventional} {description} (PR #{pull})"]\n',
            (
                "mergeweasel.toml: commits.squash_append_pull_reference needs a commits.subject form ending in"
                " ' (#{pull})' or ' (#{pull_or_issue})', or set commits.squash_append_pull_reference = false"
            ),
        ),
        (
            "[commits]\nsquash_append_pull_reference = true\nsubject = [" + PULL + ", " + ISSUE + "]\n",
            (
                "mergeweasel.toml: commits.subject: '{conventional} {description} (#{issue})' ends like the appended"
                " pull reference - use {pull_or_issue}, or set commits.squash_append_pull_reference = false"
            ),
        ),
        ('release = "x"\n', "mergeweasel.toml: release must be a table"),
        ("[release]\ntag = 1\n", "mergeweasel.toml: release.tag must be a string"),
        ('[release]\nenabled = "false"\n', "mergeweasel.toml: release.enabled must be true or false"),
        ("[release]\nenabled = 0\n", "mergeweasel.toml: release.enabled must be true or false"),
        ('[release]\ntag = "{yy}"\n', "mergeweasel.toml: release.tag: unknown placeholder {yy}"),
        ("[release\n", "mergeweasel.toml: "),
        ('[commits]\nscopes = "ui"\n', "mergeweasel.toml: commits.scopes must be a list of strings"),
        ("[commits]\nscopes = [1]\n", "mergeweasel.toml: commits.scopes: 1 is not a scope"),
        ('[commits]\nscopes = [""]\n', "mergeweasel.toml: commits.scopes: '' is not a scope"),
        ('[commits]\nscopes = ["my ui"]\n', "mergeweasel.toml: commits.scopes: 'my ui' is not a scope"),
        ('[commits]\nscopes = ["u)i"]\n', "mergeweasel.toml: commits.scopes: 'u)i' is not a scope"),
        (
            '[commits]\nsubject = "{conventional} {description}"\n',
            "mergeweasel.toml: commits.subject must be a list of strings",
        ),
        ("[commits]\nsubject = []\n", "mergeweasel.toml: commits.subject must not be empty"),
        ("[commits]\nsubject = [1]\n", "mergeweasel.toml: commits.subject: 1 is not a template"),
        (
            '[commits]\nsubject = ["{conventional}{description}"]\n',
            "mergeweasel.toml: commits.subject: '{conventional}{description}': {conventional} must be followed by a space",
        ),
        (
            '[commits]\nsquash_append_pull_reference = false\nsubject = ["{conventional} {key} {description}"]\nprojects = []\n',
            "mergeweasel.toml: commits.projects must not be empty",
        ),
        (
            '[commits]\nsquash_append_pull_reference = false\nsubject = ["{conventional} {key} {description}"]\nprojects = ["proj"]\n',
            "mergeweasel.toml: commits.projects: 'proj' is not a project",
        ),
        (
            '[commits]\nsubject = ["{conventional} {description} (#{pull})", "{description} (#{pull})"]\n',
            (
                "mergeweasel.toml: commits.subject: '{conventional} {description} (#{pull})' has {conventional}, "
                "'{description} (#{pull})' does not - either every form has it or none"
            ),
        ),
        (
            f'[commits]\nsubject = [{FREE}]\ntypes = ["fix"]\n',
            "mergeweasel.toml: commits.types needs commits.subject templates with {conventional}",
        ),
        (
            f"[commits]\nsubject = [{FREE}]\nscopes = []\n",
            "mergeweasel.toml: commits.scopes needs commits.subject templates with {conventional}",
        ),
        (
            f'[commits]\nsubject = [{FREE}]\nscopes = ["my ui"]\n',
            "mergeweasel.toml: commits.scopes needs commits.subject templates with {conventional}",
        ),
        (
            f"[commits]\nsubject = [{FREE}]\n[release]\nminor_types = []\n",
            "mergeweasel.toml: release.minor_types needs commits.subject templates with {conventional}",
        ),
        (
            f'[commits]\nsubject = [{FREE}]\n[release]\npatch_types = ["fix"]\n',
            "mergeweasel.toml: release.patch_types needs commits.subject templates with {conventional}",
        ),
        (
            f"[commits]\nsubject = [{FREE}]\n[release]\nenabled = true\n",
            (
                "mergeweasel.toml: release.enabled needs commits.subject templates with {conventional} - the release reads the "
                "commit type; add it, or set release.enabled = false"
            ),
        ),
        (
            '[commits]\nprojects = ["PROJ"]\n',
            "mergeweasel.toml: commits.projects needs a commits.subject template with {key}",
        ),
        ('[commits]\ntypes = "feat"\n', "mergeweasel.toml: commits.types must be a list of strings"),
        ("[commits]\ntypes = []\n", "mergeweasel.toml: commits.types must not be empty"),
        ("[commits]\ntypes = [1]\n", "mergeweasel.toml: commits.types: 1 is not a type"),
        ('[commits]\ntypes = ["Feat"]\n', "mergeweasel.toml: commits.types: 'Feat' is not a type"),
        ('[commits]\ntypes = ["feat-x"]\n', "mergeweasel.toml: commits.types: 'feat-x' is not a type"),
        ('[commits]\nmax_line = "72"\n', "mergeweasel.toml: commits.max_line must be an integer"),
        ("[commits]\nmax_line = true\n", "mergeweasel.toml: commits.max_line must be an integer"),
        ("[commits]\nmax_line = 7.5\n", "mergeweasel.toml: commits.max_line must be an integer"),
        ("[commits]\nmax_line = 0\n", "mergeweasel.toml: commits.max_line must be at least 1"),
        ("[commits]\nmax_line = -1\n", "mergeweasel.toml: commits.max_line must be at least 1"),
        ('[release]\nminor_types = "feat"\n', "mergeweasel.toml: release.minor_types must be a list of strings"),
        (
            '[release]\npatch_types = ["bugfix"]\n',
            "mergeweasel.toml: release.patch_types: 'bugfix' is not in commits.types",
        ),
        ("[release]\npatch_types = [1]\n", "mergeweasel.toml: release.patch_types: 1 is not a type"),
        ('[release]\nminor_types = ["Feat"]\n', "mergeweasel.toml: release.minor_types: 'Feat' is not a type"),
        (
            '[commits]\ntypes = ["feat", "fix"]\n[release]\npatch_types = ["perf"]\n',
            "mergeweasel.toml: release.patch_types: 'perf' is not in commits.types",
        ),
        (
            '[release]\nminor_types = ["feat", "fix"]\npatch_types = ["fix"]\n',
            "mergeweasel.toml: release.minor_types and release.patch_types both name fix",
        ),
    ],
)
def test_invalid_files_name_the_key(text: str, message: str) -> None:
    with pytest.raises(config.ConfigError) as raised:
        config.parse(text)
    assert str(raised.value).startswith(message)


def test_something_else_than_a_file_is_a_config_error() -> None:
    with pytest.raises(config.ConfigError, match="^mergeweasel.toml: not a file$"):
        config.load(gitea_with(NotAFile("mergeweasel.toml: not a file")), "main")


def test_a_file_that_is_not_utf8_is_a_config_error() -> None:
    with pytest.raises(config.ConfigError, match="^mergeweasel.toml: cannot read it as UTF-8 text$"):
        config.load(gitea_with(b"\xff\xfe[release]"), "main")
