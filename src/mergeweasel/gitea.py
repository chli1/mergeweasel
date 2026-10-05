"""The Gitea API client: repository-relative calls, paging, retries."""
import json
import time
import urllib.error
import urllib.request

from mergeweasel.commit_rule import Commit

# Gitea caps every list at 50 entries per page, whatever `limit` asks for.
PAGE_SIZE = 50
HTTP_TIMEOUT_SECONDS = 30
RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5


def error_message(error):
    try:
        return json.loads(error.read())["message"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return str(error)


def retried(operation):
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return operation()
        except (OSError, LookupError):
            if attempt == RETRY_ATTEMPTS - 1:
                raise
            time.sleep(RETRY_DELAY_SECONDS)


class Gitea:
    def __init__(self, server, repository, token):
        self.root = f"{server}/api/v1"
        self.api = f"{self.root}/repos/{repository}"
        self.token = token

    def call(self, method, path, body=None):
        return self._request(method, f"{self.api}{path}", body)

    def own_login(self):
        """The login of the token's account; needs the token scope read:user."""
        return self._request("GET", f"{self.root}/user")["login"]

    def paged(self, path):
        separator = "&" if "?" in path else "?"
        items, page = [], 1
        while True:
            batch = self.call("GET", f"{path}{separator}limit={PAGE_SIZE}&page={page}")
            items.extend(batch)
            if len(batch) < PAGE_SIZE:
                return items
            page += 1

    def total_count(self, path):
        """Gitea's count of all items of a list call, from its X-Total-Count header."""
        _, headers = self._exchange("GET", f"{self.api}{path}")
        value = headers.get("X-Total-Count")
        if value is None:
            raise LookupError(f"Gitea sent no X-Total-Count for {path}")
        return int(value)

    def _request(self, method, url, body=None):
        payload, _ = self._exchange(method, url, body)
        return json.loads(payload) if payload.strip() else None

    def _exchange(self, method, url, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method)
        if self.token:
            request.add_header("Authorization", f"token {self.token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read(), response.headers


def issue_numbers(gitea):
    return {issue["number"] for issue in gitea.paged("/issues?state=all&type=issues")}


def pull_commits(gitea, number):
    return [Commit(raw["sha"], raw["commit"]["message"].split("\n", 1)[0], len(raw.get("parents") or []))
            for raw in gitea.paged(f"/pulls/{number}/commits?stat=false&verification=false&files=false")]
