import base64
import urllib.error
from unittest import mock

import pytest

from mergeweasel import config
from mergeweasel.gitea import Gitea


def gitea_with(answer):
    gitea = mock.Mock(spec=Gitea)
    gitea.call.side_effect = answer if isinstance(answer, Exception) else (lambda method, path: answer)
    return gitea


def contents(text, encoding="utf-8"):
    return {"type": "file", "encoding": "base64", "content": base64.b64encode(text.encode(encoding)).decode()}


def test_the_file_is_read_from_main():
    gitea = gitea_with(contents('[release]\ntag = "{YY}.{count}"\n'))
    assert config.load(gitea).tag.text == "{YY}.{count}"
    gitea.call.assert_called_once_with("GET", "/contents/mergeweasel.toml?ref=main")


def test_without_the_file_everything_is_default():
    missing = urllib.error.HTTPError("url", 404, "Not Found", hdrs=None, fp=None)
    assert config.load(gitea_with(missing)).tag.text == config.DEFAULT_TAG


def test_another_api_error_is_raised():
    boom = urllib.error.HTTPError("url", 500, "boom", hdrs=None, fp=None)
    with pytest.raises(urllib.error.HTTPError):
        config.load(gitea_with(boom))


def test_a_byte_order_mark_is_ignored():
    assert config.load(gitea_with(contents('[release]\ntag = "{YY}.{count}"\n', "utf-8-sig"))).tag.text == "{YY}.{count}"


@pytest.mark.parametrize("text", ["", "# only a comment\n", "[release]\n"])
def test_missing_keys_take_their_default(text):
    assert config.parse(text).tag.text == config.DEFAULT_TAG


def test_crlf_line_ends_parse():
    assert config.parse('[release]\r\ntag = "{YY}.{count}"\r\n').tag.text == "{YY}.{count}"


@pytest.mark.parametrize("text, message", [
    ("[relase]\n", "mergeweasel.toml: unknown key relase"),
    ('[release]\ntags = "{YY}.{count}"\n', "mergeweasel.toml: unknown key release.tags"),
    ('release = "x"\n', "mergeweasel.toml: release must be a table"),
    ("[release]\ntag = 1\n", "mergeweasel.toml: release.tag must be a string"),
    ('[release]\ntag = "{yy}"\n', "mergeweasel.toml: release.tag: unknown placeholder {yy}"),
    ("[release\n", "mergeweasel.toml: "),
])
def test_invalid_files_name_the_key(text, message):
    with pytest.raises(config.ConfigError) as raised:
        config.parse(text)
    assert str(raised.value).startswith(message)


@pytest.mark.parametrize("answer", [
    {"type": "file", "encoding": "base64", "content": base64.b64encode(b"\xff\xfe[release]").decode()},
    {"type": "file", "encoding": "base64", "content": None},
    [{"type": "file", "name": "a.toml"}],
    {"type": "symlink", "target": "elsewhere.toml", "content": None},
])
def test_an_unreadable_file_is_a_config_error(answer):
    with pytest.raises(config.ConfigError, match="^mergeweasel.toml: "):
        config.load(gitea_with(answer))
