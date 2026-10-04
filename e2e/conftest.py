"""The throwaway Gitea and the pass under test.

Run: MERGEWEASEL_E2E_IMAGE=<image> .venv/bin/python -m pytest e2e
"""
import dataclasses
import datetime
import os
import pathlib
import secrets
import subprocess
import urllib.request

import pytest

from driver import ACCOUNTS, ADMIN, ORGANIZATION, PASSWORD, TOKEN_SCOPES, Client, create_repo, eventually

GITEA_DOCKERFILE = pathlib.Path(__file__).with_name("gitea.Dockerfile")
GITEA_START_SECONDS = 120
# Every container of the suite carries it; one older than ORPHAN_AGE outlived a run - longer than
# the CI job may take - that died before its cleanup.
LABEL = "mergeweasel-e2e"
ORPHAN_AGE = datetime.timedelta(hours=1)
PASS_TIMEOUT_SECONDS = 300


@dataclasses.dataclass(frozen=True)
class Gitea:
    url: str
    clients: dict
    tokens: dict


def docker(*arguments, **options):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, **options)
    if result.returncode != 0:
        pytest.fail(f"docker {arguments[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def remove_orphans():
    now = datetime.datetime.now(datetime.timezone.utc)
    for container in docker("ps", "--all", "--quiet", "--filter", f"label={LABEL}").split():
        created = subprocess.run(["docker", "inspect", "--format", "{{.Created}}", container],
                                 capture_output=True, text=True).stdout.strip()
        if not created:
            continue
        started = datetime.datetime.fromisoformat(created[:19]).replace(tzinfo=datetime.timezone.utc)
        if now - started > ORPHAN_AGE:
            subprocess.run(["docker", "rm", "--force", container], capture_output=True)


def healthy(url):
    try:
        with urllib.request.urlopen(f"{url}/api/healthz", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


@pytest.fixture(scope="session")
def image():
    name = os.environ.get("MERGEWEASEL_E2E_IMAGE")
    if not name:
        # Not a skip: CI must not turn green without testing anything.
        pytest.fail("MERGEWEASEL_E2E_IMAGE is not set - build the image and name it (CONTRIBUTING.md)")
    return name


@pytest.fixture(scope="session")
def gitea(image):
    # Docker names carry the run id: the daemon may serve other runs at the same time.
    run_id = os.environ.get("GITHUB_RUN_ID") or f"local-{secrets.token_hex(3)}"
    container, tag = f"mergeweasel-e2e-gitea-{run_id}", f"mergeweasel-e2e-gitea:{run_id}"
    remove_orphans()
    # A re-run of a CI job keeps its run id, and with it the name.
    subprocess.run(["docker", "rm", "--force", container], capture_output=True)
    try:
        with GITEA_DOCKERFILE.open() as dockerfile:
            docker("build", "--quiet", "--tag", tag, "-", stdin=dockerfile)
        docker("run", "--detach", "--name", container, "--label", LABEL, tag)
        address = docker("inspect", "--format", "{{.NetworkSettings.Networks.bridge.IPAddress}}", container)
        url = f"http://{address}:3000"
        try:
            eventually(lambda: healthy(url), timeout=GITEA_START_SECONDS)
        except AssertionError:
            logs = subprocess.run(["docker", "logs", "--tail", "20", container], capture_output=True, text=True)
            pytest.fail(f"Gitea does not answer at {url} - it did not start, or this host is not on Docker's "
                        f"default bridge. Its log:\n{logs.stdout}{logs.stderr}")
        docker("exec", "--user", "git", container, "gitea", "admin", "user", "create", "--admin",
               "--username", ADMIN, "--password", PASSWORD, "--email", f"{ADMIN}@example.com",
               "--must-change-password=false")
        yield Gitea(url, *accounts(url))
    finally:
        subprocess.run(["docker", "rm", "--force", container], capture_output=True)
        subprocess.run(["docker", "rmi", "--force", tag], capture_output=True)


def accounts(url):
    admin = Client(url, basic=f"{ADMIN}:{PASSWORD}")
    tokens = {ADMIN: admin.call("POST", f"/users/{ADMIN}/tokens", {"name": "e2e", "scopes": ["all"]})["sha1"]}
    for account in ACCOUNTS:
        admin.call("POST", "/admin/users", {"username": account, "email": f"{account}@example.com",
                                            "password": PASSWORD, "must_change_password": False})
        own = Client(url, basic=f"{account}:{PASSWORD}")
        tokens[account] = own.call("POST", f"/users/{account}/tokens", {"name": "e2e", "scopes": TOKEN_SCOPES})["sha1"]
    clients = {account: Client(url, token=token) for account, token in tokens.items()}
    clients[ADMIN].call("POST", "/orgs", {"username": ORGANIZATION})
    return clients, tokens


@pytest.fixture
def repo(gitea, request):
    return create_repo(gitea.clients, request.node.name)


@pytest.fixture
def run_pass(gitea, image, repo):
    def run(mergeable=None):
        """One pass of the image under test, as mergeweasel-queue.yml runs it.

        No GITHUB_RUN_ID: there is no other pass to wait for. `mergeable` names a pull request whose
        mergeable check must settle first - otherwise Gitea defers the merge to a later pass.
        """
        if mergeable is not None:
            eventually(lambda: repo.pull(mergeable)["mergeable"])
        environment = {**os.environ, "GITHUB_SERVER_URL": gitea.url, "GITHUB_REPOSITORY": repo.name,
                       "MERGEWEASEL_TOKEN": gitea.tokens["queue"]}
        result = subprocess.run(
            ["docker", "run", "--rm", "--label", LABEL, "--env", "GITHUB_SERVER_URL", "--env", "GITHUB_REPOSITORY",
             "--env", "MERGEWEASEL_TOKEN", image, "mergeweasel", "pass"],
            env=environment, capture_output=True, text=True, timeout=PASS_TIMEOUT_SECONDS)
        # pytest shows it with a failing test: what the pass decided.
        print(f"pass:\n{result.stdout}{result.stderr}")
        assert result.returncode == 0, "the pass failed"
        return result.stdout
    return run
