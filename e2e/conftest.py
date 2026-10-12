"""The throwaway Gitea and the pass under test.

Run: MERGEWEASEL_E2E_IMAGE=<image> .venv/bin/python -m pytest e2e
"""
import dataclasses
import datetime
import os
import pathlib
import re
import secrets
import subprocess
import urllib.request

import pytest

import history
import workflow
from driver import ACCOUNTS, ADMIN, LABELS, ORGANIZATION, PASSWORD, TOKEN_SCOPES, Client, create_repo, eventually

GITEA_DOCKERFILE = pathlib.Path(__file__).with_name("gitea.Dockerfile")
GITEA_START_SECONDS = 120
# Every container of the suite carries it; one older than ORPHAN_AGE outlived a run - longer than
# the CI job may take - that died before its cleanup.
LABEL = "mergeweasel-e2e"
ORPHAN_AGE = datetime.timedelta(hours=1)
PASS_TIMEOUT_SECONDS = 300


def pytest_configure(config):
    config.addinivalue_line("markers", "required_approvals(n): the repository's required approvals, 1 by default")
    config.addinivalue_line("markers", "instance(branch, labels): the default branch and the automerge, needs-fix, "
                                       "keep-commits and prio labels' names, set as the passes' variables")
    config.addinivalue_line("markers", "needs_this_commit: needs what the released image lacks - not run against it "
                                       "until the newest release contains the commit that added this marker; a comment on its "
                                       "line names what the test needs")


def pytest_generate_tests(metafunc):
    # Every scenario also runs against the newest release: the workflow files must keep working with it.
    if "image" in metafunc.fixturenames:
        metafunc.parametrize("image", ["built", "released"], indirect=True)


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
def built_image():
    name = os.environ.get("MERGEWEASEL_E2E_IMAGE")
    if not name:
        # Not a skip: CI must not turn green without testing anything.
        pytest.fail("MERGEWEASEL_E2E_IMAGE is not set - build the image and name it (CONTRIBUTING.md)")
    return name


@pytest.fixture(scope="session")
def queue_workflow():
    return workflow.read()


@pytest.fixture(scope="session")
def released_image(queue_workflow):
    """The newest release, which the workflow runs: the files on main must keep working with it.

    Pulled on every run, because its tag moves with each release.
    """
    docker("pull", "--quiet", queue_workflow.image)
    return queue_workflow.image


@pytest.fixture(scope="session")
def released_revision(released_image):
    """The commit the newest release was built from, or "" if its image does not say."""
    return docker("image", "inspect", "--format", '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                  released_image)


@pytest.fixture
def image(request):
    """The image run_pass starts: the built one, and the newest release in a test's [released] variant."""
    if request.param == "released" and request.node.get_closest_marker("needs_this_commit"):
        revision = request.getfixturevalue("released_revision")
        reason = history.unreleased(request.function, revision) if revision else "the released image names no revision"
        if reason:
            pytest.skip(f"needs_this_commit: {reason}")
    return request.getfixturevalue(f"{request.param}_image")


@pytest.fixture(scope="session")
def gitea(built_image):
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
def instance(request):
    """The instance's names: (default branch, label names)."""
    marker = request.node.get_closest_marker("instance")
    return (marker.kwargs["branch"], marker.kwargs["labels"]) if marker else ("main", LABELS)


@pytest.fixture
def repo(gitea, request, instance):
    marker = request.node.get_closest_marker("required_approvals")
    branch, labels = instance
    # A parametrized test's name carries brackets, which a repository name may not.
    name = re.sub(r"[^\w.-]+", "-", request.node.name).strip("-")
    return create_repo(gitea.clients, name, required_approvals=marker.args[0] if marker else 1,
                       branch=branch, labels=labels)


@pytest.fixture
def run_pass(gitea, image, queue_workflow, repo, instance):
    def run(mergeable=None, approvers=("queue",)):
        """One pass, with the environment and the command mergeweasel-queue.yml gives it.

        No GITHUB_RUN_ID: there is no other pass to wait for. `mergeable` names a pull request whose
        mergeable check must settle first - otherwise Gitea defers the merge to a later pass.
        `approvers` default: the merger's own token, one account in both roles.
        """
        if mergeable is not None:
            eventually(lambda: repo.pull(mergeable)["mergeable"])
        automerge, needs_fix, keep_commits, prio = instance[1]
        # The repository's Actions variables and secrets; the default branch is the repository's own.
        contexts = {
            "vars": {"MERGEWEASEL_AUTOMERGE_LABEL": automerge, "MERGEWEASEL_NEEDS_FIX_LABEL": needs_fix,
                     "MERGEWEASEL_KEEP_COMMITS_LABEL": keep_commits, "MERGEWEASEL_PRIO_LABEL": prio,
                     "MERGEWEASEL_RENOVATE_ACCOUNT": "renovate"},
            "secrets": {"MERGEWEASEL_MERGER_TOKEN": gitea.tokens["queue"],
                        "MERGEWEASEL_APPROVER_TOKENS": "\n".join(gitea.tokens[account] for account in approvers)},
        }
        # The runner's context, which the workflow does not spell out.
        passed = {"GITHUB_SERVER_URL": gitea.url, "GITHUB_REPOSITORY": repo.name, **queue_workflow.resolve(contexts)}
        # By name only: the tokens stay off the command line.
        options = [option for name in passed for option in ("--env", name)]
        result = subprocess.run(
            ["docker", "run", "--rm", "--label", LABEL, *options, image, *queue_workflow.command],
            env={**os.environ, **passed}, capture_output=True, text=True, timeout=PASS_TIMEOUT_SECONDS)
        # pytest shows it with a failing test: what the pass decided.
        print(f"pass:\n{result.stdout}{result.stderr}")
        assert result.returncode == 0, "the pass failed"
        return result.stdout
    return run
