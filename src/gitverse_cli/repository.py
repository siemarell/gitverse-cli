import re
import subprocess
from dataclasses import dataclass

GITVERSE_HOST = "gitverse.ru"
# https://gitverse.ru/owner/repo(.git), ssh://git@gitverse.ru[:port]/owner/repo.git, git@gitverse.ru:owner/repo.git
_REMOTE_URL = re.compile(
    r"^(?:https?://(?:[^@/]+@)?|ssh://(?:[^@/]+@)?|[^@/]+@)" + re.escape(GITVERSE_HOST) + r"(?::\d+)?[:/]"
    r"(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
)


class RepositoryNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class Repository:
    owner: str
    name: str

    def __str__(self) -> str:
        return f"{self.owner}/{self.name}"


def resolve_repository(explicit: str | None) -> Repository:
    """`-R owner/repo`, else the gitverse.ru remote of the current directory (`origin` first)."""
    if explicit is not None:
        owner, _, name = explicit.partition("/")
        if not owner or not name or "/" in name:
            raise RepositoryNotFoundError(f"expected OWNER/REPO, got {explicit!r}")
        return Repository(owner, name)
    for url in _remote_urls():
        repository = parse_remote_url(url)
        if repository is not None:
            return repository
    raise RepositoryNotFoundError("no gitverse.ru remote in this directory; pass -R OWNER/REPO")


def parse_remote_url(url: str) -> Repository | None:
    match = _REMOTE_URL.match(url.strip())
    if match is None:
        return None
    return Repository(match["owner"], match["name"])


def _remote_urls() -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "config", "--get-regexp", r"^remote\..*\.url$"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    urls_by_remote = dict(line.split(" ", 1) for line in completed.stdout.splitlines() if " " in line)
    origin = urls_by_remote.pop("remote.origin.url", None)
    return ([origin] if origin else []) + list(urls_by_remote.values())
