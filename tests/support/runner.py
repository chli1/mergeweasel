"""A runner next to the forge: it runs the forge's workflows, each job in a sibling container."""

import contextlib
import dataclasses
import subprocess
from collections.abc import Iterator

from server import LABEL, Server, build, docker, names

# Jobs on the default bridge reach the forge as http://gitea:3000, its root URL. The label lets the orphan
# sweep find a job container that outlived its run.
CONFIG = """runner:
  capacity: 4
container:
  network: bridge
  options: --add-host=gitea:{ip} --label {label}
  force_pull: false
"""


@dataclasses.dataclass(frozen=True)
class Runner:
    container: str

    def log(self, lines: int = 40) -> str:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), self.container], capture_output=True, text=True
        )
        return result.stdout + result.stderr


@contextlib.contextmanager
def start(server: Server, run_id: str, image: str) -> Iterator[Runner]:
    """image: the default job image, ubuntu-latest - the built image, which exists only locally."""
    named = names(server.forge, run_id)
    subprocess.run(["docker", "rm", "--force", named.runner], capture_output=True)
    token = docker("exec", "--user", "git", server.container, "gitea", "actions", "generate-runner-token").splitlines()[
        -1
    ]
    script = (
        'printf "%s" "$CONFIG" > /tmp/config.yaml && cd /tmp && '
        f"{server.forge.runner_command} register --no-interactive "
        f'--instance {server.url} --token "$TOKEN" --name {named.runner} '
        f"--labels ubuntu-latest:docker://{image} --config /tmp/config.yaml "
        f"&& exec {server.forge.runner_command} daemon --config /tmp/config.yaml"
    )
    try:
        build(server.forge.runner_dockerfile, named.runner_tag)
        # Root: the Docker socket belongs to root.
        docker(
            "run",
            "--detach",
            "--name",
            named.runner,
            "--label",
            LABEL,
            "--user",
            "0",
            "--volume",
            "/var/run/docker.sock:/var/run/docker.sock",
            "--env",
            f"CONFIG={CONFIG.format(ip=server.ip, label=LABEL)}",
            "--env",
            f"TOKEN={token}",
            "--entrypoint",
            "sh",
            named.runner_tag,
            "-c",
            script,
        )
        yield Runner(named.runner)
    finally:
        subprocess.run(["docker", "rm", "--force", named.runner], capture_output=True)
        subprocess.run(["docker", "rmi", "--force", named.runner_tag], capture_output=True)
        # This server's job containers and volumes: a runner removed mid-job leaves them.
        task = f"name={server.forge.job_prefix}-{server.task_prefix}"
        listings = {
            "container": ["docker", "container", "ls", "--all", "--quiet", "--filter", task],
            "volume": ["docker", "volume", "ls", "--quiet", "--filter", task],
        }
        for kind, listing in listings.items():
            for name in subprocess.run(listing, capture_output=True, text=True).stdout.split():
                subprocess.run(["docker", kind, "rm", "--force", name], capture_output=True)
