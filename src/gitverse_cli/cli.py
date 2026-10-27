"""gv: GitVerse's public REST API from the terminal, `gh`-style. Facts about the API: docs/API_NOTES.md."""

import argparse
import getpass
import json
import pathlib
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TextIO

import httpx
import keyring.errors

from gitverse_cli.api import ApiError, GitVerseClient, RefusedRequestError, path_segment
from gitverse_cli.auth import ENVIRONMENT_VARIABLE, TokenStore, default_token_store
from gitverse_cli.repository import Repository, RepositoryNotFoundError, resolve_repository

EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_NOT_LOGGED_IN = 4
EXIT_INTERRUPTED = 130

# Neither the docs nor the OpenAPI spec list run and job statuses; these are Gitea's,
# whose Actions API GitVerse's mirrors. Anything else counts as finished, so `watch`
# cannot hang on an unknown final status — only on an unknown active one.
ACTIVE_STATUSES = frozenset({"unknown", "waiting", "running", "blocked", "queued", "pending", "in_progress"})
PASSED_STATUSES = frozenset({"success", "skipped"})


class UsageError(Exception):
    pass


class NotLoggedInError(Exception):
    pass


@dataclass
class Context:
    token_store: TokenStore
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    read_secret: Callable[[str], str] = getpass.getpass
    _client: GitVerseClient | None = field(default=None, init=False)

    def client(self) -> GitVerseClient:
        if self._client is None:
            token = self.token_store.load()
            if token is None:
                raise NotLoggedInError
            self._client = GitVerseClient(token.value, self.transport)
        return self._client

    def client_for(self, token: str) -> GitVerseClient:
        return GitVerseClient(token, self.transport)

    def out(self, text: str = "") -> None:
        print(text, file=self.stdout)

    def json(self, payload: object) -> None:
        self.out(json.dumps(payload, ensure_ascii=False, indent=2))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def main() -> None:
    context = Context(default_token_store(), sys.stdin, sys.stdout, sys.stderr)
    sys.exit(run(sys.argv[1:], context))


def run(argv: Sequence[str], context: Context) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace, Context], int] = arguments.handler
    try:
        return handler(arguments, context)
    except ApiError as error:
        print(f"gv: {error}", file=context.stderr)
        return EXIT_FAILURE
    except NotLoggedInError:
        print(f"gv: not logged in; run `gv auth login` (or set {ENVIRONMENT_VARIABLE})", file=context.stderr)
        return EXIT_NOT_LOGGED_IN
    except keyring.errors.KeyringError as error:
        print(f"gv: system keyring: {type(error).__name__}: {error}", file=context.stderr)
        return EXIT_FAILURE
    except RefusedRequestError as error:
        print(f"gv: {error}", file=context.stderr)
        return EXIT_FAILURE
    except (UsageError, RepositoryNotFoundError) as error:
        print(f"gv: {error}", file=context.stderr)
        return EXIT_USAGE
    except httpx.HTTPError as error:
        # The request line only: an httpx error never carries the headers, the token among them.
        print(f"gv: {type(error).__name__}: {error}", file=context.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    finally:
        context.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gv", description="GitVerse from the command line (public REST API).")
    commands = parser.add_subparsers(required=True, metavar="COMMAND")

    repository_option = argparse.ArgumentParser(add_help=False)
    repository_option.add_argument(
        "-R", "--repo", metavar="OWNER/REPO", help="repository (default: the gitverse.ru remote of this directory)"
    )
    json_option = argparse.ArgumentParser(add_help=False)
    json_option.add_argument("--json", action="store_true", help="print the API's JSON instead of text")

    auth = commands.add_parser("auth", help="log in, check, log out").add_subparsers(required=True, metavar="ACTION")
    auth.add_parser("login", help="store a token (hidden prompt, or stdin when it is not a terminal)").set_defaults(
        handler=_auth_login
    )
    auth.add_parser("status", help="who the stored token belongs to").set_defaults(handler=_auth_status)
    auth.add_parser("logout", help="remove the stored token").set_defaults(handler=_auth_logout)

    repositories = commands.add_parser("repo", help="repositories").add_subparsers(required=True, metavar="ACTION")
    repo_create = repositories.add_parser("create", parents=[json_option], help="create an empty repository")
    repo_create.add_argument("name", metavar="[ORG/]NAME", help="ORG/NAME — in that organization; NAME — your own")
    repo_create.add_argument("--public", action="store_true", help="visible to everyone (default: private)")
    repo_create.add_argument("-d", "--description")
    repo_create.set_defaults(handler=_repo_create)
    repo_view = repositories.add_parser("view", parents=[json_option], help="a repository's details")
    repo_view.add_argument(
        "repository", nargs="?", metavar="OWNER/REPO", help="default: the gitverse.ru remote of this directory"
    )
    repo_view.set_defaults(handler=_repo_view)

    runs = commands.add_parser("run", help="workflow runs").add_subparsers(required=True, metavar="ACTION")
    run_list = runs.add_parser("list", parents=[repository_option, json_option], help="recent runs")
    run_list.add_argument("-w", "--workflow", help="only this workflow's runs (file name, e.g. ci.yml)")
    run_list.add_argument("-b", "--branch")
    run_list.add_argument("-s", "--status")
    run_list.add_argument("-L", "--limit", type=_positive_int, default=20)
    run_list.set_defaults(handler=_run_list)

    run_view = runs.add_parser("view", parents=[repository_option, json_option], help="a run and its jobs")
    run_view.add_argument("run", type=_run_number, metavar="RUN", help="run number (#N in `gv run list`)")
    logs = run_view.add_mutually_exclusive_group()
    logs.add_argument("--log", action="store_true", help="print every job's log")
    logs.add_argument("--log-failed", action="store_true", help="print the logs of the jobs that did not pass")
    run_view.add_argument("--job", type=int, metavar="JOB_ID", help="only this job (with --log/--log-failed)")
    run_view.add_argument("--tail", type=_positive_int, metavar="N", help="only the last N lines of each log")
    run_view.set_defaults(handler=_run_view)

    run_watch = runs.add_parser(
        "watch", parents=[repository_option], help="wait for a run to finish; exit 1 unless it succeeded or was skipped"
    )
    run_watch.add_argument("run", type=_run_number, metavar="RUN")
    run_watch.add_argument(
        "-i", "--interval", type=_positive_float, default=10.0, help="seconds between polls (default 10)"
    )
    run_watch.set_defaults(handler=_run_watch)

    run_rerun = runs.add_parser("rerun", parents=[repository_option], help="run a finished run (or one job) again")
    run_rerun.add_argument("run", type=_run_number, metavar="RUN")
    run_rerun.add_argument("--job", type=int, metavar="JOB_ID", help="rerun only this job")
    run_rerun.set_defaults(handler=_run_rerun)

    run_cancel = runs.add_parser("cancel", parents=[repository_option], help="cancel an active run")
    run_cancel.add_argument("run", type=_run_number, metavar="RUN")
    run_cancel.set_defaults(handler=_run_cancel)

    workflows = commands.add_parser("workflow", help="workflows").add_subparsers(required=True, metavar="ACTION")
    workflows.add_parser(
        "list", parents=[repository_option, json_option], help="the repository's workflows"
    ).set_defaults(handler=_workflow_list)
    workflow_run = workflows.add_parser("run", parents=[repository_option], help="start a workflow_dispatch workflow")
    workflow_run.add_argument("workflow", help="workflow file name, e.g. deploy.yml")
    workflow_run.add_argument(
        "-f", "--field", action="append", default=[], metavar="KEY=VALUE", help="an input of the workflow"
    )
    workflow_run.add_argument("--ref", help="branch to run on (default: the repository's default branch)")
    workflow_run.set_defaults(handler=_workflow_run)

    packages = commands.add_parser("package", help="packages of the registry").add_subparsers(
        required=True, metavar="ACTION"
    )
    package_owner = argparse.ArgumentParser(add_help=False)
    package_owner.add_argument("type", help="package type: container, generic, pypi, npm, …")
    package_owner.add_argument("name", help="package name, e.g. myproject/backend")
    package_owner.add_argument("--owner", help="package owner (default: the repository's owner)")
    package_owner.add_argument("--user", action="store_true", help="the owner is a user, not an organization")
    package_owner.add_argument("-R", "--repo", metavar="OWNER/REPO", help="repository whose owner owns the package")
    package_versions = packages.add_parser(
        "versions", parents=[package_owner, json_option], help="a package's versions"
    )
    package_versions.add_argument("-L", "--limit", type=_positive_int, default=30)
    package_versions.set_defaults(handler=_package_versions)
    package_delete = packages.add_parser("delete", parents=[package_owner], help="delete a package or one version")
    package_delete.add_argument("--version", metavar="VERSION_ID", help="only this version (id from `versions`)")
    package_delete.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    package_delete.set_defaults(handler=_package_delete)

    api = commands.add_parser("api", parents=[repository_option], help="a raw request, like `gh api`")
    api.add_argument("path", help="e.g. repos/{owner}/{repo}/actions/runs ({owner}, {repo} come from -R or git)")
    api.add_argument("-X", "--method", default="GET")
    api.add_argument(
        "-f", "--field", action="append", default=[], metavar="KEY=VALUE", help="query (GET) or JSON body field"
    )
    api.set_defaults(handler=_api)
    return parser


def _auth_login(_arguments: argparse.Namespace, context: Context) -> int:
    if context.stdin.isatty():
        token = context.read_secret("GitVerse token (https://gitverse.ru/settings/tokens, input hidden): ")
    else:
        token = context.stdin.read()
    token = token.strip()
    if not token:
        raise UsageError("empty token")
    with context.client_for(token) as client:
        user = client.get_json("/user")
    where = context.token_store.save(token)
    context.out(f"logged in to gitverse.ru as {user.get('login')}; token stored in {where}")
    if context.token_store.load_from_environment() is not None:
        context.out(f"note: {ENVIRONMENT_VARIABLE} is set and takes precedence over the stored token")
    return 0


def _auth_status(_arguments: argparse.Namespace, context: Context) -> int:
    token = context.token_store.load()
    if token is None:
        raise NotLoggedInError
    response = context.client().request("GET", "/user")
    remaining = response.headers.get("GitVerse-RateLimit-User-Remaining")
    context.out(f"logged in to gitverse.ru as {response.json().get('login')} (token from {token.source})")
    if remaining is not None:
        context.out(f"API requests left this hour: {remaining}")
    return 0


def _auth_logout(_arguments: argparse.Namespace, context: Context) -> int:
    removed = context.token_store.delete()
    context.out(f"token removed from {', '.join(removed)}" if removed else "no stored token")
    if context.token_store.load_from_environment() is not None:
        context.out(f"note: {ENVIRONMENT_VARIABLE} is still set")
    return 0


def _repo_create(arguments: argparse.Namespace, context: Context) -> int:
    owner, separator, name = str(arguments.name).rpartition("/")
    if not name or (separator and (not owner or "/" in owner)):
        raise UsageError(f"expected NAME or ORG/NAME, got {arguments.name!r}")
    # `gv api -f` sends strings only, and the API wants `private` a JSON boolean.
    body: dict[str, object] = {"name": name, "private": not arguments.public}
    if arguments.description:
        body["description"] = arguments.description
    path = f"/orgs/{path_segment(owner)}/repos" if owner else "/user/repos"
    repository = context.client().request("POST", path, body=body).json()
    if arguments.json:
        context.json(repository)
        return 0
    context.out(f"created {_repository_summary(repository)}")
    context.out(f"push: git remote add origin {repository.get('ssh_url') or repository.get('clone_url')}")
    return 0


def _repo_view(arguments: argparse.Namespace, context: Context) -> int:
    repository = context.client().get_json(_repository_path(resolve_repository(arguments.repository)))
    if arguments.json:
        context.json(repository)
        return 0
    context.out(_repository_summary(repository))
    context.out(f"  default branch {repository.get('default_branch')}  pushed {repository.get('pushed_at')}")
    context.out(f"  {repository.get('clone_url')}  {repository.get('ssh_url')}")
    return 0


def _run_list(arguments: argparse.Namespace, context: Context) -> int:
    repository = resolve_repository(arguments.repo)
    path = f"{_repository_path(repository)}/actions/runs"
    if arguments.workflow:
        path = (
            f"{_repository_path(repository)}/actions/workflows/{path_segment(_workflow_file(arguments.workflow))}/runs"
        )
    filters = {"branch": arguments.branch, "status": arguments.status}
    params: dict[str, str | int] = {key: value for key, value in filters.items() if value}
    runs = context.client().get_list(path, "workflow_runs", arguments.limit, params)
    if arguments.json:
        context.json(runs)
        return 0
    for workflow_run in runs:
        context.out(_run_line(workflow_run))
    if not runs:
        context.out("no runs")
    return 0


def _run_view(arguments: argparse.Namespace, context: Context) -> int:
    repository = resolve_repository(arguments.repo)
    client = context.client()
    run_path = f"{_repository_path(repository)}/actions/runs/index/{arguments.run}"
    workflow_run = client.get_json(run_path)
    jobs = client.get_list(f"{run_path}/jobs", "jobs", None)
    if arguments.job is not None:
        jobs = [job for job in jobs if job.get("id") == arguments.job]
        if not jobs:
            raise UsageError(f"run #{arguments.run} has no job {arguments.job}")
    if arguments.log or arguments.log_failed:
        shown = [job for job in jobs if arguments.log or not _passed_or_active(job.get("status"))]
        if arguments.json:
            context.json([{"job": job, "log": _job_log(client, repository, job, arguments.tail)} for job in shown])
            return 0
        for job in shown:
            context.out(f"=== job {job.get('id')} {job.get('name')} ({job.get('status')})")
            context.out(_job_log(client, repository, job, arguments.tail))
        if not shown:
            context.out("no failed jobs")
        return 0
    if arguments.json:
        context.json({"run": workflow_run, "jobs": jobs})
        return 0
    _print_run(context, workflow_run, jobs)
    if any(not _passed_or_active(job.get("status")) for job in jobs):
        context.out(f"logs of the failed jobs: gv run view {arguments.run} --log-failed")
    return 0


def _run_watch(arguments: argparse.Namespace, context: Context) -> int:
    repository = resolve_repository(arguments.repo)
    client = context.client()
    run_path = f"{_repository_path(repository)}/actions/runs/index/{arguments.run}"
    last_status = None
    while True:
        workflow_run = client.get_json(run_path)
        status = workflow_run.get("status")
        if status != last_status:
            context.out(f"#{arguments.run} {status}")
            last_status = status
        if status not in ACTIVE_STATUSES:
            break
        context.sleep(arguments.interval)
    _print_run(context, workflow_run, client.get_list(f"{run_path}/jobs", "jobs", None))
    return 0 if status in PASSED_STATUSES else EXIT_FAILURE


def _run_rerun(arguments: argparse.Namespace, context: Context) -> int:
    repository = resolve_repository(arguments.repo)
    client = context.client()
    if arguments.job is not None:
        client.request("POST", f"{_repository_path(repository)}/actions/jobs/{arguments.job}/rerun")
        context.out(f"job {arguments.job} of run #{arguments.run} queued again")
        return 0
    run_id = _run_id(client, repository, arguments.run)
    client.request("POST", f"{_repository_path(repository)}/actions/runs/{run_id}/rerun")
    context.out(f"run #{arguments.run} queued again; follow: gv run watch {arguments.run}")
    return 0


def _run_cancel(arguments: argparse.Namespace, context: Context) -> int:
    repository = resolve_repository(arguments.repo)
    client = context.client()
    run_id = _run_id(client, repository, arguments.run)
    client.request("POST", f"{_repository_path(repository)}/actions/runs/{run_id}/cancel")
    context.out(f"run #{arguments.run} cancelled")
    return 0


def _workflow_list(arguments: argparse.Namespace, context: Context) -> int:
    repository = resolve_repository(arguments.repo)
    workflows = context.client().get_list(f"{_repository_path(repository)}/actions/workflows", "workflows", None)
    if arguments.json:
        context.json(workflows)
        return 0
    for workflow in workflows:
        context.out(f"{workflow.get('name')}\t{workflow.get('path')}\t{workflow.get('state')}")
    if not workflows:
        context.out("no workflows")
    return 0


def _workflow_run(arguments: argparse.Namespace, context: Context) -> int:
    repository = resolve_repository(arguments.repo)
    workflow = _workflow_file(arguments.workflow)
    inputs = _fields(arguments.field)
    params: dict[str, str | int] = {"branch": arguments.ref} if arguments.ref else {}
    # The body is the inputs map itself (`v1.workflowDispatchInputs`), not GitHub's {"ref", "inputs"}.
    context.client().request(
        "POST",
        f"{_repository_path(repository)}/actions/workflows/{path_segment(workflow)}/dispatches",
        params,
        inputs,
    )
    context.out(f"{workflow} dispatched on {arguments.ref or 'the default branch'}")
    context.out(f"follow: gv run list --workflow {workflow} --limit 1, then gv run watch <N>")
    return 0


def _package_versions(arguments: argparse.Namespace, context: Context) -> int:
    versions = context.client().get_list(f"{_package_path(arguments)}/versions", None, arguments.limit)
    if arguments.json:
        context.json(versions)
        return 0
    for version in versions:
        context.out(f"{version.get('id')}\t{version.get('name')}\t{version.get('created_at')}")
    if not versions:
        context.out("no versions")
    return 0


def _package_delete(arguments: argparse.Namespace, context: Context) -> int:
    package_path = _package_path(arguments)
    owner = _package_owner(arguments)
    target = f"{arguments.type} package {owner}/{arguments.name}"
    if arguments.version is not None:
        target = f"version {arguments.version} of {target}"
    if not arguments.yes:
        if not context.stdin.isatty():
            raise UsageError(f"deleting {target} needs --yes when stdin is not a terminal")
        print(f"Delete {target}? This cannot be undone. [y/N] ", end="", file=context.stderr, flush=True)
        if context.stdin.readline().strip().lower() not in {"y", "yes"}:
            context.out("not deleted")
            return EXIT_FAILURE
    path = package_path if arguments.version is None else f"{package_path}/versions/{path_segment(arguments.version)}"
    context.client().request("DELETE", path)
    context.out(f"deleted {target}")
    return 0


def _api(arguments: argparse.Namespace, context: Context) -> int:
    path: str = arguments.path
    if "{owner}" in path or "{repo}" in path:
        repository = resolve_repository(arguments.repo)
        path = path.replace("{owner}", repository.owner).replace("{repo}", repository.name)
    method = arguments.method.upper()
    fields = _fields(arguments.field)
    if method == "GET":
        response = context.client().request(method, path, params=fields)
    else:
        response = context.client().request(method, path, body=fields if fields else None)
    if not response.content:
        return 0
    try:
        context.json(response.json())
    except ValueError:
        context.out(response.text)
    return 0


def _repository_path(repository: Repository) -> str:
    return f"/repos/{path_segment(repository.owner)}/{path_segment(repository.name)}"


def _repository_summary(repository: dict[str, Any]) -> str:
    visibility = "private" if repository.get("private") else "public"
    return f"{repository.get('full_name')} ({visibility})  {repository.get('html_url')}"


def _package_owner(arguments: argparse.Namespace) -> str:
    return arguments.owner or resolve_repository(arguments.repo).owner


def _package_path(arguments: argparse.Namespace) -> str:
    owner_kind = "users" if arguments.user else "orgs"
    return (
        f"/{owner_kind}/{path_segment(_package_owner(arguments))}"
        f"/packages/{path_segment(arguments.type)}/{path_segment(arguments.name)}"
    )


def _run_id(client: GitVerseClient, repository: Repository, run_number: int) -> int:
    """Rerun and cancel take the run's id; people and `run list` use its number."""
    workflow_run = client.get_json(f"{_repository_path(repository)}/actions/runs/index/{run_number}")
    return int(workflow_run["id"])


def _job_log(client: GitVerseClient, repository: Repository, job: dict[str, Any], tail: int | None) -> str:
    response = client.request("GET", f"{_repository_path(repository)}/actions/jobs/{job.get('id')}/logs")
    text = response.text
    # The spec gives the answer no schema; a JSON-encoded string is unwrapped, anything else printed as is.
    if "json" in response.headers.get("Content-Type", ""):
        try:
            decoded = response.json()
        except ValueError:
            decoded = None
        if isinstance(decoded, str):
            text = decoded
    lines = text.rstrip("\n").splitlines()
    return "\n".join(lines if tail is None else lines[-tail:])


def _print_run(context: Context, workflow_run: dict[str, Any], jobs: list[dict[str, Any]]) -> None:
    context.out(_run_line(workflow_run))
    sha = str(workflow_run.get("commit_sha") or "")[:10]
    context.out(f"  commit {sha}  started {workflow_run.get('started')}  stopped {workflow_run.get('stopped')}")
    for job in jobs:
        line = f"  job {job.get('id')}\t{job.get('status')}\t{job.get('name')}"
        failed_step = job.get("failed_step")
        if isinstance(failed_step, dict) and failed_step.get("name"):
            line += f"\tfailed step: {failed_step['name']}"
        context.out(line)


def _run_line(workflow_run: dict[str, Any]) -> str:
    ref = str(workflow_run.get("ref") or "").removeprefix("refs/heads/")
    return (
        f"#{workflow_run.get('index')}\t{workflow_run.get('status')}\t{workflow_run.get('name')}"
        f"\t{workflow_run.get('event')} {ref}\t{workflow_run.get('title')}\t{workflow_run.get('started')}"
    )


def _passed_or_active(status: object) -> bool:
    return status in PASSED_STATUSES or status in ACTIVE_STATUSES


def _workflow_file(workflow: str) -> str:
    """The API names a workflow by its file name: `.gitverse/workflows/deploy.yml` → `deploy.yml`."""
    return pathlib.PurePosixPath(workflow).name


def _fields(pairs: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator or not key:
            raise UsageError(f"expected KEY=VALUE, got {pair!r}")
        fields[key] = value
    return fields


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be ≥ 1")
    return value


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return value


def _run_number(text: str) -> int:
    return _positive_int(text.removeprefix("#"))
