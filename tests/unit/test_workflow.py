"""Reading mergeweasel-queue.yml: what it hands a pass. No Gitea needed."""

import pytest
import workflow
import yaml

CONTEXTS = {"vars": {"LABEL": "merge-me"}, "secrets": {"TOKEN": "t0ken"}}


def write(tmp_path, job_env, step_env, run="mergeweasel pass"):
    path = tmp_path / "mergeweasel-queue.yml"
    path.write_text(f"""
on:
  workflow_dispatch:
jobs:
  pass:
    container:
      image: docker.io/chli1ro/mergeweasel:1.0.0@sha256:abc
    env: {job_env}
    steps:
      - if: ${{{{ !inputs.dry_run }}}}
        env: {step_env}
        run: {run}
      - if: ${{{{ inputs.dry_run }}}}
        env: {{ DRY: '1' }}
        run: mergeweasel pass --dry-run
""")
    return path


def test_reads_the_image_and_the_real_pass(tmp_path):
    queue = workflow.read(write(tmp_path, "{ A: '${{ vars.LABEL }}' }", "{ B: '${{ secrets.TOKEN }}' }"))

    assert queue.image == "docker.io/chli1ro/mergeweasel:1.0.0@sha256:abc"
    assert queue.command == ["mergeweasel", "pass"]
    assert queue.resolve(CONTEXTS) == {"A": "merge-me", "B": "t0ken"}


def test_the_step_env_wins_and_literals_pass_through(tmp_path):
    queue = workflow.read(write(tmp_path, "{ A: job, C: '${{vars.LABEL}}' }", "{ A: step }"))

    assert queue.resolve(CONTEXTS) == {"A": "step", "C": "merge-me"}


def test_the_repositorys_own_workflow_is_readable():
    queue = workflow.read()

    assert queue.image.startswith("docker.io/chli1ro/mergeweasel:")
    assert queue.command == ["mergeweasel", "pass"]
    assert {"MERGEWEASEL_MERGER_TOKEN", "MERGEWEASEL_APPROVER_TOKENS"} <= set(queue.environment)


@pytest.mark.parametrize("value", ["${{ vars.LABEL || 'automerge' }}", "x-${{ vars.LABEL }}", "${{ github.ref }}"])
def test_an_expression_the_test_cannot_evaluate_fails(tmp_path, value):
    queue = workflow.read(write(tmp_path, f'{{ A: "{value}" }}', "{}"))

    with pytest.raises(ValueError, match="A"):
        queue.resolve(CONTEXTS)


def test_a_name_the_test_does_not_supply_fails(tmp_path):
    queue = workflow.read(write(tmp_path, "{ A: '${{ vars.UNKNOWN }}' }", "{}"))

    with pytest.raises(ValueError, match="vars.UNKNOWN"):
        queue.resolve(CONTEXTS)


def test_not_exactly_one_real_pass_step_fails(tmp_path):
    path = write(tmp_path, "{}", "{}")
    path.write_text(path.read_text().replace("${{ inputs.dry_run }}", "${{ !inputs.dry_run }}"))

    with pytest.raises(ValueError, match="2"):
        workflow.read(path)


def test_each_job_of_the_queue_workflows_names_the_image_once():
    for path in (workflow.QUEUE_WORKFLOW, workflow.COMMITS_WORKFLOW, workflow.FORGEJO_QUEUE_WORKFLOW):
        text = path.read_text()
        assert len(workflow.IMAGE.findall(text)) == len(yaml.safe_load(text)["jobs"]), path.name


def test_forgejos_queue_is_giteas_polling_instead_of_workflow_run():
    gitea = yaml.safe_load(workflow.QUEUE_WORKFLOW.read_text())
    forgejo = yaml.safe_load(workflow.FORGEJO_QUEUE_WORKFLOW.read_text())
    # YAML 1.1 reads the key on as True.
    gitea_triggers, forgejo_triggers = gitea.pop(True), forgejo.pop(True)
    assert forgejo == gitea
    assert forgejo_triggers.pop("schedule") == [{"cron": "*/5 * * * *"}]
    assert "workflow_run" in gitea_triggers
    del gitea_triggers["workflow_run"]
    assert forgejo_triggers == gitea_triggers
