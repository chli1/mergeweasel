"""mergeweasel.toml: what a repository configures. Keys and defaults: docs/configuration.md."""
import dataclasses
import re
import tomllib

from mergeweasel import commit_rule, subject_template, version_template
from mergeweasel.gitea import NotAFile

CONFIG_FILE = "mergeweasel.toml"
DEFAULT_TAG = "v{major}.{minor}.{patch}"
# Table -> key -> type.
SCHEMA = {"release": {"tag": str}, "commits": {"scopes": list, "subject": list, "projects": list}}
TYPE_NAMES = {str: "a string", list: "a list of strings"}
# What the subject's scope can hold: non-empty, no whitespace, no closing parenthesis.
SCOPE = re.compile(r"[^\s)]+")
# The part of a {key} before its dash.
PROJECT = re.compile(r"[A-Z][A-Z0-9_]*")


class ConfigError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Config:
    tag: version_template.Template
    commits: commit_rule.CommitRules


def load(gitea, ref):
    """The file as of ref: a branch, or a commit's SHA."""
    try:
        content = gitea.file(CONFIG_FILE, ref)
    except NotAFile as error:
        raise ConfigError(error.message) from None
    if content is None:
        return parse("")
    try:
        # utf-8-sig: tomllib refuses the byte order mark some editors write.
        text = content.decode("utf-8-sig")
    except ValueError:
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
    commits = data.get("commits", {})
    scopes = commits.get("scopes")
    if scopes is not None:
        for scope in scopes:
            if not isinstance(scope, str) or not SCOPE.fullmatch(scope):
                raise ConfigError(f"{CONFIG_FILE}: commits.scopes: {scope!r} is not a scope - "
                                  "it must be a non-empty string without whitespace and ')'")
        scopes = tuple(scopes)
    templates = subject_templates(commits.get("subject"))
    projects = commits.get("projects")
    if projects is not None:
        if not projects:
            raise ConfigError(f"{CONFIG_FILE}: commits.projects must not be empty")
        for project in projects:
            if not isinstance(project, str) or not PROJECT.fullmatch(project):
                raise ConfigError(f"{CONFIG_FILE}: commits.projects: {project!r} is not a project - it must be "
                                  "an uppercase letter followed by uppercase letters, digits and '_'")
        if not any("key" in template.names for template in templates):
            raise ConfigError(f"{CONFIG_FILE}: commits.projects needs a commits.subject template with {{key}}")
        projects = tuple(projects)
    return Config(tag, commit_rule.CommitRules(templates, scopes, projects))


def subject_templates(texts):
    if texts is None:
        return commit_rule.DEFAULT_COMMIT_RULES.templates
    if not texts:
        raise ConfigError(f"{CONFIG_FILE}: commits.subject must not be empty")
    templates = []
    for text in texts:
        if not isinstance(text, str):
            raise ConfigError(f"{CONFIG_FILE}: commits.subject: {text!r} is not a template")
        try:
            templates.append(subject_template.compile(text))
        except subject_template.TemplateError as error:
            raise ConfigError(f"{CONFIG_FILE}: commits.subject: {text!r}: {error}") from None
    return tuple(templates)
