"""The throwaway Gitea both Docker layers run against."""
import contextlib
import dataclasses
import datetime
import pathlib
import re
import secrets
import subprocess
import urllib.request

import pytest

from driver import ACCOUNTS, ADMIN, ORGANIZATION, PASSWORD, TOKEN_SCOPES, Client, eventually

GITEA_START_SECONDS = 120
# Every container of the suite carries it; one older than ORPHAN_AGE outlived a run - longer than
# the CI job may take - that died before its cleanup.
LABEL = "mergeweasel-e2e"
ORPHAN_AGE = datetime.timedelta(hours=1)
DATABASE = "/data/gitea/gitea.db"
# A job volume of this suite: its task id has the offset's 13 digits, the host's own runners' far fewer.
JOB_VOLUME = re.compile(r"^GITEA-ACTIONS-TASK-\d{13}-")


@dataclasses.dataclass(frozen=True)
class Names:
    """The Docker names of one run: the daemon may serve other runs at the same time."""
    server: str
    server_tag: str
    runner: str
    runner_tag: str


def names(run_id):
    return Names(f"mergeweasel-e2e-gitea-{run_id}", f"mergeweasel-e2e-gitea:{run_id}",
                 f"mergeweasel-e2e-runner-{run_id}", f"mergeweasel-e2e-runner:{run_id}")


@dataclasses.dataclass(frozen=True)
class Server:
    url: str
    ip: str
    container: str
    clients: dict
    tokens: dict
    # The leading digits of every Actions task id of this server.
    task_prefix: str


def docker(*arguments, **options):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, **options)
    if result.returncode != 0:
        pytest.fail(f"docker {arguments[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def build(dockerfile, tag):
    """An image from one of the Dockerfiles next to this file, built without a context."""
    with pathlib.Path(__file__).with_name(dockerfile).open() as file:
        docker("build", "--quiet", "--tag", tag, "-", stdin=file)


def task_offset(container):
    """Starts the server's Actions task ids at a random offset; returns its leading digits.

    A job's container and volume are named GITEA-ACTIONS-TASK-<task id>-WORKFLOW-...-JOB-..., and the
    runner removes a container of that name before it starts its own: two runs on one daemon that both
    count from 1 would remove each other's jobs.
    """
    prefix = secrets.randbelow(9 * 10**6) + 10**6
    docker("exec", "--user", "git", container, "sqlite3", DATABASE,
           f"INSERT INTO sqlite_sequence (name, seq) VALUES ('action_task', {prefix * 10**6})")
    return str(prefix)


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
    # Job volumes carry no label; their names tell them apart.
    for volume in docker("volume", "ls", "--quiet", "--filter", "name=GITEA-ACTIONS-TASK-").split():
        if not JOB_VOLUME.match(volume):
            continue
        created = subprocess.run(["docker", "volume", "inspect", "--format", "{{.CreatedAt}}", volume],
                                 capture_output=True, text=True).stdout.strip()
        if created and now - datetime.datetime.fromisoformat(created[:19]).replace(tzinfo=datetime.timezone.utc) > ORPHAN_AGE:
            subprocess.run(["docker", "volume", "rm", "--force", volume], capture_output=True)


def healthy(url):
    try:
        with urllib.request.urlopen(f"{url}/api/healthz", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


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


@contextlib.contextmanager
def start(run_id):
    named = names(run_id)
    remove_orphans()
    # A re-run of a CI job keeps its run id, and with it the name.
    subprocess.run(["docker", "rm", "--force", named.server], capture_output=True)
    try:
        build("gitea.Dockerfile", named.server_tag)
        docker("run", "--detach", "--name", named.server, "--label", LABEL, named.server_tag)
        ip = docker("inspect", "--format", "{{.NetworkSettings.Networks.bridge.IPAddress}}", named.server)
        url = f"http://{ip}:3000"
        try:
            eventually(lambda: healthy(url), timeout=GITEA_START_SECONDS)
        except AssertionError:
            logs = subprocess.run(["docker", "logs", "--tail", "20", named.server], capture_output=True, text=True)
            pytest.fail(f"Gitea does not answer at {url} - it did not start, or this host is not on Docker's "
                        f"default bridge. Its log:\n{logs.stdout}{logs.stderr}")
        docker("exec", "--user", "git", named.server, "gitea", "admin", "user", "create", "--admin",
               "--username", ADMIN, "--password", PASSWORD, "--email", f"{ADMIN}@example.com",
               "--must-change-password=false")
        yield Server(url, ip, named.server, *accounts(url), task_offset(named.server))
    finally:
        subprocess.run(["docker", "rm", "--force", named.server], capture_output=True)
        subprocess.run(["docker", "rmi", "--force", named.server_tag], capture_output=True)
