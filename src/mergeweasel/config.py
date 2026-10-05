"""mergeweasel.toml: what a repository configures, read from main. Keys and defaults: docs/configuration.md."""
import base64
import dataclasses
import tomllib
import urllib.error

from mergeweasel import version_template
from mergeweasel.queue import BASE_BRANCH

CONFIG_FILE = "mergeweasel.toml"
CONFIG_PATH = f"/contents/{CONFIG_FILE}?ref={BASE_BRANCH}"
DEFAULT_TAG = "v{major}.{minor}.{patch}"
# Table -> key -> type.
SCHEMA = {"release": {"tag": str}}
TYPE_NAMES = {str: "a string"}


class ConfigError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Config:
    tag: version_template.Template


def load(gitea):
    try:
        answer = gitea.call("GET", CONFIG_PATH)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return parse("")
        raise
    if not isinstance(answer, dict) or answer.get("type") != "file":
        raise ConfigError(f"{CONFIG_FILE}: not a file")
    try:
        # utf-8-sig: tomllib refuses the byte order mark some editors write.
        text = base64.b64decode(answer["content"]).decode("utf-8-sig")
    except (TypeError, ValueError):
        raise ConfigError(f"{CONFIG_FILE}: cannot read it as UTF-8 text") from None
    return parse(text)


def parse(text):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{CONFIG_FILE}: {error}") from None
    for table, keys in data.items():
        if table not in SCHEMA:
            raise ConfigError(f"{CONFIG_FILE}: unknown key {table}")
        if not isinstance(keys, dict):
            raise ConfigError(f"{CONFIG_FILE}: {table} must be a table")
        for key, value in keys.items():
            expected = SCHEMA[table].get(key)
            if expected is None:
                raise ConfigError(f"{CONFIG_FILE}: unknown key {table}.{key}")
            if not isinstance(value, expected):
                raise ConfigError(f"{CONFIG_FILE}: {table}.{key} must be {TYPE_NAMES[expected]}")
    try:
        tag = version_template.compile(data.get("release", {}).get("tag", DEFAULT_TAG))
    except version_template.TemplateError as error:
        raise ConfigError(f"{CONFIG_FILE}: release.tag: {error}") from None
    return Config(tag)
