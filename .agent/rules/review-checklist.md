---
trigger: model_decision
description: 收尾前自检清单
---

# Review Checklist

## Always
- Check the branch and dirty state: `git status --short --branch`.
- Check diff scope: `git diff --stat` and, before commit, `git diff --cached --stat`.
- Check whitespace/errors: `git diff --check`.
- Confirm unrelated user changes were not reverted, reformatted, or staged.

## When Relevant
- Frontend changes: run the package build for the touched frontend module.
- Python changes: follow `.agent/rules/python.md`.
- Go SFU changes: run `go test ./...` from `sfu/` when local Go is available; otherwise state the environment blocker.
- Docker/build changes: run the narrowest relevant `docker compose build <service>`.

## Before Final Response
- State what changed.
- State what checks ran.
- State what could not be checked and why.
- Do not claim remote publication unless a push actually happened.
