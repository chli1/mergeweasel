"""The test's side of the throwaway Gitea: API calls as any account, and the actions of the people
and systems around the queue - the author, the reviewer, CI and Renovate."""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any, Literal

ADMIN = "root"
# renovate is the Renovate account the passes are given; ci posts the required status; queue is the merger.
ACCOUNTS = ("queue", "reviewer", "reviewer2", "approver1", "approver2", "author", "ci", "renovate")
PASSWORD = "e2e-password-1"
# The merger's scopes from docs/onboarding.md; the other accounts get the same.
TOKEN_SCOPES = ["write:repository", "write:issue", "read:user", "read:organization"]
ORGANIZATION = "e2e"
LABELS = ("automerge", "needs-fix", "keep-commits", "prio")
REQUIRED_CONTEXT = "ci/test"
TIMEOUT_SECONDS = 30


class ApiError(AssertionError):
    def __init__(self, method: str, path: str, code: int, message: str) -> None:
        super().__init__(f"{method} {path}: {code} {message}")
        self.code = code


class Client:
    """One account's view of the Gitea API."""

    def __init__(self, server: str, token: str | None = None, basic: str | None = None) -> None:
        self.root = f"{server}/api/v1"
        if token:
            self.authorization = f"token {token}"
        else:
            # Without a token the client signs in with a password.
            assert basic is not None
            self.authorization = "Basic " + base64.b64encode(basic.encode()).decode()

    def call(self, method: str, path: str, body: object = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"{self.root}{path}", data=data, method=method)
        request.add_header("Authorization", self.authorization)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            raise ApiError(method, path, error.code, error.read().decode()[:500]) from None
        return json.loads(payload) if payload.strip() else None


def eventually[T](
    probe: Callable[[], T | Literal[False] | None], timeout: float = TIMEOUT_SECONDS, interval: float = 0.5
) -> T:
    """The first truthy result of probe(): Gitea settles some effects asynchronously."""
    deadline = time.monotonic() + timeout
    while True:
        result = probe()
        if result:
            return result
        if time.monotonic() > deadline:
            raise AssertionError(f"not reached within {timeout} s, last result: {result!r}")
        time.sleep(interval)


def create_repo(
    clients: Mapping[str, Client],
    name: str,
    required_approvals: int = 1,
    branch: str = "main",
    labels: tuple[str, ...] = LABELS,
    contexts: Iterable[str] = (REQUIRED_CONTEXT,),
) -> "Repo":
    """A repository set up as docs/onboarding.md describes, with issue #1 for the titles to refer to.

    branch: its default branch; labels: the automerge, needs-fix, keep-commits and prio labels' names;
    contexts: the status checks its branch protection requires.
    """
    admin = clients[ADMIN]
    admin.call("POST", f"/orgs/{ORGANIZATION}/repos", {"name": name, "auto_init": True, "default_branch": branch})
    full_name = f"{ORGANIZATION}/{name}"
    path = f"/repos/{full_name}"
    admin.call(
        "PATCH",
        path,
        {"allow_squash_merge": True, "allow_fast_forward_only_merge": True, "default_delete_branch_after_merge": True},
    )
    for account in ACCOUNTS:
        admin.call("PUT", f"{path}/collaborators/{account}", {"permission": "write"})
    # The automerge label is the organization's, the others the repository's: each label removal the
    # queue makes - automerge after a merge, needs-fix on Renovate's retry - meets one of the two kinds.
    automerge, *others = labels
    if automerge not in {label["name"] for label in admin.call("GET", f"/orgs/{ORGANIZATION}/labels?limit=50")}:
        admin.call("POST", f"/orgs/{ORGANIZATION}/labels", {"name": automerge, "color": "#888888"})
    for label in others:
        admin.call("POST", f"{path}/labels", {"name": label, "color": "#888888"})
    clients["author"].call("POST", f"{path}/issues", {"title": "The issue every title refers to"})
    admin.call(
        "POST",
        f"{path}/branch_protections",
        {
            "rule_name": branch,
            "enable_status_check": True,
            "status_check_contexts": list(contexts),
            "required_approvals": required_approvals,
            "enable_approvals_whitelist": True,
            # Singular: Gitea ignores approvals_whitelist_usernames.
            "approvals_whitelist_username": ["reviewer", "reviewer2", "queue", "approver1", "approver2"],
            "dismiss_stale_approvals": True,
            "block_on_outdated_branch": True,
            "enable_merge_whitelist": True,
            "merge_whitelist_usernames": ["queue"],
            # The admin moves the branch in the scenarios; everyone else lands through the queue.
            "enable_push": True,
            "enable_push_whitelist": True,
            "push_whitelist_usernames": [ADMIN],
        },
    )
    return Repo(clients, full_name, branch, labels[0])


class Repo:
    def __init__(
        self, clients: Mapping[str, Client], name: str, branch: str = "main", automerge: str = "automerge"
    ) -> None:
        self.clients = clients
        self.name = name
        self.path = f"/repos/{name}"
        # The default branch, and the name of the label that queues a pull request.
        self.branch = branch
        self.automerge = automerge

    def call(self, account: str, method: str, path: str, body: object = None) -> Any:
        return self.clients[account].call(method, f"{self.path}{path}", body)

    def read(self, path: str) -> Any:
        return self.call(ADMIN, "GET", path)

    # The people and systems around the queue.

    def commit(
        self, account: str, branch: str, files: Mapping[str, str], message: str, new_branch: str | None = None
    ) -> str:
        """One commit through the contents API; files maps a path to its new content."""
        operations: list[dict[str, str]] = []
        for path, content in files.items():
            operation = {"path": path, "content": base64.b64encode(content.encode()).decode()}
            existing = self._file_sha(branch, path)
            operation.update({"operation": "update", "sha": existing} if existing else {"operation": "create"})
            operations.append(operation)
        body: dict[str, object] = {"branch": branch, "message": message, "files": operations}
        if new_branch:
            body["new_branch"] = new_branch
        sha = self.call(account, "POST", "/contents", body)["commit"]["sha"]
        # Gitea handles a push asynchronously: a pull request opened or approved before that would
        # see it arrive later - as a new push that dismisses the approval. Its feed entry comes last.
        eventually(lambda: [entry for entry in self.read("/activities/feeds") if sha in entry["content"]])
        return sha

    def move_main(self, files: Mapping[str, str], message: str) -> str:
        return self.commit(ADMIN, self.branch, files, message)

    def open_pull(self, account: str, branch: str, title: str, body: str = "", labels: Collection[str] = ()) -> int:
        """labels: set in the creating call itself, as Renovate does."""
        known = self.read("/labels") + self.clients[ADMIN].call("GET", f"/orgs/{ORGANIZATION}/labels?limit=50")
        ids = [label["id"] for label in known if label["name"] in labels]
        return self.call(
            account,
            "POST",
            "/pulls",
            {"head": branch, "base": self.branch, "title": title, "body": body, "labels": ids},
        )["number"]

    def post_status(self, sha: str, state: str, target_url: str = "") -> None:
        self.call(
            "ci", "POST", f"/statuses/{sha}", {"state": state, "context": REQUIRED_CONTEXT, "target_url": target_url}
        )

    def approve(self, number: int, account: str = "reviewer") -> None:
        self.call(account, "POST", f"/pulls/{number}/reviews", {"event": "APPROVED", "body": "Looks good."})

    def add_label(self, account: str, number: int, name: str) -> None:
        self.call(account, "POST", f"/issues/{number}/labels", {"labels": [name]})

    def ready(self, number: int, state: str = "success", target_url: str = "", labeller: str = "author") -> None:
        """CI reports on the head, the reviewer approves, the author queues it."""
        self.post_status(self.head(number), state, target_url)
        self.approve(number)
        self.add_label(labeller, number, self.automerge)

    def tag(self, name: str) -> None:
        """The tag a repository's owner sets by hand on the default branch to enable releases."""
        self.call(ADMIN, "POST", "/tags", {"tag_name": name, "target": self.branch})

    def open_renovate_pull(
        self,
        branch: str,
        files: Mapping[str, str],
        title: str = "fix(deps): update a (#1)",
        label_at_creation: bool = False,
    ) -> int:
        """Renovate's update: its commit, its pull request, its automerge label - CI green on the head.

        label_at_creation: the label comes with the pull request, as Renovate's addLabels sets it;
        otherwise in a call of its own.
        """
        self.commit("renovate", self.branch, files, title, new_branch=branch)
        number = self.open_pull("renovate", branch, title, labels=(self.automerge,) if label_at_creation else ())
        self.post_status(self.head(number), "success")
        if not label_at_creation:
            self.add_label("renovate", number, self.automerge)
        return number

    # What the assertions read.

    def pull(self, number: int) -> Any:
        return self.read(f"/pulls/{number}")

    def head(self, number: int) -> str:
        return self.pull(number)["head"]["sha"]

    def main_sha(self) -> str:
        return self.read(f"/branches/{self.branch}")["commit"]["id"]

    def branches(self) -> set[str]:
        return {branch["name"] for branch in self.read("/branches")}

    def git_commit(self, sha: str) -> Any:
        return self.read(f"/git/commits/{sha}")

    def labels(self, number: int) -> set[str]:
        return {label["name"] for label in self.read(f"/issues/{number}/labels")}

    def comments_by(self, account: str, number: int) -> list[str]:
        return [
            comment["body"]
            for comment in self.read(f"/issues/{number}/comments")
            if comment["user"]["login"] == account
        ]

    def reviews(self, number: int) -> Any:
        return self.read(f"/pulls/{number}/reviews")

    def statuses(self, sha: str) -> Any:
        return self.read(f"/commits/{sha}/statuses")

    def states(self, sha: str) -> dict[str, str]:
        """The latest state per context, as the forge combines them - the queue reads the same.

        Not from statuses(): Forgejo lists changes within one second oldest first.
        """
        return {
            status["context"]: status["status"]
            for status in self.read(f"/commits/{sha}/status?limit=50&page=1")["statuses"] or ()
        }

    def runs(self) -> list[tuple[str, str, str]]:
        """(workflow file, event, status) of each Actions task, newest first."""
        return [
            (task["workflow_id"], task["event"], task["status"])
            for task in self.read("/actions/tasks")["workflow_runs"]
        ]

    def task_ids(self) -> list[int]:
        """The id of each Actions task, newest first."""
        return [task["id"] for task in self.read("/actions/tasks")["workflow_runs"]]

    def tags(self) -> dict[str, str]:
        return {tag["name"]: tag["commit"]["sha"] for tag in self.read("/tags")}

    def release(self, tag: str) -> Any:
        try:
            return self.read(f"/releases/tags/{urllib.parse.quote(tag)}")
        except ApiError as error:
            if error.code == 404:
                return None
            raise

    def pusher(self, number: int, sha: str) -> str | None:
        """Who pushed the commit, as the pull request's timeline records it; None if not recorded."""
        return next(
            (
                entry["user"]["login"]
                for entry in self.read(f"/issues/{number}/timeline")
                if entry["type"] == "pull_push" and sha in json.loads(entry["body"])["commit_ids"]
            ),
            None,
        )

    def _file_sha(self, branch: str, path: str) -> str | None:
        try:
            return self.read(f"/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(branch)}")["sha"]
        except ApiError as error:
            if error.code == 404:
                return None
            raise
