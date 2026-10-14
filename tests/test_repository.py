import pytest

from gitverse_cli.repository import Repository, RepositoryNotFoundError, parse_remote_url, resolve_repository


@pytest.mark.parametrize(
    "url",
    [
        "https://gitverse.ru/acme/shop.git",
        "https://gitverse.ru/acme/shop",
        "https://user@gitverse.ru/acme/shop.git",
        "git@gitverse.ru:acme/shop.git",
        "ssh://git@gitverse.ru:2222/acme/shop.git",
    ],
)
def test_gitverse_remote_urls_name_the_repository(url: str) -> None:
    assert parse_remote_url(url) == Repository("acme", "shop")


@pytest.mark.parametrize(
    "url", ["https://github.com/a/b.git", "git@gitverse.ru:only-owner", "https://gitverse.ru/a/b/c"]
)
def test_other_urls_name_nothing(url: str) -> None:
    assert parse_remote_url(url) is None


def test_the_option_wins_and_must_be_owner_slash_repo() -> None:
    assert resolve_repository("owner/name") == Repository("owner", "name")
    with pytest.raises(RepositoryNotFoundError):
        resolve_repository("owner")
