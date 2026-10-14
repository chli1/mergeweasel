"""The `mergeweasel` command. Everything it needs comes from the Actions environment of Gitea or Forgejo."""
import argparse
import os
import sys

from mergeweasel import accounts, check, queue, release, settings as instance, turn
from mergeweasel.api import ApiError
from mergeweasel.forgejo import detect

MERGER_TOKEN = "MERGEWEASEL_MERGER_TOKEN"
APPROVER_TOKENS = "MERGEWEASEL_APPROVER_TOKENS"
UNKNOWN_MERGER = "merger account unknown, carry-over assumed genuine"
UNKNOWN_APPROVERS = "approver accounts unknown, assuming enough"


class EnvironmentMissing(Exception):
    pass


def required(name):
    value = os.environ.get(name, "")
    if not value:
        raise EnvironmentMissing(f"{name} is not set - mergeweasel runs in Actions, which sets it")
    return value


class TokenProblem(Exception):
    pass


def account_login(client, source):
    """The login behind a token; source names the token in an error, never its value."""
    try:
        login = client.own_login()
    except ApiError as error:
        raise TokenProblem(f"cannot read the account of {source}: {error.message} - check the token, "
                           "e.g. missing scope read:user") from error
    # Without a login the pass could not tell the merger's carry-over or an approver's approval.
    if not login:
        raise TokenProblem(f"the account of {source} has no login")
    return login


def forge_at(server, token):
    """The client class for the forge at server, printed; None after an error, which it prints."""
    try:
        forge = detect(server, token)
    except ApiError as error:
        print(f"cannot detect the forge at {server}: {error.message}", file=sys.stderr)
        return None
    print(f"forge: {forge.__name__}")
    return forge


def approver_clients(forge, server, repository, text):
    """One client per approver token, keyed by its login, in the secret's order."""
    clients = {}
    tokens = [line.strip() for line in text.splitlines() if line.strip()]
    for position, token in enumerate(tokens, start=1):
        source = f"token {position} of {APPROVER_TOKENS}"
        client = forge(server, repository, token)
        login = account_login(client, source)
        if login in clients:
            raise TokenProblem(f"{source} belongs to {login} again - the server counts one approval per account")
        clients[login] = client
    return clients


def instance_settings(gitea):
    """The instance's names, printed; None after an error, which it prints."""
    try:
        found = instance.read(gitea, os.environ)
    except instance.SettingsError as error:
        print(error, file=sys.stderr)
        return None
    except ApiError as error:
        print(f"cannot read the repository: {error.message}", file=sys.stderr)
        return None
    print(found.describe())
    return found


def labels_exist(gitea, settings):
    try:
        missing = instance.missing_labels(gitea, instance.required_labels(settings))
    except ApiError as error:
        print(f"cannot read the labels: {error.message}", file=sys.stderr)
        return False
    if missing is None:
        print("cannot read the organization's labels - not checked whether the labels exist")
        return True
    if missing:
        print(f"labels missing in the repository and its organization: {', '.join(missing)} - "
              "create them, or set the label variables to existing ones", file=sys.stderr)
        return False
    return True


def queue_pass(server, repository, dry_run):
    token = os.environ.get(MERGER_TOKEN, "").strip()
    approver_tokens = os.environ.get(APPROVER_TOKENS, "")
    if not token and not dry_run:
        print(f"{MERGER_TOKEN} is not set", file=sys.stderr)
        return 1
    # CI's dry run has only the run token, which belongs to no account.
    forge = forge_at(server, token or os.environ.get("GITHUB_TOKEN", ""))
    if forge is None:
        return 1
    try:
        if token:
            gitea = forge(server, repository, token)
            merger = accounts.Merger(account_login(gitea, MERGER_TOKEN))
        else:
            gitea = forge(server, repository, os.environ.get("GITHUB_TOKEN", ""))
            merger = accounts.DryRunMerger()
            print(UNKNOWN_MERGER)
        if approver_tokens.strip() or not dry_run:
            approvers = accounts.Approvers(approver_clients(forge, server, repository, approver_tokens))
            print(f"approvers: {', '.join(approvers.logins()) or 'none'}")
        else:
            approvers = accounts.DryRunApprovers()
            print(UNKNOWN_APPROVERS)
    except TokenProblem as problem:
        print(problem, file=sys.stderr)
        return 1
    settings = instance_settings(gitea)
    if settings is None or not labels_exist(gitea, settings):
        return 1
    # Unset outside Actions: a local run has no other pass to wait for.
    own_id = os.environ.get("GITHUB_RUN_ID")
    if own_id and not turn.wait_for_turn(gitea, int(own_id), os.environ.get("GITHUB_EVENT_NAME", "")):
        return 0
    released = release.run(gitea, dry_run, settings.base_branch)
    queue.run_pass(gitea, server, dry_run, merger, approvers, settings)
    return 0 if released else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mergeweasel", description="A merge queue for Gitea.")
    commands = parser.add_subparsers(dest="command", required=True)
    pass_parser = commands.add_parser("pass", help="run one queue pass")
    pass_parser.add_argument("--dry-run", action="store_true", help="decide and report, change nothing")
    check_parser = commands.add_parser("check-commits", help="check what a pull request would land on the base branch")
    check_parser.add_argument("--event", required=True, help="the pull_request event payload ($GITHUB_EVENT_PATH)")
    arguments = parser.parse_args(argv)
    try:
        server = required("GITHUB_SERVER_URL").rstrip("/")
        repository = required("GITHUB_REPOSITORY")
    except EnvironmentMissing as error:
        print(error, file=sys.stderr)
        return 1
    if arguments.command == "pass":
        return queue_pass(server, repository, arguments.dry_run)
    run_token = os.environ.get("GITHUB_TOKEN", "")
    forge = forge_at(server, run_token)
    if forge is None:
        return 1
    gitea = forge(server, repository, run_token)
    settings = instance_settings(gitea)
    if settings is None:
        return 1
    return check.run(gitea, arguments.event, settings)
