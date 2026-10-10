"""The release step: tag main and publish a Gitea release with notes when it holds releasing commits.

The commit types since the previous release - the nearest tag in main's history of the template's shape - decide
whether to release; the template in mergeweasel.toml names the release. How and why: docs/design.md, "Releases".
"""
import dataclasses
import datetime
import itertools
import sys
import traceback

from mergeweasel import commit_rule, config
from mergeweasel.gitea import ApiError, ReleaseExists

BREAKING, FEATURE, FIX = "breaking", "feature", "fix"
SECTIONS = ((BREAKING, "Breaking changes"), (FEATURE, "Features"), (FIX, "Fixes"))
# How far back main's history is searched for the previous release.
MAX_COMMITS = 1000


@dataclasses.dataclass(frozen=True)
class Change:
    kind: str
    # The bullet: the subject's description and reference.
    line: str
    # A breaking change's commit body - what users must do; empty for the others.
    body: str


def kind(commit_type, minor_types, patch_types):
    if commit_type in minor_types:
        return FEATURE
    if commit_type in patch_types:
        return FIX
    return None


def change(message, rules=commit_rule.DEFAULT_COMMIT_RULES, minor_types=config.DEFAULT_MINOR_TYPES,
           patch_types=config.DEFAULT_PATCH_TYPES):
    subject, _, rest = message.partition("\n")
    if commit_rule.REVERT.match(subject):
        # git's own revert subject counts as the type revert.
        released = kind("revert", minor_types, patch_types)
        return Change(released, subject, "") if released else None
    parts = commit_rule.parse_subject(subject, rules)
    if parts is None:
        return None
    line = parts.line()
    if parts.breaking:
        # Gitea appends Co-authored-by trailers to squash commits; they are no note.
        body = "\n".join(text for text in rest.split("\n") if not commit_rule.ATTRIBUTION.match(text))
        return Change(BREAKING, line, body.strip())
    released = kind(parts.type, minor_types, patch_types)
    return Change(released, line, "") if released else None


def outside_the_rule(message, rules):
    """A subject the release cannot read: it releases nothing, whatever it meant to."""
    subject = message.partition("\n")[0]
    return not commit_rule.EXEMPT.match(subject) and commit_rule.parse_subject(subject, rules) is None


def next_version(version, changes):
    kinds = {item.kind for item in changes}
    major, minor, patch = version
    if BREAKING in kinds:
        return (major + 1, 0, 0)
    if FEATURE in kinds:
        return (major, minor + 1, 0)
    if FIX in kinds:
        return (major, minor, patch + 1)
    return None


def notes(changes):
    blocks = []
    for kind, heading in SECTIONS:
        items = [item for item in changes if item.kind == kind]
        if not items:
            continue
        entries = []
        for item in items:
            entry = f"- {item.line}\n"
            if item.body:
                entry += "\n" + "".join(f"  {text}\n" if text else "\n" for text in item.body.split("\n")) + "\n"
            entries.append(entry)
        blocks.append(f"## {heading}\n\n" + "".join(entries).rstrip("\n") + "\n")
    return "\n".join(blocks)


def newest(template, names):
    """The previous release among the tags on one commit: the highest version, for SemVer."""
    return max(names, key=template.version) if template.semver else max(names)


def run(gitea, dry_run, base_branch, now=None):
    """Release the base branch if it holds releasing commits; False after an error, which it prints.

    Never raises: a failed release must not hold up the queue, the job fails at its end.
    """
    try:
        return _release(gitea, dry_run, base_branch, now or datetime.datetime.now(datetime.timezone.utc))
    except (ApiError, config.ConfigError) as error:
        print(f"release failed: {error}", file=sys.stderr)
        return False
    except Exception as error:
        # A bug: without its traceback it shows as one line like 'object'.
        traceback.print_exc()
        print(f"release failed: {error!r}", file=sys.stderr)
        return False


def release_tags(gitea, template):
    """Tag name -> commit sha of every tag of the template's shape."""
    return {ref.name: gitea.tag_commit(ref.sha) if ref.annotated else ref.sha
            for ref in gitea.tag_refs() if template.matches(ref.name)}


def _release(gitea, dry_run, base_branch, now):
    configuration = config.load(gitea, base_branch)
    # The forms only: a scope, project or type the lists no longer name is still read as it landed;
    # the release lists decide whether it releases.
    template, rules = configuration.tag, dataclasses.replace(configuration.commits, scopes=None, projects=None,
                                                              types=None)
    tags = release_tags(gitea, template)
    if not tags and template.semver:
        print(f"release: no tag matching {template.text} to bump from, nothing released")
        return True
    since, base = [], None
    if tags:
        on_commit = {}
        for name, commit in tags.items():
            on_commit.setdefault(commit, []).append(name)
        # main is linear: the nearest tagged commit in its history is the previous release.
        walked = 0
        for commit in itertools.islice(gitea.commits(base_branch), MAX_COMMITS):
            walked += 1
            if commit.sha in on_commit:
                base = newest(template, on_commit[commit.sha])
                break
            since.append(commit)
        if base is None:
            where = f"within the last {MAX_COMMITS} commits of" if walked >= MAX_COMMITS else "on"
            print(f"release failed: no tag matching {template.text} {where} {base_branch}", file=sys.stderr)
            return False
    else:
        # Without SemVer fields the name needs no previous release: main's head alone decides the first one.
        since = list(itertools.islice(gitea.commits(base_branch), 1))
    if not since:
        return True
    head = since[0].sha
    for commit in reversed(since):
        if outside_the_rule(commit.message, rules):
            subject = commit.message.partition("\n")[0]
            print(f"release: {commit.sha[:7]} is outside the subject rule and releases nothing: {subject!r}")
    changes = [item for item in (change(commit.message, rules, configuration.minor_types, configuration.patch_types)
                                 for commit in reversed(since)) if item]
    if not changes:
        print(f"release: nothing to release since {base}" if base
              else f"release: nothing to release on {head[:7]}, the first {template.text} release waits for a releasing commit")
        return True
    version = next_version(template.version(base), changes) if template.semver else None
    count = gitea.commit_count(head) if template.uses_count else None
    name, text = template.render(version=version, now=now, count=count, sha=head), notes(changes)
    if name in tags:
        print(f"release failed: {name} exists on another commit, {tags[name][:7]}", file=sys.stderr)
        return False
    if dry_run:
        print(f"release: would release {name} on {head[:7]}:\n{text}")
        return True
    try:
        gitea.create_release(name, head, text)
    except ReleaseExists:
        # Another pass created it since the tags were read.
        print(f"release: {name} exists already")
        return True
    print(f"release: released {name} on {head[:7]}")
    return True
