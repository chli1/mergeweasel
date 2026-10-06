"""The `mergeweasel` command. Everything it needs comes from the Gitea Actions environment."""
import argparse
import os
import sys
import urllib.error

from mergeweasel import check, queue, release, settings as instance
from mergeweasel.gitea import Gitea, error_message

MERGER_TOKEN = "MERGEWEASEL_MERGER_TOKEN"
APPROVER_TOKENS = "MERGEWEASEL_APPROVER_TOKENS"
UNKNOWN_MERGER = "merger account unknown, carry-over assumed genuine"
UNKNOWN_APPROVERS = "approver accounts unknown, assuming enough"


class EnvironmentMissing(Exception):
    pass


def required(name):
    value = os.environ.get(name, "")
    if not value:
        raise EnvironmentMissing(f"{name} is not set - mergeweasel runs in Gitea Actions, which sets it")
    return value


class TokenProblem(Exception):
    pass


def account_login(client, source):
    """The login behind a token; source names the token in an error, never its value."""
    try:
        login = client.own_login()
    except (OSError, LookupError, ValueError) as error:
        if isinstance(error, urllib.error.HTTPError):
            reason = error_message(error)
        elif isinstance(error, ValueError):
            # http.client quotes an invalid header, the token included.
            reason = "the token or Gitea's answer is malformed"
        else:
            reason = error
        raise TokenProblem(f"cannot read the account of {source}: {reason} - check the token, "
                           "e.g. missing scope read:user") from error
    # Without a login the pass could not tell the merger's carry-over or an approver's approval.
    if not login:
        raise TokenProblem(f"the account of {source} has no login")
    return login


def approver_clients(server, repository, text):
    """One client per approver token, keyed by its login, in the secret's order."""
    clients = {}
    tokens = [line.strip() for line in text.splitlines() if line.strip()]
    for position, token in enumerate(tokens, start=1):
        source = f"token {position} of {APPROVER_TOKENS}"
        client = Gitea(server, repository, token)
        login = account_login(client, source)
        if login in clients:
            raise TokenProblem(f"{source} belongs to {login} again - Gitea counts one approval per account")
        clients[login] = client
    return clients


def api_reason(error):
    return error_message(error) if isinstance(error, urllib.error.HTTPError) else error


def instance_settings(gitea):
    """The instance's names, printed; None after an error, which it prints."""
    try:
        found = instance.read(gitea, os.environ)
    except instance.SettingsError as error:
        print(error, file=sys.stderr)
        return None
    except (OSError, LookupError) as error:
        print(f"cannot read the repository: {api_reason(error)}", file=sys.stderr)
        return None
    print(found.describe())
    return found


def labels_exist(gitea, settings):
    try:
        missing = instance.missing_labels(gitea, instance.required_labels(settings, os.environ))
    except (OSError, LookupError) as error:
        print(f"cannot read the labels: {api_reason(error)}", file=sys.stderr)
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
    try:
        if token:
            gitea = Gitea(server, repository, token)
            merger = account_login(gitea, MERGER_TOKEN)
        elif dry_run:
            # CI's dry run has only the run token, which belongs to no account.
            gitea = Gitea(server, repository, os.environ.get("GITHUB_TOKEN", ""))
            merger = None
            print(UNKNOWN_MERGER)
        else:
            print(f"{MERGER_TOKEN} is not set", file=sys.stderr)
            return 1
        if approver_tokens.strip() or not dry_run:
            approvers = approver_clients(server, repository, approver_tokens)
            print(f"approvers: {', '.join(approvers) or 'none'}")
        else:
            approvers = None
            print(UNKNOWN_APPROVERS)
    except TokenProblem as problem:
        print(problem, file=sys.stderr)
        return 1
    settings = instance_settings(gitea)
    if settings is None or not labels_exist(gitea, settings):
        return 1
    # Unset outside Actions: a local run has no other pass to wait for.
    own_id = os.environ.get("GITHUB_RUN_ID")
    if own_id and not queue.wait_for_turn(gitea, int(own_id), os.environ.get("GITHUB_EVENT_NAME", "")):
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
    gitea = Gitea(server, repository, os.environ.get("GITHUB_TOKEN", ""))
    settings = instance_settings(gitea)
    if settings is None:
        return 1
    return check.run(gitea, arguments.event, settings)
