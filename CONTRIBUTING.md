# Contributing

## Setup and tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest
```

The package uses the standard library only; pytest is the one test dependency.

The end-to-end test runs passes of a built image against a throwaway Gitea in Docker:

```sh
docker build -t mergeweasel:dev .
MERGEWEASEL_E2E_IMAGE=mergeweasel:dev .venv/bin/python -m pytest e2e
```

The test reaches that Gitea by its IP address on Docker's default bridge network. Run it on a Linux
host, or in a container on the default bridge. Docker Desktop does not work, because its host
cannot reach container IP addresses; neither does a CI job on a network of its own.

## Layout

- `src/mergeweasel/`:
  - `cli.py` - the commands and the environment;
  - `gitea.py` - the API client, with paging and retries;
  - `commit_rule.py` - what may land on `main`;
  - `queue.py` - the queue pass;
  - `check.py` - `check-commits`;
  - `release.py` - the release step;
  - `version_template.py` - the release tag template;
  - `config.py` - reading `mergeweasel.toml`.
- `tests/`: pytest, one suite per module, on constructed state and a fake API client.
- `.gitea/workflows/`: the two workflows this repository runs on (`mergeweasel-queue.yml`,
  `mergeweasel-commits.yml`), and `ci.yml`.
- `e2e/`: the end-to-end test - `gitea.Dockerfile` (the pinned Gitea), `driver.py` (the people
  and systems around the queue), `test_queue.py` and `test_release.py` (the scenarios).
- `docs/`: [design](docs/design.md) explains how a pass works and why.

## Pull requests

This repository lands its pull requests through its own queue, with the image version pinned in
its workflows. The pull request title and its `## Commit` section become the commit; the rules are
in [usage](docs/usage.md).

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
which builds the image and pushes it to `docker.io/chli1ro/mergeweasel` as `X.Y.Z` and `latest`.

The repository needs three secrets:

- `MERGEWEASEL_MERGER_TOKEN` - the merger's token ([onboarding](docs/onboarding.md#the-accounts));
- `MERGEWEASEL_APPROVER_TOKENS` - the approver accounts' tokens, one per line;
- `DOCKERHUB_TOKEN` - a Docker Hub access token of `chli1ro` with write access to the repository.
