"""The Gitea API client: one method per operation, typed values, domain exceptions; paging and retries."""
import base64
import dataclasses
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from mergeweasel.commit_rule import Commit

# Gitea caps every list at 50 entries per page, whatever `limit` asks for.
PAGE_SIZE = 50
HTTP_TIMEOUT_SECONDS = 30
RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5
COMMIT_QUERY = "stat=false&verification=false&files=false"
# http.client quotes an invalid header in its error, the token included.
MALFORMED = "the token or Gitea's answer is malformed"
# TimelineEvent.kind
PUSH, LABEL_ADDED, LABEL_REMOVED, OTHER = "push", "label_added", "label_removed", "other"
# Gitea's 405 while its mergeable check still runs; the next pass retries.
TRANSIENT_MERGE_REFUSAL = "Please try again later"


def gitea_message(error):
    """Gitea's message in an HTTP error's body, else the HTTP reason."""
    try:
        return json.loads(error.read())["message"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return str(error.reason)


class ApiError(Exception):
    """Gitea's answer to a request, or its absence; status None without an HTTP answer."""

    def __init__(self, message, status=None, url=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.url = url


class Unreachable(ApiError):
    pass


class NotFound(ApiError):
    pass


class Conflict(ApiError):
    """A rebase that conflicts with the base branch."""


class MergeRefused(ApiError):
    """A merge Gitea refuses for good: the pull request needs a fix."""


class MergeDeferred(ApiError):
    """A merge Gitea refuses for now: its checks still settle, or the head moved."""


class ReleaseExists(ApiError):
    pass


class NotAFile(ApiError):
    pass


def reraise_as(kind, error):
    raise kind(error.message, error.status, error.url) from error


@dataclasses.dataclass(frozen=True)
class Branch:
    sha: str
    # Empty when the branch protection requires no status checks.
    required_contexts: tuple
    required_approvals: int


@dataclasses.dataclass(frozen=True)
class PullRequest:
    number: int
    base_ref: str
    head_ref: str
    head_sha: str
    # Name -> id.
    labels: dict
    draft: bool
    poster: str
    title: str
    body: str
    merged: bool


@dataclasses.dataclass(frozen=True)
class CommitStatus:
    context: str
    state: str
    target_url: str
    description: str
    # The login that set it, None if Gitea names none.
    creator: str | None


@dataclasses.dataclass(frozen=True)
class Review:
    login: str
    state: str
    official: bool
    dismissed: bool
    stale: bool
    commit_id: str


@dataclasses.dataclass(frozen=True)
class TimelineEvent:
    # Chronological.
    id: int
    kind: str
    login: str | None
    # The label of a label event, else None.
    label: str | None


@dataclasses.dataclass(frozen=True)
class TagRef:
    name: str
    # An annotated tag's ref points at the tag object, not at the commit.
    sha: str
    annotated: bool


@dataclasses.dataclass(frozen=True)
class HistoryCommit:
    sha: str
    message: str


@dataclasses.dataclass(frozen=True)
class WorkflowRun:
    id: int
    status: str
    event: str


def pull_request(raw):
    return PullRequest(
        number=raw["number"], base_ref=raw["base"]["ref"], head_ref=raw["head"]["ref"], head_sha=raw["head"]["sha"],
        labels={label["name"]: label["id"] for label in raw["labels"]}, draft=raw.get("draft", False),
        poster=raw["user"]["login"], title=raw["title"], body=raw.get("body") or "",
        merged=raw["merged"])


def timeline_event(raw):
    if raw["type"] == "pull_push":
        kind = PUSH
    elif raw["type"] == "label":
        # Gitea marks an added label with the body "1".
        kind = LABEL_ADDED if raw.get("body") == "1" else LABEL_REMOVED
    else:
        kind = OTHER
    return TimelineEvent(raw["id"], kind, (raw.get("user") or {}).get("login"), (raw.get("label") or {}).get("name"))


def retried(operation):
    """Retries a network error or a 5xx answer; anything else is no lag that waiting fixes."""
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return operation()
        except ApiError as error:
            transient = isinstance(error, Unreachable) or (error.status or 0) >= 500
            if not transient or attempt == RETRY_ATTEMPTS - 1:
                raise
            time.sleep(RETRY_DELAY_SECONDS)


class Gitea:
    def __init__(self, server, repository, token):
        self.root = f"{server}/api/v1"
        self.api = f"{self.root}/repos/{repository}"
        self._owner = repository.split("/")[0]
        self.token = token

    def own_login(self):
        """The login of the token's account; needs the token scope read:user."""
        return self._json("GET", "/user", root=True)["login"]

    def _exchange(self, method, url, body=None):
        data = None if body is None else json.dumps(body).encode()
        try:
            request = urllib.request.Request(url, data=data, method=method)
            if self.token:
                request.add_header("Authorization", f"token {self.token}")
            if data is not None:
                request.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.read(), response.headers
        except urllib.error.HTTPError as error:
            kind = NotFound if error.code == 404 else ApiError
            raise kind(gitea_message(error), error.code, url) from error
        except OSError as error:
            # URLError and timeouts: no answer at all.
            raise Unreachable(str(getattr(error, "reason", error)), None, url) from error
        except ValueError:
            raise ApiError(MALFORMED, None, url) from None

    def _json(self, method, path, body=None, root=False):
        url = f"{self.root if root else self.api}{path}"
        payload, _ = self._exchange(method, url, body)
        if not payload.strip():
            return None
        try:
            return json.loads(payload)
        except ValueError:
            raise ApiError("Gitea's answer is not JSON", None, url) from None

    def _pages(self, path, root=False):
        """The list's items, one page read at a time as the caller advances."""
        separator = "&" if "?" in path else "?"
        page = 1
        while True:
            batch = self._json("GET", f"{path}{separator}limit={PAGE_SIZE}&page={page}", root=root)
            yield from batch
            if len(batch) < PAGE_SIZE:
                return
            page += 1

    def default_branch(self):
        return self._json("GET", "")["default_branch"]

    def branch(self, name):
        # branches/{name}, not branch_protections: the protection API needs repo admin.
        raw = self._json("GET", f"/branches/{name}")
        required = tuple(raw["status_check_contexts"] or ()) if raw["enable_status_check"] else ()
        return Branch(raw["commit"]["id"], required, raw["required_approvals"])

    def branch_head(self, name):
        """The branch's head from the git refs API, which reads the repository itself: a pull
        request's head.sha may still lag behind a push."""
        ref = f"refs/heads/{name}"
        path = f"/git/{urllib.parse.quote(ref)}"
        # The API matches by prefix: renovate/gradle also returns renovate/gradle-8.x.
        head = next((entry["object"]["sha"] for entry in self._json("GET", path) if entry["ref"] == ref), None)
        if head is None:
            raise NotFound(f"{ref} not found", None, f"{self.api}{path}")
        return head

    def open_pulls(self):
        return [pull_request(raw) for raw in self._pages("/pulls?state=open")]

    def pull(self, number):
        return pull_request(self._json("GET", f"/pulls/{number}"))

    def statuses(self, sha):
        """The latest status per context on sha."""
        raw = self._json("GET", f"/commits/{sha}/status?limit={PAGE_SIZE}")
        return [CommitStatus(status["context"], status["status"], status.get("target_url") or "",
                             status.get("description") or "", (status.get("creator") or {}).get("login"))
                for status in raw["statuses"] or ()]

    def is_behind(self, head, base):
        """True if base holds commits head lacks."""
        # compare, not merge_base: Gitea updates merge_base asynchronously after a push.
        return self._json("GET", f"/compare/{head}...{base}")["total_commits"] > 0

    def reviews(self, number):
        return [Review(raw["user"]["login"], raw["state"], raw["official"], raw["dismissed"], raw["stale"],
                       raw["commit_id"]) for raw in self._pages(f"/pulls/{number}/reviews")]

    def timeline(self, number):
        return [timeline_event(raw) for raw in self._pages(f"/issues/{number}/timeline")]

    def pull_commits(self, number):
        return [Commit(raw["sha"], raw["commit"]["message"].split("\n", 1)[0], len(raw.get("parents") or []))
                for raw in self._pages(f"/pulls/{number}/commits?{COMMIT_QUERY}")]

    def is_issue(self, number):
        """False for a pull request, which Gitea also serves as an issue, and for a missing number."""
        try:
            return self._json("GET", f"/issues/{number}").get("pull_request") is None
        except NotFound:
            return False

    def file(self, path, ref):
        """The file's content as of ref - a branch or a commit's SHA -, None if it does not exist there."""
        try:
            answer = self._json("GET", f"/contents/{path}?ref={ref}")
        except NotFound:
            return None
        url = f"{self.api}/contents/{path}?ref={ref}"
        if not isinstance(answer, dict) or answer.get("type") != "file":
            raise NotAFile(f"{path}: not a file", None, url)
        try:
            return base64.b64decode(answer["content"])
        except (TypeError, ValueError):
            raise NotAFile(f"{path}: cannot read its content", None, url) from None

    def tag_refs(self):
        """Every tag; the git refs API lists them all in one call."""
        try:
            refs = self._json("GET", "/git/refs/tags")
        except NotFound:
            # Gitea's answer when no tag exists.
            return []
        return [TagRef(ref["ref"].removeprefix("refs/tags/"), ref["object"]["sha"], ref["object"]["type"] == "tag")
                for ref in refs]

    def tag_commit(self, sha):
        """The commit an annotated tag object points at."""
        return self._json("GET", f"/git/tags/{sha}")["object"]["sha"]

    def commits(self, ref):
        """ref's history, newest first."""
        return (HistoryCommit(raw["sha"], raw["commit"]["message"])
                for raw in self._pages(f"/commits?sha={ref}&{COMMIT_QUERY}"))

    def commit_count(self, sha):
        """The commits in sha's history, from Gitea's X-Total-Count header."""
        url = f"{self.api}/commits?sha={sha}&{COMMIT_QUERY}&limit=1"
        _, headers = self._exchange("GET", url)
        value = headers.get("X-Total-Count")
        if value is None:
            raise ApiError("Gitea sent no X-Total-Count", None, url)
        return int(value)

    def labels(self):
        return [label["name"] for label in self._pages("/labels")]

    def org_labels(self):
        """The organization's label names, None if unreadable: a user's repository, or a token without access."""
        try:
            return [label["name"] for label in self._pages(f"/orgs/{self._owner}/labels", root=True)]
        except ApiError as error:
            if error.status in (403, 404):
                return None
            raise

    def workflow_runs(self, file):
        """The newest runs of the workflow, one page."""
        raw = self._json("GET", f"/actions/workflows/{file}/runs?limit={PAGE_SIZE}")
        return [WorkflowRun(run["id"], run["status"], run["event"]) for run in raw["workflow_runs"]]

    def rebase(self, number):
        try:
            self._json("POST", f"/pulls/{number}/update?style=rebase")
        except ApiError as error:
            if error.status == 409:
                reraise_as(Conflict, error)
            raise

    def merge(self, number, head_sha, message):
        """Squash into message's subject and body, or fast-forward the commits as they are if message is None."""
        if message is None:
            request = {"Do": "fast-forward-only", "head_commit_id": head_sha}
        else:
            request = {"Do": "squash", "MergeTitleField": message.subject, "MergeMessageField": message.body,
                       "head_commit_id": head_sha}
        try:
            self._json("POST", f"/pulls/{number}/merge", request)
        except ApiError as error:
            if error.status == 405 and error.message != TRANSIENT_MERGE_REFUSAL:
                reraise_as(MergeRefused, error)
            if error.status in (405, 409):
                reraise_as(MergeDeferred, error)
            raise

    def approve(self, number, sha, body):
        self._json("POST", f"/pulls/{number}/reviews", {"event": "APPROVED", "commit_id": sha, "body": body})

    def add_label(self, number, name):
        self._json("POST", f"/issues/{number}/labels", {"labels": [name]})

    def remove_label(self, number, label_id):
        self._json("DELETE", f"/issues/{number}/labels/{label_id}")

    def comment(self, number, body):
        self._json("POST", f"/issues/{number}/comments", {"body": body})

    def set_status(self, sha, context, state, description):
        self._json("POST", f"/statuses/{sha}", {"state": state, "context": context, "description": description})

    def create_release(self, name, sha, body):
        try:
            self._json("POST", "/releases", {"tag_name": name, "target_commitish": sha, "name": name, "body": body})
        except ApiError as error:
            if error.status == 409:
                reraise_as(ReleaseExists, error)
            raise
