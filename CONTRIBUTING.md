# Contributing

## Setup and tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest
```

The package uses the standard library only. The tests need pytest, and the end-to-end test also
needs PyYAML to read the workflow files.

The end-to-end test runs passes of a built image against a throwaway Gitea in Docker:

```sh
docker build -t mergeweasel:dev .
MERGEWEASEL_E2E_IMAGE=mergeweasel:dev .venv/bin/python -m pytest e2e
```

The test reaches that Gitea by its IP address on Docker's default bridge network. Run it on a Linux
host, or in a container on the default bridge. Docker Desktop does not work, because its host
cannot reach container IP addresses; neither does a CI job on a network of its own.

Each pass gets the environment and the command that `mergeweasel-queue.yml` gives it
([the image and its workflows](docs/design.md#the-image-and-its-workflows)): the job's and the real
pass step's `env`, with the test's values for each `${{ vars.X }}` and `${{ secrets.X }}`. An
expression of any other shape, or a variable or secret the test does not supply, fails the test, so
a change to the workflow cannot slip past it.

Every scenario runs twice, as `[built]` against the built image and as `[released]` against the
newest release, the `latest` image the workflow runs, which proves that the workflow files keep
working with the previous release. Every run pulls that image from Docker Hub, because `latest`
moves with each release; the CI job keeps the five newest release images on the runners' Docker
daemon.

A scenario that needs something the newest release does not have yet - a new option, a changed
behaviour - carries `@pytest.mark.needs_this_commit`. Its `[released]` variant is skipped until the
newest release contains the commit that added the marker line, found with `git blame`; from then
on it runs against both images by itself, so the marker never needs removing. Touching the marker
line later moves that commit forward and only delays the `[released]` run. When the history cannot
answer - an uncommitted marker, a shallow clone, a released image without a revision label - the
`[released]` variant is skipped too, and the skip names the reason (`pytest -rs` shows it).

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
- `tests/`: pytest, one suite per module, on constructed state and a client faked with
  `mock.create_autospec`; only `test_gitea.py` fakes HTTP.
- `.gitea/workflows/`: the two workflows this repository runs on (`mergeweasel-queue.yml`,
  `mergeweasel-commits.yml`), which each release also publishes as the template, `ci.yml` and
  `mirror.yml`.
- `e2e/`: the end-to-end test - `gitea.Dockerfile` (the pinned Gitea), `driver.py` (the people
  and systems around the queue), `workflow.py` (what `mergeweasel-queue.yml` hands a pass),
  `history.py` (when a `needs_this_commit` scenario may run against the newest release),
  `test_queue.py`, `test_instance.py` and `test_release.py` (the scenarios).
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
- `e2e` - builds the image and runs the end-to-end test against it.

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
