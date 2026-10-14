# gv — GitVerse from the command line

`gv` is a small, `gh`-style CLI for the public REST API of [GitVerse](https://gitverse.ru):
CI/CD runs and job logs, manual workflow runs, registry packages, and raw API calls.

It exists because GitVerse has no official CLI, and reading CI logs through the web UI
(or copying them into a chat with a coding agent) gets old fast. The token lives in your
system keyring, so a script or an AI agent can use `gv` without ever seeing it.

> Status: early. Request and response shapes follow GitVerse's documentation and OpenAPI
> spec; some of them have not been checked against the live API yet — see
> [docs/API_NOTES.md](docs/API_NOTES.md). Issues and pull requests are welcome.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/) (or pipx).

```bash
uv tool install git+https://github.com/siemarell/gitverse-cli
# or: pipx install git+https://github.com/siemarell/gitverse-cli
```

Update with `uv tool upgrade gitverse-cli`.

## Log in

Create a personal token at gitverse.ru → Settings → Token management, then:

```bash
gv auth login     # paste the token at the hidden prompt (or pipe it on stdin)
gv auth status    # who you are and how many API requests are left this hour
gv auth logout
```

- The token is checked (`GET /user`) before it is stored.
- It is stored in the system keyring (macOS Keychain, Secret Service, Windows Credential
  Locker). Without a usable keyring it falls back to `~/.config/gv/token` with mode 0600.
- `GV_TOKEN` in the environment overrides both.
- The token is never printed, never put in error messages, and only ever sent to
  `https://api.gitverse.ru`.

Token permissions: tick «Repositories» for runs, logs and workflows. For packages:
«Read», plus «Delete» if you want to clean the registry.

## Usage

Inside a clone, the repository is taken from its `gitverse.ru` git remote; anywhere else
pass `-R owner/repo`.

```bash
# Runs
gv run list                              # #N  status  workflow  event ref  title  started
gv run list -w ci.yml -b main -s failure -L 5
gv run view 12                           # run #12, its jobs and the failed step of each
gv run view 12 --log-failed --tail 200   # logs of the jobs that did not pass
gv run view 12 --log --job 501           # one job's full log
gv run watch 12                          # wait; exit 0 only if it succeeded (or was skipped)
gv run rerun 12                          # or: --job 501
gv run cancel 12

# Workflows
gv workflow list
gv workflow run deploy.yml --ref main -f environment=staging

# Registry packages (there is no API endpoint to list packages: name it)
gv package versions container myproject/backend
gv package delete container myproject/backend --version 7      # asks first; -y to skip
gv package versions npm my-lib --owner someone --user          # a user's package

# Anything else, like `gh api` ({owner} and {repo} are filled in)
gv api repos/{owner}/{repo}/actions/runs -f status=failure
gv api repos/{owner}/{repo}/actions/runs/123/cancel -X POST
```

`--json` on `list`/`view`/`versions` prints the API's JSON instead of compact text.

Run numbers are the `#N` shown by `gv run list` (the run's index in the repository).

Exit codes: `0` ok, `1` failure (API error, failed run under `watch`), `2` usage error,
`4` not logged in, `130` interrupted.

## Using it from an AI coding agent

[`skills/gitverse/SKILL.md`](skills/gitverse/SKILL.md) is a ready-made skill for Claude
Code (copy it to `.claude/skills/gitverse/` of your project or to `~/.claude/skills/`). It
tells the agent to read runs and logs freely, to leave logging in to you, and to ask before
anything that writes (dispatch, rerun, cancel, delete).

## Development

```bash
uv sync
uv run ruff format --check . && uv run ruff check .
uv run basedpyright
uv run pytest
```

Tests run against a fake API (`httpx.MockTransport`) and a fake keyring; nothing touches
the network.

## License

MIT — see [LICENSE](LICENSE).
