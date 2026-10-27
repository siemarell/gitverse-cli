---
name: gitverse
description: GitVerse CI/CD and package registry through the `gv` CLI — list pipeline runs, read job logs (why CI or a deploy failed), watch a run, start a workflow manually, rerun or cancel a run, list and delete package versions, create and view repositories, raw API calls. Use whenever a task needs GitVerse CI state or logs instead of asking the user to copy them.
---

# GitVerse via `gv`

`gv` wraps GitVerse's public REST API (`api.gitverse.ru`), `gh`-style.
Source and docs: https://github.com/siemarell/gitverse-cli

## Setup (once)

- `command -v gv` fails → `uv tool install git+https://github.com/siemarell/gitverse-cli`.
- `gv auth status` exits 4 → **the user logs in, not you**: ask them to run
  `gv auth login` in their own terminal and paste the token at the hidden prompt. Never ask
  for the token in the chat, never pass it as an argument, never print `GV_TOKEN`.

## Reading — free to run

```bash
gv run list                               # recent runs: #N status workflow event ref title started
gv run list -w ci.yml -L 5 -b main        # one workflow / branch; -s failure filters by status
gv run view 12                            # run #12 and its jobs, failed step of each
gv run view 12 --log-failed --tail 200    # logs of the jobs that did not pass
gv run view 12 --log --job 501            # one job's whole log
gv run watch 12                           # waits; exit 0 only on success (or skipped)
gv workflow list
gv package versions container <name>      # versions with their ids
gv repo view <owner>/<repo>               # visibility, default branch, clone URLs
gv api repos/{owner}/{repo}/actions/runs  # anything else, like `gh api`
```

`--json` on list/view commands gives the API's JSON. Run numbers are the `#N` of
`gv run list`; `-R owner/repo` overrides the repository taken from `git remote`.
Start with `--log-failed --tail 200`: full logs are long.

## Writing — only with the user's explicit yes

These change deployments or the registry. Name the exact command and wait for a yes.

```bash
gv workflow run <file.yml> --ref <branch> -f key=value
gv run rerun 12            # or --job <id>
gv run cancel 12
gv package delete container <name> --version <id> --yes   # irreversible
gv repo create <org>/<name> -d "…"         # private unless --public; prints the remote to push to
```

A dispatch prints no run number: follow it with `gv run list -w <file.yml> -L 1`, then
`gv run watch <N>`.

## Limits

- No endpoint lists an owner's packages: the package name must be known.
- Some response shapes are taken from the OpenAPI spec, not seen live. A command that
  fails in an unexpected way: retry it as `gv api …` and report the raw answer.
- 2000 API requests per hour per user; `gv auth status` shows what is left.
