# Contributing

## Setup and tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest
```

The package uses the standard library only; pytest is the one test dependency.

The end-to-end test runs passes of a built image against a throwaway Gitea in Docker. It reaches
that Gitea by its IP on Docker's default bridge: run it on a Linux host, or in a container on the
default bridge - not on Docker Desktop, whose host does not reach container IPs, and not in a CI job
on a network of its own:

```sh
docker build -t mergeweasel:dev .
MERGEWEASEL_E2E_IMAGE=mergeweasel:dev .venv/bin/python -m pytest e2e
```

## Layout

- `src/mergeweasel/`: `cli.py` (commands, environment), `gitea.py` (API client, paging, retries),
  `commit_rule.py` (what may land), `queue.py` (the pass), `check.py` (`check-commits`).
- `tests/`: pytest, one suite per module, on constructed state and a fake API client.
- `.gitea/workflows/`: the thin layer (`mergeweasel-queue.yml`, `mergeweasel-commits.yml`) this
  repository runs on, and `ci.yml`.
- `e2e/`: the end-to-end test - `gitea.Dockerfile` (the pinned Gitea), `driver.py` (the people
  and systems around the queue), `test_queue.py` (the scenarios).
- `docs/`: [design](docs/design.md) explains how a pass works and why.

Build the image locally with `docker build -t mergeweasel:dev .`.

## Pull requests

This repository lands its pull requests through its own queue, with the image version pinned in
its workflows. The pull request title and its `## Commit` section become the commit - the rules
are in [usage](docs/usage.md).

CI (`ci.yml`) runs on every pull request:

- `test` - the unit tests;
- `image` - builds the image, which then checks the pull request itself and dry-runs a pass with
  the run token;
- `e2e` - builds the image and runs the end-to-end test against it.

The required checks are `ci / test (pull_request)`, `ci / image (pull_request)`,
`ci / e2e (pull_request)` and `mergeweasel-commits / check (pull_request)`.

## Releases

A tag `vX.Y.Z` on `main` starts the `release` job of `ci.yml`: it builds the image and pushes it to
`ghcr.io/chli1/mergeweasel` as `X.Y.Z` and `latest`. Tags are set by hand for now. The first push of
a new package needs its visibility set to public once.

The repository needs two secrets:

- `MERGEWEASEL_TOKEN` - the queue account's token ([onboarding](docs/onboarding.md#the-queue-account));
- `GHCR_TOKEN` - a GitHub token with `write:packages` for the push to ghcr.io.
