# GitVerse public API — notes

What `gv` relies on, with sources. Read on 2026-10-14; quotes are translated from Russian.

## Headers, auth, errors

Source: <https://gitverse.ru/docs/developers/public-api>,
<https://gitverse.ru/docs/developers/public-api/using-public-api/api-versioning>

- Base URL `https://api.gitverse.ru`, no path prefix.
- `Authorization: Bearer <token>` — required.
- `Accept: application/vnd.gitverse.object+json;version=1` — required. "The version is
  mandatory. If the Accept header is missing or invalid, the request fails with
  400 Bad Request." The overview page also mentions `application/vnd.gitverse+json`
  without a version; `gv` uses the versioned form.
- Errors: a JSON object `{"error": {"code", "message", "status", "documentation_url"}}`.
- Pagination: `page` and `per_page`; a `Link` header with `rel="next"` on successful
  responses when there are more results. `gv` follows it (same host only). The maximum
  `per_page` is not documented; `gv` uses 50 (Gitea's cap).
- OpenAPI 2.0 specs: <https://gitverse.ru/gitverse/rest-api-description>
  (`v1/openapi-1.11.json` at the time of writing).

Rate limits (<https://gitverse.ru/docs/developers/public-api/using-public-api/rate-limits>):
2000 requests per hour per user; 429 with `Retry-After` when exceeded;
`GitVerse-RateLimit-User-Remaining` on every response.

## CI/CD

Source: <https://gitverse.ru/docs/developers/public-api/actions> and the spec.

- `GET /repos/{owner}/{repo}/actions/runs` (query `actor`, `branch`, `event`, `status`,
  `per_page`, `page`) → `{total_count, workflow_runs: [run]}`.
- `GET …/actions/workflows/{workflow}/runs` — the same for one workflow (file name).
- `GET …/actions/runs/index/{run_index}` → a run (`id`, `index`, `name`, `title`,
  `status`, `event`, `ref`, `commit_sha`, `started`, `stopped`, `actor`, …);
  `…/runs/index/{run_index}/jobs` → `{total_count, jobs: [job]}` (`id`, `name`, `status`,
  `failed_step`, `steps`, …).
- `POST …/actions/runs/{run_id}/cancel`, `POST …/actions/runs/{run_id}/rerun`,
  `POST …/actions/jobs/{job_id}/rerun`.
- `GET …/actions/jobs/{job_id}/logs` — the job's log; the spec gives no response schema.
- `POST …/actions/workflows/{workflow}/dispatches`, query `branch` or `tag`, body: a flat
  map of input name → string value; answers 204.

## Packages

Source: <https://gitverse.ru/docs/developers/public-api/packages>

- `GET /orgs/{org}/packages/{type}/{name}/versions`,
  `DELETE /orgs/{org}/packages/{type}/{name}/versions/{version_id}`,
  `DELETE /orgs/{org}/packages/{type}/{name}`; the same under `/users/{username}/…`.
- There is no endpoint that lists packages. Container images have type `container`.

## Tokens

Source: <https://gitverse.ru/docs/collaborative/authentification/tokens>

- Packages: "Read" lets you read package contents, "Delete" lets you remove packages
  from the registry. Repositories: tick "Repositories". Which permission covers the
  Actions API is not documented.

## Not yet verified against the live API

- The values of run and job `status` (taken from Gitea; `gv` treats `success` and
  `skipped` as passed and `unknown`, `waiting`, `running`, `blocked`, `queued`, `pending`,
  `in_progress` as active).
- The format of the job log response.
- The dispatch body.
- Encoding of `/` in package names (`myproject%2Fweb`).
- The maximum `per_page`.
