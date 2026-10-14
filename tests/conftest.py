"""A fake GitVerse API on httpx.MockTransport and a fake keyring. Answer shapes follow the
OpenAPI spec `v1/openapi-1.11.json` (docs/API_NOTES.md) — none was recorded from the live API."""

import io
import json
import pathlib
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import keyring.errors
import pytest

from gitverse_cli.auth import TokenStore
from gitverse_cli.cli import Context, run

TOKEN = "secret-token-value-1234"
API = "https://api.gitverse.ru"
REPOSITORY = "/repos/acme/shop"

Route = Callable[[httpx.Request], httpx.Response]


class FakeKeyring:
    def __init__(self, available: bool = True, failure: Exception | None = None) -> None:
        self.available = available
        self.failure = failure
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        self._require()
        return self.passwords.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self._require()
        self.passwords[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self._require()
        if self.passwords.pop((service_name, username), None) is None:
            raise keyring.errors.PasswordDeleteError("not found")

    def _require(self) -> None:
        if self.failure is not None:
            raise self.failure
        if not self.available:
            raise keyring.errors.NoKeyringError("no backend")


class TerminalInput(io.StringIO):
    def isatty(self) -> bool:
        return True


@dataclass
class FakeApi:
    routes: dict[tuple[str, str], Route] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def add(self, method: str, path: str, payload: object, status: int = 200) -> None:
        self.routes[(method, path)] = lambda _request: _json(payload, status)

    def add_route(self, method: str, path: str, route: Route) -> None:
        self.routes[(method, path)] = route

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # Undecoded: `%2F` inside a segment must stay distinct from `/`.
        route = self.routes.get((request.method, request.url.raw_path.decode().partition("?")[0]))
        if route is None:
            return _json({"error": {"code": "NOT_FOUND", "message": "no such route", "status": 404}}, 404)
        return route(request)


@dataclass
class Result:
    code: int
    stdout: str
    stderr: str


@dataclass
class Cli:
    api: FakeApi
    keyring: FakeKeyring
    store: TokenStore
    sleeps: list[float]
    read_secret: Callable[[str], str] = field(default=lambda _prompt: "")

    def __call__(self, *argv: str, stdin: io.StringIO | None = None) -> Result:
        stdout, stderr = io.StringIO(), io.StringIO()
        context = Context(
            self.store,
            stdin or io.StringIO(),
            stdout,
            stderr,
            transport=httpx.MockTransport(self.api.handle),
            sleep=self.sleeps.append,
            read_secret=self.read_secret,
        )
        code = run(list(argv), context)
        return Result(code, stdout.getvalue(), stderr.getvalue())


@pytest.fixture
def cli(tmp_path: pathlib.Path) -> Cli:
    fake_keyring = FakeKeyring()
    store = TokenStore(tmp_path / "gv", fake_keyring, {})
    store.save(TOKEN)
    return Cli(FakeApi(), fake_keyring, store, [])


def _json(payload: object, status: int) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
