import io
import json

import httpx
import pytest
from conftest import REPOSITORY, TOKEN, Cli, TerminalInput

RUN = {
    "id": 9001,
    "index": 12,
    "name": "CI",
    "title": "Fix the build",
    "status": "failure",
    "event": "push",
    "ref": "refs/heads/main",
    "commit_sha": "1a2b3c4d5e6f7a8b",
    "started": "2026-10-14T10:00:00Z",
    "stopped": "2026-10-14T10:05:00Z",
}
JOBS = {
    "total_count": 2,
    "jobs": [
        {"id": 501, "name": "check", "status": "failure", "failed_step": {"name": "deploy/ci-check.sh"}},
        {"id": 502, "name": "release", "status": "skipped"},
    ],
}


def test_run_list_prints_one_line_per_run_from_the_repository_of_option(cli: Cli) -> None:
    cli.api.add("GET", f"{REPOSITORY}/actions/runs", {"total_count": 1, "workflow_runs": [RUN]})
    result = cli("run", "list", "-R", "acme/shop", "--limit", "5", "--branch", "main")
    assert result.code == 0
    assert result.stdout == "#12\tfailure\tCI\tpush main\tFix the build\t2026-10-14T10:00:00Z\n"
    assert dict(cli.api.requests[0].url.params) == {"branch": "main", "per_page": "5"}


def test_run_list_of_a_workflow_and_as_json(cli: Cli) -> None:
    cli.api.add("GET", f"{REPOSITORY}/actions/workflows/deploy.yml/runs", {"workflow_runs": [RUN]})
    result = cli("run", "list", "-R", "acme/shop", "-w", ".gitverse/workflows/deploy.yml", "--json")
    assert json.loads(result.stdout) == [RUN]


def test_run_view_shows_jobs_and_points_at_the_failed_logs(cli: Cli) -> None:
    cli.api.add("GET", f"{REPOSITORY}/actions/runs/index/12", RUN)
    cli.api.add("GET", f"{REPOSITORY}/actions/runs/index/12/jobs", JOBS)
    result = cli("run", "view", "#12", "-R", "acme/shop")
    assert result.stdout.splitlines() == [
        "#12\tfailure\tCI\tpush main\tFix the build\t2026-10-14T10:00:00Z",
        "  commit 1a2b3c4d5e  started 2026-10-14T10:00:00Z  stopped 2026-10-14T10:05:00Z",
        "  job 501\tfailure\tcheck\tfailed step: deploy/ci-check.sh",
        "  job 502\tskipped\trelease",
        "logs of the failed jobs: gv run view 12 --log-failed",
    ]


def test_log_failed_fetches_only_the_failed_jobs_and_tails_them(cli: Cli) -> None:
    cli.api.add("GET", f"{REPOSITORY}/actions/runs/index/12", RUN)
    cli.api.add("GET", f"{REPOSITORY}/actions/runs/index/12/jobs", JOBS)
    log = "line 1\nline 2\nline 3\n"
    cli.api.add_route(
        "GET",
        f"{REPOSITORY}/actions/jobs/501/logs",
        lambda _request: httpx.Response(200, text=log, headers={"Content-Type": "text/plain"}),
    )
    result = cli("run", "view", "12", "-R", "acme/shop", "--log-failed", "--tail", "2")
    assert result.stdout == "=== job 501 check (failure)\nline 2\nline 3\n"
    assert not any(request.url.path.endswith("/jobs/502/logs") for request in cli.api.requests)


def test_watch_polls_until_the_run_finishes_and_fails_with_it(cli: Cli) -> None:
    statuses = iter(["waiting", "running", "running", "failure"])
    cli.api.add_route(
        "GET",
        f"{REPOSITORY}/actions/runs/index/12",
        lambda _request: httpx.Response(200, json={**RUN, "status": next(statuses)}),
    )
    cli.api.add("GET", f"{REPOSITORY}/actions/runs/index/12/jobs", JOBS)
    result = cli("run", "watch", "12", "-R", "acme/shop", "--interval", "3")
    assert result.code == 1
    assert result.stdout.splitlines()[:3] == ["#12 waiting", "#12 running", "#12 failure"]
    assert cli.sleeps == [3.0, 3.0, 3.0]


def test_watch_exits_zero_on_success(cli: Cli) -> None:
    cli.api.add("GET", f"{REPOSITORY}/actions/runs/index/12", {**RUN, "status": "success"})
    cli.api.add("GET", f"{REPOSITORY}/actions/runs/index/12/jobs", {"jobs": []})
    assert cli("run", "watch", "12", "-R", "acme/shop").code == 0


def test_rerun_and_cancel_address_the_run_by_its_id(cli: Cli) -> None:
    cli.api.add("GET", f"{REPOSITORY}/actions/runs/index/12", RUN)
    cli.api.add("POST", f"{REPOSITORY}/actions/runs/9001/rerun", {}, 201)
    cli.api.add("POST", f"{REPOSITORY}/actions/runs/9001/cancel", {}, 201)
    cli.api.add("POST", f"{REPOSITORY}/actions/jobs/501/rerun", {}, 201)
    assert cli("run", "rerun", "12", "-R", "acme/shop").code == 0
    assert cli("run", "cancel", "12", "-R", "acme/shop").code == 0
    assert cli("run", "rerun", "12", "--job", "501", "-R", "acme/shop").code == 0
    posts = [request.url.path for request in cli.api.requests if request.method == "POST"]
    assert posts == [
        f"{REPOSITORY}/actions/runs/9001/rerun",
        f"{REPOSITORY}/actions/runs/9001/cancel",
        f"{REPOSITORY}/actions/jobs/501/rerun",
    ]


def test_workflow_run_posts_the_inputs_map_on_the_branch(cli: Cli) -> None:
    cli.api.add_route(
        "POST", f"{REPOSITORY}/actions/workflows/deploy.yml/dispatches", lambda _request: httpx.Response(204)
    )
    result = cli("workflow", "run", "deploy.yml", "-R", "acme/shop", "-f", "ref=feature-branch", "--ref", "main")
    assert result.code == 0
    request = cli.api.requests[0]
    assert json.loads(request.content) == {"ref": "feature-branch"}
    assert dict(request.url.params) == {"branch": "main"}


def test_workflow_list(cli: Cli) -> None:
    workflows = [{"name": "Deploy", "path": ".gitverse/workflows/deploy.yml", "state": "active"}]
    cli.api.add("GET", f"{REPOSITORY}/actions/workflows", {"total_count": 1, "workflows": workflows})
    assert cli("workflow", "list", "-R", "acme/shop").stdout == "Deploy\t.gitverse/workflows/deploy.yml\tactive\n"


def test_package_versions_of_the_repository_owner_organisation(cli: Cli) -> None:
    versions = [{"id": 7, "name": "1a2b3c4", "created_at": "2026-10-13"}]
    cli.api.add("GET", "/orgs/acme/packages/container/shop%2Fweb/versions", versions)
    result = cli("package", "versions", "container", "shop/web", "-R", "acme/shop")
    assert result.stdout == "7\t1a2b3c4\t2026-10-13\n"
    assert cli.api.requests[0].url.raw_path.startswith(b"/orgs/acme/packages/container/shop%2Fweb/versions")


def test_package_delete_refuses_without_yes_when_nobody_can_answer(cli: Cli) -> None:
    result = cli("package", "delete", "container", "web", "--owner", "acme", "--version", "7")
    assert result.code == 2
    assert "needs --yes" in result.stderr
    assert cli.api.requests == []


def test_package_delete_asks_on_a_terminal_and_keeps_the_package_on_no(cli: Cli) -> None:
    result = cli("package", "delete", "container", "web", "--owner", "acme", stdin=TerminalInput("n\n"))
    assert result.code == 1
    assert "Delete container package acme/web?" in result.stderr
    assert cli.api.requests == []


def test_package_delete_with_yes(cli: Cli) -> None:
    cli.api.add_route("DELETE", "/users/me/packages/generic/tool/versions/7", lambda _request: httpx.Response(204))
    result = cli("package", "delete", "generic", "tool", "--owner", "me", "--user", "--version", "7", "--yes")
    assert result.code == 0
    assert result.stdout == "deleted version 7 of generic package me/tool\n"


def test_api_fills_placeholders_and_sends_get_fields_as_query(cli: Cli) -> None:
    cli.api.add("GET", f"{REPOSITORY}/actions/runs", {"total_count": 0})
    result = cli("api", "repos/{owner}/{repo}/actions/runs", "-R", "acme/shop", "-f", "status=failure")
    assert json.loads(result.stdout) == {"total_count": 0}
    assert dict(cli.api.requests[0].url.params) == {"status": "failure"}


def test_an_api_error_is_one_line_on_stderr_without_the_token(cli: Cli) -> None:
    result = cli("api", "/repos/nobody/nothing")
    assert result.code == 1
    assert result.stderr == "gv: HTTP 404 NOT_FOUND no such route (GET /repos/nobody/nothing)\n"
    assert TOKEN not in result.stdout + result.stderr


def test_login_checks_the_token_before_storing_it(cli: Cli) -> None:
    cli.store.delete()
    cli.api.add("GET", "/user", {"login": "octocat"})
    result = cli("auth", "login", stdin=io.StringIO("new-token\n"))
    assert result.stdout == "logged in to gitverse.ru as octocat; token stored in system keyring\n"
    token = cli.store.load()
    assert token is not None
    assert token.value == "new-token"
    assert cli.api.requests[0].headers["Authorization"] == "Bearer new-token"


def test_a_rejected_token_is_not_stored_and_not_printed(cli: Cli) -> None:
    cli.store.delete()
    cli.api.add("GET", "/user", {"error": {"code": "UNAUTHORIZED", "message": "Unauthorized", "status": 401}}, 401)
    result = cli("auth", "login", stdin=io.StringIO("bad-token-value\n"))
    assert result.code == 1
    assert cli.store.load() is None
    assert "bad-token-value" not in result.stdout + result.stderr


def test_login_on_a_terminal_reads_the_token_hidden(cli: Cli) -> None:
    cli.api.add("GET", "/user", {"login": "octocat"})
    prompts: list[str] = []

    def read_secret(prompt: str) -> str:
        prompts.append(prompt)
        return "typed-token"

    cli.read_secret = read_secret
    result = cli("auth", "login", stdin=TerminalInput())
    assert result.code == 0
    assert len(prompts) == 1


def test_status_names_the_user_and_where_the_token_is(cli: Cli) -> None:
    cli.api.add_route(
        "GET",
        "/user",
        lambda _request: httpx.Response(
            200, json={"login": "octocat"}, headers={"GitVerse-RateLimit-User-Remaining": "1999"}
        ),
    )
    result = cli("auth", "status")
    assert (
        result.stdout
        == "logged in to gitverse.ru as octocat (token from system keyring)\nAPI requests left this hour: 1999\n"
    )


def test_without_a_token_commands_exit_4(cli: Cli) -> None:
    cli("auth", "logout")
    result = cli("run", "list", "-R", "acme/shop")
    assert result.code == 4
    assert "gv auth login" in result.stderr


def test_watch_rejects_a_zero_interval(cli: Cli) -> None:
    with pytest.raises(SystemExit):
        cli("run", "watch", "12", "-R", "acme/shop", "--interval", "0")


CREATED = {
    "full_name": "acme/shop",
    "private": True,
    "html_url": "https://gitverse.ru/acme/shop",
    "clone_url": "https://gitverse.ru/acme/shop.git",
    "ssh_url": "ssh://git@ssh.gitverse.ru:2222/acme/shop.git",
    "default_branch": "master",
    "pushed_at": None,
}


def test_repo_create_in_an_organization_is_private_by_default(cli: Cli) -> None:
    cli.api.add("POST", "/orgs/acme/repos", CREATED, status=201)
    result = cli("repo", "create", "acme/shop", "-d", "The shop")
    assert result.code == 0
    assert json.loads(cli.api.requests[0].content) == {"name": "shop", "private": True, "description": "The shop"}
    assert result.stdout.splitlines() == [
        "created acme/shop (private)  https://gitverse.ru/acme/shop",
        "push: git remote add origin ssh://git@ssh.gitverse.ru:2222/acme/shop.git",
    ]


def test_repo_create_without_an_owner_is_the_users_own_and_public_on_request(cli: Cli) -> None:
    cli.api.add("POST", "/user/repos", {**CREATED, "private": False}, status=201)
    result = cli("repo", "create", "shop", "--public")
    assert result.code == 0
    assert json.loads(cli.api.requests[0].content) == {"name": "shop", "private": False}


@pytest.mark.parametrize("name", ["/shop", "acme/", "a/b/c"])
def test_repo_create_refuses_a_malformed_name(cli: Cli, name: str) -> None:
    result = cli("repo", "create", name)
    assert result.code == 2
    assert not cli.api.requests


def test_repo_view(cli: Cli) -> None:
    cli.api.add("GET", REPOSITORY, CREATED)
    result = cli("repo", "view", "acme/shop")
    assert result.stdout.splitlines() == [
        "acme/shop (private)  https://gitverse.ru/acme/shop",
        "  default branch master  pushed None",
        "  https://gitverse.ru/acme/shop.git  ssh://git@ssh.gitverse.ru:2222/acme/shop.git",
    ]
