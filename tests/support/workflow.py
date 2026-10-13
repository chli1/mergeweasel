"""What mergeweasel-queue.yml hands a pass: the image, the environment and the command."""
import dataclasses
import pathlib
import re
import shlex

import yaml

WORKFLOWS = pathlib.Path(__file__).parents[2] / ".gitea" / "workflows"
QUEUE_WORKFLOW = WORKFLOWS / "mergeweasel-queue.yml"
# A workflow's mergeweasel image; the end-to-end test puts the image under test in its place.
IMAGE = re.compile(r"(image:\s*)docker\.io/chli1ro/mergeweasel\S*")
# The step of job `pass` that runs the real pass; the other one is the dry run.
PASS_STEP_CONDITION = "${{ !inputs.dry_run }}"
EXPRESSION = re.compile(r"\$\{\{\s*(vars|secrets)\.(\w+)\s*\}\}")


@dataclasses.dataclass(frozen=True)
class QueueWorkflow:
    image: str
    # Name to value as written in the file: a literal or one ${{ vars.X }} / ${{ secrets.X }}.
    environment: dict
    command: list

    def resolve(self, contexts):
        """The environment with each expression looked up in contexts ({"vars": {...}, "secrets": {...}}).

        An expression of another shape, or a name contexts lack, fails: a new one in the workflow must
        reach the test, not pass untested.
        """
        resolved = {}
        for name, value in self.environment.items():
            value = str(value)
            if "${{" in value:
                match = EXPRESSION.fullmatch(value)
                if not match:
                    raise ValueError(f"{name}: the test cannot evaluate {value!r}")
                context, key = match.groups()
                if key not in contexts[context]:
                    raise ValueError(f"{name}: the test supplies no {context}.{key}")
                value = contexts[context][key]
            resolved[name] = value
        return resolved


def read(path=QUEUE_WORKFLOW):
    job = yaml.safe_load(path.read_text())["jobs"]["pass"]
    steps = [step for step in job["steps"] if step.get("if") == PASS_STEP_CONDITION]
    if len(steps) != 1:
        raise ValueError(f"{path.name}: expected one step with if: {PASS_STEP_CONDITION}, found {len(steps)}")
    step = steps[0]
    return QueueWorkflow(job["container"]["image"], {**job.get("env", {}), **step.get("env", {})},
                         shlex.split(step["run"]))
