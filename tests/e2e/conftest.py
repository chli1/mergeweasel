"""The end-to-end layer: Gitea runs this repository's workflows on a real runner."""
import pathlib
import re
import subprocess

import pytest

import runner as runners
from actions import CHECK_CONTEXT, CI_CONTEXT
from driver import ADMIN, create_repo
from workflow import IMAGE, WORKFLOWS

QUEUE_FILES = ("mergeweasel-queue.yml", "mergeweasel-commits.yml")


@pytest.fixture(scope="session")
def runner(server, run_id, built_image):
    with runners.start(server, run_id, built_image) as running:
        yield running


@pytest.fixture
def workflow_files(request, image):
    """The queue's and the commit check's workflow files, each with the image under test: this branch's,
    or in [upgrade] the newest release's."""
    if request.node.callspec.params["image"] == "upgrade":
        # The image fixture has failed [upgrade] already if the image names no revision.
        revision = request.getfixturevalue("released_revision")
        texts = {}
        for name in QUEUE_FILES:
            shown = subprocess.run(["git", "show", f"{revision}:.gitea/workflows/{name}"], cwd=WORKFLOWS,
                                   capture_output=True, text=True)
            if shown.returncode != 0:
                pytest.fail(f"[upgrade] cannot read {name} of the newest release {revision[:12]}: {shown.stderr.strip()}")
            texts[name] = shown.stdout
    else:
        texts = {name: (WORKFLOWS / name).read_text() for name in QUEUE_FILES}
    return {name: IMAGE.sub(lambda match: match.group(1) + image, text) for name, text in texts.items()}


@pytest.fixture
def actions_repo(server, runner, image, workflow_files, request):
    """A repository set up as docs/onboarding.md describes, running the workflow files under test."""
    name = re.sub(r"[^\w.-]+", "-", request.node.name).strip("-")
    repo = create_repo(server.clients, name, contexts=(CI_CONTEXT, CHECK_CONTEXT))
    admin = server.clients[ADMIN]
    for secret, value in {"MERGEWEASEL_MERGER_TOKEN": server.tokens["queue"],
                          "MERGEWEASEL_APPROVER_TOKENS": server.tokens["approver1"]}.items():
        admin.call("PUT", f"{repo.path}/actions/secrets/{secret}", {"data": value})
    files = {f".gitea/workflows/{name}": text for name, text in workflow_files.items()}
    files[".gitea/workflows/ci.yml"] = pathlib.Path(__file__).with_name("ci.yml").read_text().replace("__IMAGE__", image)
    repo.move_main(files, "ci: the queue")
    return repo
