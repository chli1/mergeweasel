# Contributing

## Setup and tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest
```

The package uses the standard library only. The tests need pytest, and the Docker layers also need
PyYAML to read the workflow files.

The tests come in three layers, one directory each under `tests/`:

- `tests/unit/` checks each module on constructed state, with the Gitea client faked. A plain
  `pytest` runs this layer and nothing else; it needs neither Docker nor a network.
- `tests/integration/` runs one queue pass at a time against a throwaway Gitea in Docker. The test
  itself starts each pass, at the moment it chooses, so every scenario can check exactly what one
  pass decided: the rebase, the stale approval, `needs-fix`, `prio`, `keep-commits`, the release.
- `tests/e2e/` lets that Gitea run this repository's workflow files on a real runner
  (`act_runner`, next to Gitea in Docker). Nobody starts a pass: the scenarios open, approve and
  label pull requests the way people do, and check that the workflows land them - the label added
  last, CI finishing last, a moved `main`, red CI, two pull requests in a row. This layer is the
  one that proves the triggers (`workflow_run`, `pull_request_target`), the jobs' `if:` and the
  runner's view of the workflow files.

Both Docker layers run against an image built from the working tree:

```sh
docker build -t mergeweasel:dev .
MERGEWEASEL_E2E_IMAGE=mergeweasel:dev .venv/bin/python -m pytest tests/integration tests/e2e
```

They run against Gitea by default. `MERGEWEASEL_E2E_FORGE=forgejo` runs the integration layer
against Forgejo instead:

```sh
MERGEWEASEL_E2E_FORGE=forgejo MERGEWEASEL_E2E_IMAGE=mergeweasel:dev .venv/bin/python -m pytest tests/integration
```

What tells the two forges apart lives in `tests/support/forge.py`: one class per server, `Gitea`
and `Forgejo`, each implementing the abstract `Forge`. A class names its Dockerfile and implements
the behaviour the scenarios see differently, such as `squash_message()`: Gitea appends a
`Co-authored-by` trailer to a squash commit, Forgejo does not. A scenario calls the forge's method
instead of branching on its name, and a new difference becomes a new method on `Forge`. The end-to-end layer needs `workflow_run`, which Forgejo
lacks, so it runs against Gitea only.

They reach Gitea by its IP address on Docker's default bridge network, and the runner reaches the
Docker daemon through `/var/run/docker.sock`, where it starts each job as a container next to
Gitea. Run them on a Linux host, or in a container on the default bridge with the daemon's socket.
Docker Desktop does not work, because its host cannot reach container IP addresses; neither does a
CI job on a network of its own. Several runs can share one daemon: Gitea's and the runner's
containers and images carry the run id (`MERGEWEASEL_E2E_RUN_ID`, a random one outside CI), and each
Gitea numbers its Actions tasks from a random offset, so the job containers and volumes the runner
names after them differ too. A run that dies before its cleanup leaves them behind; the next run
removes what is older than an hour.

In the integration layer each pass gets the environment and the command that `mergeweasel-queue.yml`
gives it ([the image and its workflows](docs/design.md#the-image-and-its-workflows)): the job's and
the real pass step's `env`, with the test's values for each `${{ vars.X }}` and `${{ secrets.X }}`.
An expression of any other shape, or a variable or secret the test does not supply, fails the test,
so a change to the workflow cannot slip past it.

Every scenario runs in several variants, each standing for a setup that exists out there:

- `[built]` - this branch's workflow files with the image built from it: the next release.
- `[released]` - this branch's workflow files with the newest release, the `latest` image: this
  repository itself between merging a change and its release, because it runs the files on `main`
  with `latest`.
- `[upgrade]` - the newest release's workflow files, read from the git history at the revision its
  image names, with the built image: a consumer after Renovate bumped the image pin in their copies
  of the workflow files, which it leaves unchanged otherwise. In the integration layer each pass
  gets the environment and the command of the release's `mergeweasel-queue.yml`, so the scenarios
  with their own label names or release settings test the old interface as well.

Every run pulls the newest release from Docker Hub, because `latest` moves with each release.

A failing variant tells which side of a change is ahead of the other, and each side has its own
marker. Both skip only their own variant, only until the newest release contains the commit that
added the marker line (found with `git blame`), and never `[built]`, which tests the change itself:

- `@pytest.mark.needs_new_image` - `[released]` fails: the new workflow files need something the
  newest release's image does not have yet, such as a new option or a changed behaviour. This is
  the usual case for a new feature; this repository runs such files on `latest` only between the
  merge and the release.
- `@pytest.mark.breaks_old_workflows` - `[upgrade]` fails: the new image no longer works with the
  newest release's workflow files. That is a breaking change, made on purpose or not at all.

A change that fails `[upgrade]` breaks consumers' existing workflow files. Either keep it
compatible, or make it a breaking change on purpose: mark the commit breaking (`!`), say in its
`## Commit` section what consumers change in their copies of `mergeweasel-*.yml`, and mark the
failing scenarios `breaks_old_workflows`. The release is then a new major version, which Renovate
does not merge on its own - its notes reach consumers before their queue runs the new image.

From the release on, a marked scenario runs in every variant by itself, so a marker never needs
removing. Touching the marker line later moves its commit forward and only delays its variant. When
the history cannot answer - an uncommitted marker, a shallow clone, a released image without a
revision label - the variant is skipped too, and the skip names the reason (`pytest -rs` shows it).
The one exception is `[upgrade]` with a released image that names no revision: it cannot read the
release's workflow files at all, so it fails, marked or not, instead of turning CI green
untested.

## Layout

- `src/mergeweasel/`:
  - `cli.py` - the commands and the environment;
  - `gitea.py` - the API client: one method per operation, typed values, domain exceptions,
    paging and retries; the only module that knows the Gitea API;
  - `commit_rule.py` - what may land on `main`;
  - `queue.py` - the queue pass;
  - `accounts.py` - the merger and the approver accounts, and their dry-run stand-ins;
  - `turn.py` - serializing the passes;
  - `check.py` - `check-commits`;
  - `release.py` - the release step;
  - `version_template.py` - the release tag template;
  - `subject_template.py` - the subject templates;
  - `config.py` - reading `mergeweasel.toml`.
- `tests/unit/`: pytest, one suite per module, on constructed state and a client faked with
  `mock.create_autospec`; only `test_gitea.py` fakes HTTP.
- `.gitea/workflows/`: the two workflows this repository runs on (`mergeweasel-queue.yml`,
  `mergeweasel-commits.yml`), which each release also publishes as the template, `ci.yml`,
  `ci-cleanup.yml` and `mirror.yml`.
- `tests/integration/`: one pass at a time against a throwaway Gitea - `test_queue.py`,
  `test_instance.py` and `test_release.py` (the scenarios).
- `tests/e2e/`: the workflows on a real runner - `ci.yml` (the test repository's CI, steered by
  the pull request body), `test_queue.py` (the scenarios), `test_runner.py` (a job reaches Gitea).
- `tests/support/`: what the Docker layers share - `gitea.Dockerfile`, `forgejo.Dockerfile` and
  `gitea-runner.Dockerfile` (the pinned forges and runner), `forge.py` (what tells the forges
  apart), `server.py` (starting the forge), `runner.py` (starting the runner),
  `fixtures.py` (the shared fixtures and variants), `driver.py` (the people and systems around the
  queue), `actions.py` (waiting for the workflows), `workflow.py` (what the workflow files hand a
  pass), `history.py` (when a marked scenario may run with the newest release).
- `docs/`: the user documentation, listed in the [README](README.md#documentation);
  [design](docs/design.md) explains how a pass works and why.

## Pull requests

This repository lands its pull requests through its own queue, running mergeweasel's `latest`
image. The pull request title and its `## Commit` section become the commit; the rules are
in [usage](docs/usage.md), with this repository's own forms from `mergeweasel.toml`: the title
ends in its issue, `(#N)`, or in its own number, `(PR #N)`, and nothing is appended.

CI (`ci.yml`) runs on every pull request:

- `test` - the unit tests;
- `image` - builds the image, which then checks the pull request itself and dry-runs a pass with
  the run token;
- `layers` - one job per Docker layer, variant and forge, such as `integration (built, gitea)`
  or `e2e (upgrade, gitea)`: each builds the image and runs that variant of the layer against
  that forge. Forgejo runs the integration layer's `[built]` variant only. The
  `[built]` jobs also run the tests without a variant, such as the runner probe of
  `tests/e2e/test_runner.py`;
- `e2e` - passes once every `layers` job passed. It is the one required check of the Docker
  layers, so their jobs can change without touching the branch protection.

The Docker layers' jobs run in parallel, on as many runners as are free. Each starts its own forge
and names its containers and images `<run id>-<layer>-<variant>-<forge>` (`MERGEWEASEL_E2E_RUN_ID`).

A new push to a pull request cancels the run still going for its previous head. The runner skips
every remaining step of a cancelled run, so `ci` cleans up nothing itself: `ci-cleanup.yml` runs
after each finished `ci` run, cancelled or not, and removes the Docker containers and images
named after its run id.

The required checks are `ci / test (pull_request)`, `ci / image (pull_request)`,
`ci / e2e (pull_request)` and `mergeweasel-commits / check (pull_request)`.

## Releases

The queue sets the tags: every merge with a releasing commit gets the next version
([design](docs/design.md#releases)). A tag `vX.Y.Z` on `main` starts the `release` job of `ci.yml`,
which builds the image and pushes it to `docker.io/chli1ro/mergeweasel` as `X.Y.Z` and `latest`,
attaches the workflow templates to the release, and publishes the same release on the GitHub
mirror. Every push to `main` starts `mirror.yml`, which pushes `main` and the release tags to
[github.com/chli1/mergeweasel](https://github.com/chli1/mergeweasel). Both GitHub steps do nothing
in a repository without the token secret they read.
