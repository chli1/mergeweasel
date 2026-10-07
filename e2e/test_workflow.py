"""Reading mergeweasel-queue.yml: what it hands a pass. No Gitea needed."""
import pytest

import workflow

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


def test_reads_the_pinned_image_and_the_real_pass(tmp_path):
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
    queue = workflow.read(write(tmp_path, f"{{ A: \"{value}\" }}", "{}"))

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
