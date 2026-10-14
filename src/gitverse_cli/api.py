import json
import urllib.parse
from collections.abc import Mapping
from typing import Any

import httpx

API_URL = "https://api.gitverse.ru"
# The version is mandatory: without it, or with a wrong one, the API answers 400. The
# overview page also names `application/vnd.gitverse+json`, but without a version — the
# versioning page's form is the one with it. Source: docs/API_NOTES.md.
ACCEPT = "application/vnd.gitverse.object+json;version=1"
# The page size the API takes is not documented; Gitea, whose Actions API this one
# mirrors, caps it at 50.
PAGE_SIZE = 50


class ApiError(Exception):
    """An answer ≥ 400. Carries the request line and the API's own error object — never headers."""

    def __init__(self, method: str, path: str, status: int, code: str, message: str, retry_after: str | None) -> None:
        self.method = method
        self.path = path
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(str(self))

    def __str__(self) -> str:
        parts = [f"HTTP {self.status}", self.code, self.message, f"({self.method} {self.path})"]
        text = " ".join(part for part in parts if part)
        if self.retry_after is not None:
            text += f"; retry after {self.retry_after} s"
        return text


class RefusedRequestError(Exception):
    """A request the client will not send, or an answer of a shape it cannot read."""


class GitVerseClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None, base_url: str = API_URL) -> None:
        self._base_url = base_url.rstrip("/")
        # httpx drops Authorization on a redirect to another origin, so following one
        # (a log served from storage) does not hand the token over.
        self._http = httpx.Client(
            base_url=self._base_url,
            headers={"Accept": ACCEPT, "Authorization": f"Bearer {token}", "User-Agent": "gitverse-cli"},
            transport=transport,
            timeout=60.0,
            follow_redirects=True,
        )

    def __enter__(self) -> "GitVerseClient":
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int] | None = None,
        body: object = None,
    ) -> httpx.Response:
        url = self._own_url(path)
        content = None if body is None else json.dumps(body).encode()
        headers = {} if body is None else {"Content-Type": "application/json"}
        response = self._http.request(method, url, params=params, content=content, headers=headers)
        if response.status_code >= 400:
            raise _api_error(method, response)
        return response

    def get_json(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:  # noqa: ANN401 -- JSON of the API, shape known per endpoint
        return self.request("GET", path, params).json()

    def get_list(
        self,
        path: str,
        items_key: str | None,
        limit: int | None,
        params: Mapping[str, str | int] | None = None,
    ) -> list[Any]:
        """Follows the `Link: rel="next"` pages until `limit` items (None: all of them).

        `items_key` names the array inside an object answer; None: the answer is the array.
        """
        page_size = PAGE_SIZE if limit is None else min(limit, PAGE_SIZE)
        url: str | None = path
        query: Mapping[str, str | int] | None = {**(params or {}), "per_page": page_size}
        items: list[Any] = []
        while url is not None and (limit is None or len(items) < limit):
            response = self.request("GET", url, query)
            payload = response.json()
            if items_key is None:
                page = payload
            elif isinstance(payload, dict):
                page = payload.get(items_key) or []
            else:
                raise RefusedRequestError(
                    f"expected an object with {items_key!r} from {path}, got {type(payload).__name__}"
                )
            if not isinstance(page, list):
                raise RefusedRequestError(f"expected a list from {path}, got {type(page).__name__}")
            if not page:
                break
            items.extend(page)
            url = self._next_page(response)
            query = None  # the next link carries the whole query
        return items if limit is None else items[:limit]

    def _next_page(self, response: httpx.Response) -> str | None:
        next_url = response.links.get("next", {}).get("url")
        if next_url is None or not self._is_own(next_url):
            return None
        return next_url

    def _own_url(self, path: str) -> str:
        # An absolute URL to another host would get the token too: base_url does not stop it.
        if "://" in path:
            if not self._is_own(path):
                raise RefusedRequestError(f"not sending the token outside {self._base_url}: {path}")
            return path
        return "/" + path.lstrip("/")

    def _is_own(self, url: str) -> bool:
        return url == self._base_url or url.startswith(self._base_url + "/")


def path_segment(value: str) -> str:
    """A package name may hold `/` (container images): it is one segment of the path."""
    return urllib.parse.quote(value, safe="")


def _api_error(method: str, response: httpx.Response) -> ApiError:
    code, message = "", ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        error = payload["error"]
        code, message = str(error.get("code") or ""), str(error.get("message") or "")
    elif isinstance(payload, dict) and isinstance(payload.get("message"), str):
        message = payload["message"]
    else:
        message = response.text[:300].strip()
    # The request sent, not the one a redirect led to: a storage URL's query is a signature.
    sent = response.history[0].request if response.history else response.request
    return ApiError(
        method,
        sent.url.path,
        response.status_code,
        code,
        message,
        response.headers.get("Retry-After"),
    )
