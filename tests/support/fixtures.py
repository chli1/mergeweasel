"""The shared hooks and fixtures of both Docker layers: the throwaway forge, the images, the repository.

Run: MERGEWEASEL_E2E_IMAGE=<image> .venv/bin/python -m pytest tests/integration tests/e2e
"""

import os
import re
import secrets
import subprocess

import forge
import history
import pytest
import workflow
from driver import LABELS, create_repo
from server import docker, start

# [upgrade]: the newest release's workflow files with the built image - a user's setup after
# Renovate bumped the image.
VARIANTS = ["built", "released", "upgrade"]


# The marker that holds a variant back until the newest release contains the marker's commit: the new
# files need what the old image lacks, or the new image breaks the old files. [built] runs always.
MARKERS = {"released": "needs_new_image", "upgrade": "breaks_old_workflows"}


def skip_reason(variant, marked, unreleased):
    """Why a variant skips: marked with the marker that holds it back, and not released yet."""
    if variant not in MARKERS or not marked or not unreleased:
        return None
    return f"{MARKERS[variant]}: {unreleased}"


def missing_revision(variant, revision):
    """Why [upgrade] cannot run: it reads the newest release's workflow files at the revision its image names."""
    if variant == "upgrade" and not revision:
        return "the released image names no revision - [upgrade] cannot read its workflow files"
    return None


def pytest_generate_tests(metafunc):
    # Every scenario also runs with the newest release: its image, and in [upgrade] its workflow files.
    if "image" in metafunc.fixturenames:
        metafunc.parametrize("image", VARIANTS, indirect=True)


@pytest.fixture(scope="session")
def run_id():
    # Docker names carry the run id: the daemon may serve other runs at the same time, and the jobs
    # of one CI run as well - CI sets <run id>-<job>.
    return os.environ.get("MERGEWEASEL_E2E_RUN_ID") or f"local-{secrets.token_hex(3)}"


@pytest.fixture(scope="session")
def built_image():
    name = os.environ.get("MERGEWEASEL_E2E_IMAGE")
    if not name:
        # Not a skip: CI must not turn green without testing anything.
        pytest.fail("MERGEWEASEL_E2E_IMAGE is not set - build the image and name it (CONTRIBUTING.md)")
    return name


@pytest.fixture(scope="session")
def server(built_image, run_id):
    with start(forge.selected(), run_id) as running:
        yield running


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
    return docker(
        "image", "inspect", "--format", '{{index .Config.Labels "org.opencontainers.image.revision"}}', released_image
    )


@pytest.fixture
def image(request):
    """The image the test runs: the newest release in [released], else the built one."""
    if request.param == "upgrade":
        problem = missing_revision(request.param, request.getfixturevalue("released_revision"))
        if problem:
            # Not a skip, not even for a marked scenario: CI must not turn green untested.
            pytest.fail(problem)
    marker = MARKERS.get(request.param)
    marked = marker is not None and request.node.get_closest_marker(marker) is not None
    if marked:
        revision = request.getfixturevalue("released_revision")
        unreleased = (
            history.unreleased(request.function, revision, marker)
            if revision
            else "the released image names no revision"
        )
        reason = skip_reason(request.param, marked, unreleased)
        if reason:
            pytest.skip(reason)
    return request.getfixturevalue("released_image" if request.param == "released" else "built_image")


@pytest.fixture
def workflow_text(request, image):
    """Reads a workflow file, given by its path, as the variant runs it: this branch's, or in [upgrade] the
    newest release's."""
    if request.node.callspec.params["image"] != "upgrade":
        return lambda path: path.read_text()
    # The image fixture has failed [upgrade] already if the image names no revision.
    revision = request.getfixturevalue("released_revision")

    def read(path):
        name = path.relative_to(workflow.ROOT).as_posix()
        exists = subprocess.run(["git", "cat-file", "-e", revision], cwd=workflow.ROOT, capture_output=True)
        if (
            exists.returncode == 0
            and subprocess.run(
                ["git", "cat-file", "-e", f"{revision}:{name}"], cwd=workflow.ROOT, capture_output=True
            ).returncode
            != 0
        ):
            pytest.skip(f"[upgrade]: the newest release {revision[:12]} ships no {name} yet")
        shown = subprocess.run(["git", "show", f"{revision}:{name}"], cwd=workflow.ROOT, capture_output=True, text=True)
        if shown.returncode != 0:
            pytest.fail(f"[upgrade] cannot read {name} of the newest release {revision[:12]}: {shown.stderr.strip()}")
        return shown.stdout

    return read


@pytest.fixture
def instance(request):
    """The instance's names: (default branch, label names)."""
    marker = request.node.get_closest_marker("instance")
    return (marker.kwargs["branch"], marker.kwargs["labels"]) if marker else ("main", LABELS)


@pytest.fixture
def repo(server, request, instance):
    marker = request.node.get_closest_marker("required_approvals")
    branch, labels = instance
    # A parametrized test's name carries brackets, which a repository name may not.
    name = re.sub(r"[^\w.-]+", "-", request.node.name).strip("-")
    return create_repo(
        server.clients, name, required_approvals=marker.args[0] if marker else 1, branch=branch, labels=labels
    )
