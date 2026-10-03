"""The `mergeweasel` command. Everything it needs comes from the Gitea Actions environment."""
import argparse
import os
import sys
import urllib.error

from mergeweasel import check, queue
from mergeweasel.gitea import Gitea, error_message

UNKNOWN_ACCOUNT = "queue account unknown, carry-over assumed genuine"


class EnvironmentMissing(Exception):
    pass


def required(name):
    value = os.environ.get(name, "")
    if not value:
        raise EnvironmentMissing(f"{name} is not set - mergeweasel runs in Gitea Actions, which sets it")
    return value


def queue_pass(server, repository, dry_run):
    token = os.environ.get("MERGEWEASEL_TOKEN", "")
    if token:
        gitea = Gitea(server, repository, token)
        try:
            account = gitea.own_login()
        except (OSError, LookupError, ValueError) as error:
            reason = error_message(error) if isinstance(error, urllib.error.HTTPError) else error
            print(f"cannot read the queue account from MERGEWEASEL_TOKEN: {reason} - check the token, "
                  "e.g. missing scope read:user", file=sys.stderr)
            return 1
        # Without its own login the pass would accept any carry-over as genuine.
        if not account:
            print("the account of MERGEWEASEL_TOKEN has no login", file=sys.stderr)
            return 1
    elif dry_run:
        # CI's dry run has only the run token, which belongs to no account.
        gitea = Gitea(server, repository, os.environ.get("GITHUB_TOKEN", ""))
        account = None
        print(UNKNOWN_ACCOUNT)
    else:
        print("MERGEWEASEL_TOKEN is not set", file=sys.stderr)
        return 1
    # Unset outside Actions: a local run has no other pass to wait for.
    own_id = os.environ.get("GITHUB_RUN_ID")
    if own_id and not queue.wait_for_turn(gitea, int(own_id), os.environ.get("GITHUB_EVENT_NAME", "")):
        return 0
    queue.run_pass(gitea, server, dry_run, account)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mergeweasel", description="A merge queue for Gitea.")
    commands = parser.add_subparsers(dest="command", required=True)
    pass_parser = commands.add_parser("pass", help="run one queue pass")
    pass_parser.add_argument("--dry-run", action="store_true", help="decide and report, change nothing")
    check_parser = commands.add_parser("check-commits", help="check what a pull request would land on main")
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
    return check.run(Gitea(server, repository, os.environ.get("GITHUB_TOKEN", "")), arguments.event)
