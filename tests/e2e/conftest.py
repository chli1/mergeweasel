"""The end-to-end layer: the forge runs this repository's workflows on a real runner."""
import pathlib
import re

import pytest

import runner as runners
import workflow
from actions import CHECK_CONTEXT, CI_CONTEXT
from driver import ADMIN, create_repo


@pytest.fixture(scope="session")
def runner(server, run_id, built_image):
    with runners.start(server, run_id, built_image) as running:
        yield running


@pytest.fixture
def workflow_files(workflow_text, image, server):
    """The queue's and the commit check's workflow files as the variant runs them, each with the image
    under test, the queue's the forge's own."""
    sources = {"mergeweasel-queue.yml": server.forge.queue_source,
               "mergeweasel-commits.yml": workflow.COMMITS_WORKFLOW}
    files = {name: workflow.IMAGE.sub(lambda match: match.group(1) + image, workflow_text(path))
             for name, path in sources.items()}
    files["mergeweasel-queue.yml"] = server.forge.queue_workflow(files["mergeweasel-queue.yml"])
    return files


@pytest.fixture
def actions_repo(server, runner, image, workflow_files, request):
    """A repository set up as docs/onboarding.md describes, running the workflow files under test."""
    name = re.sub(r"[^\w.-]+", "-", request.node.name).strip("-")
    repo = create_repo(server.clients, name, contexts=(CI_CONTEXT, CHECK_CONTEXT))
    admin = server.clients[ADMIN]
    for secret, value in {"MERGEWEASEL_MERGER_TOKEN": server.tokens["queue"],
                          "MERGEWEASEL_APPROVER_TOKENS": server.tokens["approver1"]}.items():
        admin.call("PUT", f"{repo.path}/actions/secrets/{secret}", {"data": value})
    directory = server.forge.workflow_directory
    files = {f"{directory}/{name}": text for name, text in workflow_files.items()}
    ci = pathlib.Path(__file__).with_name("ci.yml").read_text()
    files[f"{directory}/ci.yml"] = (ci.replace("__IMAGE__", image)
                                    .replace("__CI_SLOW_SECONDS__", str(server.forge.ci_slow_seconds)))
    repo.move_main(files, "ci: the queue")
    return repo
