# Local Git Worktrees

Put repository-local git worktrees under `worktree/`.

Use a safe directory name by replacing `/` in branch names with `-`.

```sh
git worktree add -b codex/foo worktree/codex-foo main
cd worktree/codex-foo
```

After the branch is committed and pushed, remove the local worktree:

```sh
git worktree remove worktree/codex-foo
```

VS Code can track selected active worktrees through local settings. Keep this file local because active branch directories are machine-specific:

```jsonc
{
  "git.scanRepositories": [
    "worktree/codex-foo",
    "worktree/feat-bar"
  ]
}
```

Rules:

- `worktree/` is ignored by Git.
- `worktree/` is excluded from Docker build contexts.
- Local worktree directories are disposable; keep durable state in branches and remote branches.
