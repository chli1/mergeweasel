from unittest import mock

import pytest

from mergeweasel import config
from mergeweasel.gitea import ApiError, Gitea, NotAFile


def gitea_with(answer):
    """answer: the file's bytes, None for no file, or the exception file() raises."""
    gitea = mock.create_autospec(Gitea, instance=True)
    if isinstance(answer, Exception):
        gitea.file.side_effect = answer
    else:
        gitea.file.return_value = answer
    return gitea


def contents(text, encoding="utf-8"):
    return text.encode(encoding)


def test_the_file_is_read_from_a_branch():
    gitea = gitea_with(contents('[release]\ntag = "{YY}.{count}"\n'))
    assert config.load(gitea, "main").tag.text == "{YY}.{count}"
    gitea.file.assert_called_once_with("mergeweasel.toml", "main")


def test_the_file_is_read_at_a_given_commit():
    gitea = gitea_with(contents(""))
    config.load(gitea, "a" * 40)
    gitea.file.assert_called_once_with("mergeweasel.toml", "a" * 40)


def test_without_the_file_everything_is_default():
    assert config.load(gitea_with(None), "main").tag.text == config.DEFAULT_TAG


def test_another_api_error_is_raised():
    boom = ApiError("boom", 500, "url")
    with pytest.raises(ApiError):
        config.load(gitea_with(boom), "main")


def test_a_byte_order_mark_is_ignored():
    assert config.load(gitea_with(contents('[release]\ntag = "{YY}.{count}"\n', "utf-8-sig")), "main").tag.text == "{YY}.{count}"


@pytest.mark.parametrize("text", ["", "# only a comment\n", "[release]\n"])
def test_missing_keys_take_their_default(text):
    assert config.parse(text).tag.text == config.DEFAULT_TAG


def test_without_scopes_any_scope_passes():
    assert config.parse("").scopes is None


@pytest.mark.parametrize("text, scopes", [
    ('[commits]\nscopes = ["domain", "ui"]\n', ("domain", "ui")),
    ("[commits]\nscopes = []\n", ()),
])
def test_scopes_are_read(text, scopes):
    assert config.parse(text).scopes == scopes


def test_crlf_line_ends_parse():
    assert config.parse('[release]\r\ntag = "{YY}.{count}"\r\n').tag.text == "{YY}.{count}"


@pytest.mark.parametrize("text, message", [
    ("[relase]\n", "mergeweasel.toml: unknown key relase"),
    ('[release]\ntags = "{YY}.{count}"\n', "mergeweasel.toml: unknown key release.tags"),
    ('release = "x"\n', "mergeweasel.toml: release must be a table"),
    ("[release]\ntag = 1\n", "mergeweasel.toml: release.tag must be a string"),
    ('[release]\ntag = "{yy}"\n', "mergeweasel.toml: release.tag: unknown placeholder {yy}"),
    ("[release\n", "mergeweasel.toml: "),
    ('[commits]\nscopes = "ui"\n', "mergeweasel.toml: commits.scopes must be a list of strings"),
    ("[commits]\nscopes = [1]\n", "mergeweasel.toml: commits.scopes: 1 is not a scope"),
    ('[commits]\nscopes = [""]\n', "mergeweasel.toml: commits.scopes: '' is not a scope"),
    ('[commits]\nscopes = ["my ui"]\n', "mergeweasel.toml: commits.scopes: 'my ui' is not a scope"),
    ('[commits]\nscopes = ["u)i"]\n', "mergeweasel.toml: commits.scopes: 'u)i' is not a scope"),
])
def test_invalid_files_name_the_key(text, message):
    with pytest.raises(config.ConfigError) as raised:
        config.parse(text)
    assert str(raised.value).startswith(message)


def test_something_else_than_a_file_is_a_config_error():
    with pytest.raises(config.ConfigError, match="^mergeweasel.toml: not a file$"):
        config.load(gitea_with(NotAFile("mergeweasel.toml: not a file")), "main")


def test_a_file_that_is_not_utf8_is_a_config_error():
    with pytest.raises(config.ConfigError, match="^mergeweasel.toml: cannot read it as UTF-8 text$"):
        config.load(gitea_with(b"\xff\xfe[release]"), "main")
