import httpx
import pytest

from gitverse_cli.api import ACCEPT, ApiError, GitVerseClient, RefusedRequestError


def test_every_request_carries_the_versioned_accept_and_the_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    with GitVerseClient("t0ken", httpx.MockTransport(handle)) as client:
        client.get_json("/user")
    assert seen[0].headers["Accept"] == ACCEPT == "application/vnd.gitverse.object+json;version=1"
    assert seen[0].headers["Authorization"] == "Bearer t0ken"
    assert str(seen[0].url) == "https://api.gitverse.ru/user"


def test_pages_follow_the_link_header_up_to_the_limit() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        headers = (
            {} if page == 3 else {"Link": f'<https://api.gitverse.ru/items?page={page + 1}&per_page=2>; rel="next"'}
        )
        return httpx.Response(200, json={"items": [page * 10, page * 10 + 1]}, headers=headers)

    with GitVerseClient("t", httpx.MockTransport(handle)) as client:
        assert client.get_list("/items", "items", None) == [10, 11, 20, 21, 30, 31]
        assert client.get_list("/items", "items", 3) == [10, 11, 20]


def test_a_next_link_to_another_host_is_not_followed() -> None:
    hosts: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json=[1], headers={"Link": '<https://elsewhere.example/items?page=2>; rel="next"'})

    with GitVerseClient("t", httpx.MockTransport(handle)) as client:
        assert client.get_list("/items", None, None) == [1]
    assert hosts == ["api.gitverse.ru"]


def test_an_error_answer_becomes_api_error_with_the_api_code_and_no_token() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        error = {"code": "FORBIDDEN", "message": "Access denied", "status": 403}
        return httpx.Response(403, json={"error": error}, headers={"Retry-After": "60"})

    with GitVerseClient("t0ken-secret", httpx.MockTransport(handle)) as client, pytest.raises(ApiError) as caught:
        client.request("POST", "/repos/o/r/actions/runs/1/rerun")
    assert (
        str(caught.value) == "HTTP 403 FORBIDDEN Access denied (POST /repos/o/r/actions/runs/1/rerun); retry after 60 s"
    )
    assert "t0ken-secret" not in str(caught.value)


def test_an_absolute_url_to_a_lookalike_host_is_refused_before_sending() -> None:
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    with GitVerseClient("t", httpx.MockTransport(handle)) as client, pytest.raises(RefusedRequestError):
        client.request("GET", "https://api.gitverse.ru.evil.example/x")
    assert sent == []


def test_an_error_after_a_redirect_names_the_request_sent_without_any_query() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.gitverse.ru":
            return httpx.Response(302, headers={"Location": "https://storage.example/log?signature=s3cr3t"})
        return httpx.Response(403, text="denied")

    with GitVerseClient("t", httpx.MockTransport(handle)) as client, pytest.raises(ApiError) as caught:
        client.request("GET", "/repos/o/r/actions/jobs/1/logs")
    assert caught.value.path == "/repos/o/r/actions/jobs/1/logs"
    assert "s3cr3t" not in str(caught.value)


def test_a_list_where_an_object_was_expected_is_a_clear_error() -> None:
    with (
        GitVerseClient("t", httpx.MockTransport(lambda _request: httpx.Response(200, json=[1]))) as client,
        pytest.raises(RefusedRequestError),
    ):
        client.get_list("/items", "items", None)
