"""The release step: tag main and publish a Gitea release with notes when it holds releasing commits.

The version comes from the Conventional Commits since the nearest `vX.Y.Z` tag in main's history. How and why:
docs/design.md, "Releases".
"""
import dataclasses
import re
import sys
import urllib.error

from mergeweasel import commit_rule
from mergeweasel.gitea import PAGE_SIZE, error_message
from mergeweasel.queue import BASE_BRANCH

VERSION_TAG = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
BREAKING, FEATURE, FIX = "breaking", "feature", "fix"
# semantic-release's default rules; every other type releases nothing.
PATCH_TYPES = ("fix", "perf", "revert")
SECTIONS = ((BREAKING, "Breaking changes"), (FEATURE, "Features"), (FIX, "Fixes"))
TAG_REFS = "refs/tags/"
# How far back main's history is searched for the previous release.
MAX_COMMITS = 1000


@dataclasses.dataclass(frozen=True)
class Change:
    kind: str
    # The bullet: the subject's description and reference.
    line: str
    # A breaking change's commit body - what users must do; empty for the others.
    body: str


def latest_version(tag_names):
    versions = [tuple(int(part) for part in match.groups())
                for match in map(VERSION_TAG.match, tag_names) if match]
    return max(versions, default=None)


def change(message):
    subject, _, rest = message.partition("\n")
    if commit_rule.REVERT.match(subject):
        return Change(FIX, subject, "")
    parts = commit_rule.parse_subject(subject)
    if parts is None:
        return None
    line = f"{parts.scope + ': ' if parts.scope else ''}{parts.description} ({parts.reference})"
    if parts.breaking:
        # Gitea appends Co-authored-by trailers to squash commits; they are no note.
        body = "\n".join(text for text in rest.split("\n") if not commit_rule.ATTRIBUTION.match(text))
        return Change(BREAKING, line, body.strip())
    if parts.type == "feat":
        return Change(FEATURE, line, "")
    if parts.type in PATCH_TYPES:
        return Change(FIX, line, "")
    return None


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


def tag_name(version):
    return "v{}.{}.{}".format(*version)


def run(gitea, dry_run):
    """Release main if it holds releasing commits; False after an error, which it prints.

    Never raises: a failed release must not hold up the queue, the job fails at its end.
    """
    try:
        return _release(gitea, dry_run)
    except Exception as error:
        reason = error_message(error) if isinstance(error, urllib.error.HTTPError) else error
        print(f"release failed: {reason}", file=sys.stderr)
        return False


def version_tags(gitea):
    """Tag name -> commit sha of every vX.Y.Z tag; the git refs API lists them all in one call."""
    try:
        refs = gitea.call("GET", "/git/refs/tags")
    except urllib.error.HTTPError as error:
        # Gitea answers 404 when no ref matches.
        if error.code == 404:
            return {}
        raise
    tags = {}
    for ref in refs:
        name = ref["ref"].removeprefix(TAG_REFS)
        if not VERSION_TAG.match(name):
            continue
        target = ref["object"]
        # An annotated tag's ref points at the tag object, not at the commit.
        tags[name] = (gitea.call("GET", f"/git/tags/{target['sha']}")["object"]["sha"]
                      if target["type"] == "tag" else target["sha"])
    return tags


def history(gitea):
    """main's commits, newest first, at most MAX_COMMITS of them."""
    for page in range(1, MAX_COMMITS // PAGE_SIZE + 1):
        batch = gitea.call("GET", f"/commits?sha={BASE_BRANCH}&stat=false&verification=false&files=false"
                                  f"&limit={PAGE_SIZE}&page={page}")
        yield from batch
        if len(batch) < PAGE_SIZE:
            return


def _release(gitea, dry_run):
    tags = version_tags(gitea)
    if not tags:
        print("release: no vX.Y.Z tag to bump from, nothing released")
        return True
    on_commit = {}
    for name, commit in tags.items():
        on_commit.setdefault(commit, []).append(name)
    # main is linear: the nearest tagged commit in its history is the previous release.
    since, version, walked = [], None, 0
    for commit in history(gitea):
        walked += 1
        if commit["sha"] in on_commit:
            version = latest_version(on_commit[commit["sha"]])
            break
        since.append(commit)
    if version is None:
        where = f"within the last {MAX_COMMITS} commits of" if walked >= MAX_COMMITS else "on"
        print(f"release failed: no vX.Y.Z tag {where} {BASE_BRANCH}", file=sys.stderr)
        return False
    base = tag_name(version)
    if not since:
        return True
    head = since[0]["sha"]
    changes = [item for item in (change(commit["commit"]["message"]) for commit in reversed(since)) if item]
    following = next_version(version, changes)
    if following is None:
        print(f"release: nothing to release since {base}")
        return True
    name, text = tag_name(following), notes(changes)
    if name in tags:
        print(f"release failed: {name} exists on another commit, {tags[name][:7]}", file=sys.stderr)
        return False
    if dry_run:
        print(f"release: would release {name} on {head[:7]}:\n{text}")
        return True
    try:
        gitea.call("POST", "/releases", {"tag_name": name, "target_commitish": head, "name": name, "body": text})
    except urllib.error.HTTPError as error:
        if error.code != 409:
            raise
        # Another pass created it since the tags were read.
        print(f"release: {name} exists already")
        return True
    print(f"release: released {name} on {head[:7]}")
    return True
