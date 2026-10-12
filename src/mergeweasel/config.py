"""mergeweasel.toml: what a repository configures. Keys and defaults: docs/configuration.md."""
import dataclasses
import re
import tomllib

from mergeweasel import commit_rule, subject_template, version_template
from mergeweasel.gitea import NotAFile

CONFIG_FILE = "mergeweasel.toml"
DEFAULT_TAG = "v{major}.{minor}.{patch}"
# semantic-release's default rules; every other type releases nothing.
DEFAULT_MINOR_TYPES = ("feat",)
DEFAULT_PATCH_TYPES = ("fix", "perf", "revert")
# Table -> key -> type.
SCHEMA = {"release": {"enabled": bool, "tag": str, "minor_types": list, "patch_types": list},
          "commits": {"scopes": list, "subject": list, "projects": list, "types": list, "max_line": int,
                      "squash_append_pull_reference": bool}}
TYPE_NAMES = {bool: "true or false", str: "a string", list: "a list of strings", int: "an integer"}
# What {conventional} reads as the type.
TYPE = re.compile(r"[a-z]+")
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
    # The types that release a minor and a patch version; `!` always releases a major one.
    minor_types: tuple = DEFAULT_MINOR_TYPES
    patch_types: tuple = DEFAULT_PATCH_TYPES
    # Off by default: the queue releases nothing; the other release keys stay checked.
    release_enabled: bool = False
    # Tables and keys the file names and this version does not know, dotted; load() reports them.
    unknown_keys: tuple = ()


def load(gitea, ref):
    """The file as of ref: a branch, or a commit's SHA. Prints the keys it ignores."""
    try:
        content = gitea.file(CONFIG_FILE, ref)
    except NotAFile as error:
        raise ConfigError(error.message) from None
    try:
        # utf-8-sig: tomllib refuses the byte order mark some editors write.
        text = "" if content is None else content.decode("utf-8-sig")
    except ValueError:
        raise ConfigError(f"{CONFIG_FILE}: cannot read it as UTF-8 text") from None
    configuration = parse(text)
    for key in configuration.unknown_keys:
        print(f"{CONFIG_FILE}: unknown key {key} ignored")
    return configuration


def parse(text):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{CONFIG_FILE}: {error}") from None
    unknown = []
    for table, keys in data.items():
        if table not in SCHEMA:
            unknown.append(table)
            continue
        if not isinstance(keys, dict):
            raise ConfigError(f"{CONFIG_FILE}: {table} must be a table")
        for key, value in keys.items():
            expected = SCHEMA[table].get(key)
            if expected is None:
                unknown.append(f"{table}.{key}")
                continue
            # TOML's true is an int to Python.
            if not isinstance(value, expected) or isinstance(value, bool) != (expected is bool):
                raise ConfigError(f"{CONFIG_FILE}: {table}.{key} must be {TYPE_NAMES[expected]}")
    release = data.get("release", {})
    enabled = release.get("enabled", False)
    try:
        tag = version_template.compile(release.get("tag", DEFAULT_TAG))
    except version_template.TemplateError as error:
        raise ConfigError(f"{CONFIG_FILE}: release.tag: {error}") from None
    commits = data.get("commits", {})
    templates = subject_templates(commits.get("subject"))
    check_conventional(templates, commits, release, enabled)
    scopes = commits.get("scopes")
    if scopes is not None:
        for scope in scopes:
            if not isinstance(scope, str) or not SCOPE.fullmatch(scope):
                raise ConfigError(f"{CONFIG_FILE}: commits.scopes: {scope!r} is not a scope - "
                                  "it must be a non-empty string without whitespace and ')'")
        scopes = tuple(scopes)
    append = commits.get("squash_append_pull_reference", commit_rule.DEFAULT_COMMIT_RULES.append_pull_reference)
    if append:
        check_appendable(templates)
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
    types = commit_types(commits.get("types"))
    max_line = commits.get("max_line", commit_rule.MAX_LINE)
    if max_line < 1:
        raise ConfigError(f"{CONFIG_FILE}: commits.max_line must be at least 1")
    minor_types, patch_types = release_types(release, types)
    rules = commit_rule.CommitRules(templates, scopes, projects, types, max_line, append_pull_reference=append)
    return Config(tag, rules, minor_types, patch_types, enabled, tuple(unknown))


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


# The keys that read {conventional}, by table; type-free forms leave them nothing to read.
TYPE_KEYS = {"commits": ("types", "scopes"), "release": ("minor_types", "patch_types")}


def check_conventional(templates, commits, release, enabled):
    """Either every form has {conventional} or none has - then nothing may need the type."""
    typed = [template for template in templates if subject_template.CONVENTIONAL in template.names]
    if len(typed) == len(templates):
        return
    if typed:
        free = next(template for template in templates if template not in typed)
        raise ConfigError(f"{CONFIG_FILE}: commits.subject: {typed[0].text!r} has {{conventional}}, "
                          f"{free.text!r} does not - either every form has it or none")
    for table, keys in (("commits", commits), ("release", release)):
        for key in TYPE_KEYS[table]:
            if key in keys:
                raise ConfigError(f"{CONFIG_FILE}: {table}.{key} needs commits.subject templates with {{conventional}}")
    if enabled:
        raise ConfigError(f"{CONFIG_FILE}: release.enabled needs commits.subject templates with {{conventional}} - "
                          "the release reads the commit type; add it, or set release.enabled = false")


# A squash subject ends in the appended pull reference; these forms take it, an {issue} form would fail it.
APPENDED_ENDINGS = (" (#{pull})", " (#{pull_or_issue})")


def check_appendable(templates):
    key = "commits.squash_append_pull_reference"
    for template in templates:
        if template.text.endswith(" (#{issue})"):
            raise ConfigError(f"{CONFIG_FILE}: commits.subject: {template.text!r} ends like the appended pull "
                              f"reference - use {{pull_or_issue}}, or set {key} = false")
    if not any(template.text.endswith(APPENDED_ENDINGS) for template in templates):
        raise ConfigError(f"{CONFIG_FILE}: {key} needs a commits.subject form ending in "
                          + " or ".join(f"'{ending}'" for ending in APPENDED_ENDINGS) + f", or set {key} = false")


def commit_types(texts):
    if texts is None:
        return commit_rule.TYPES
    if not texts:
        raise ConfigError(f"{CONFIG_FILE}: commits.types must not be empty")
    for text in texts:
        check_type("commits.types", text)
    return tuple(texts)


def check_type(key, text):
    if not isinstance(text, str) or not TYPE.fullmatch(text):
        raise ConfigError(f"{CONFIG_FILE}: {key}: {text!r} is not a type - it must be lowercase letters a-z")


def release_types(table, types):
    """minor_types and patch_types. A list the file leaves out keeps of its default only the types in
    `types` and not in the other, configured list - so its errors never name an entry the file lacks."""
    configured = {}
    for key in ("minor_types", "patch_types"):
        if key not in table:
            continue
        for entry in table[key]:
            check_type(f"release.{key}", entry)
            if entry not in types:
                raise ConfigError(f"{CONFIG_FILE}: release.{key}: {entry!r} is not in commits.types")
        configured[key] = tuple(table[key])
    both = set(configured.get("minor_types", ())) & set(configured.get("patch_types", ()))
    if both:
        raise ConfigError(f"{CONFIG_FILE}: release.minor_types and release.patch_types both name {', '.join(sorted(both))}")
    taken = {entry for entries in configured.values() for entry in entries}

    def effective(key, default):
        if key in configured:
            return configured[key]
        return tuple(name for name in default if name in types and name not in taken)

    return effective("minor_types", DEFAULT_MINOR_TYPES), effective("patch_types", DEFAULT_PATCH_TYPES)
