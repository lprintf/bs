# Development Practices

## Core Rules

| Rule | Requirement |
| --- | --- |
| Main branch | Do not develop directly in the main worktree. |
| Task isolation | Create one branch and one worktree per task. |
| Worktree location | Put local task worktrees under `worktree/`. |
| Directory naming | Replace `/` in branch names with `-`. |
| Git remote | Use SSH remotes only; do not use HTTPS remotes for this project. |
| Durable state | Keep durable state in commits and remote branches, not local directories. |
| Cleanup | Clean local worktrees only after merge, or after explicit branch abandonment and remote branch deletion. |

## Standard Flow

```sh
git worktree add -b codex/task-name worktree/codex-task-name main
cd worktree/codex-task-name
```

Work in the task worktree, then verify, commit, and push:

```sh
git status --short --branch
git remote -v
git commit -m "type: concise subject"
git push -u origin codex/task-name
```

The expected remote form is:

```sh
origin  git@github.com:lprintf/bs.git (fetch)
origin  git@github.com:lprintf/bs.git (push)
```

`origin` is local clone configuration and is not committed. If a checkout has HTTPS configured, fix it before pushing:

```sh
git remote set-url origin git@github.com:lprintf/bs.git
```

After the branch is merged into `main`, or after the branch is explicitly abandoned and the remote branch is deleted:

```sh
git worktree remove worktree/codex-task-name
git branch -d codex/task-name
```

Use `git branch -D` only when the branch was explicitly abandoned and its remote branch has already been deleted.

## VS Code

Keep `worktree/` ignored by Git. Track only active task worktrees in local VS Code settings:

```jsonc
{
  "git.scanRepositories": [
    "worktree/codex-task-name"
  ]
}
```

Do not commit `.vscode/settings.json`; active worktree lists are machine-local.

After changing `.vscode/settings.json`, use the lightest refresh that works:

| Need | Command |
| --- | --- |
| Refresh one detected repository | `Git: Refresh` |
| Reopen repositories that VS Code previously closed | `Git: Reopen Closed Repositories` |
| Add one active worktree explicitly | `Git: Open Repository` |
| Restart Git and other extensions without reloading the window | `Developer: Restart Extension Host` |
| Last resort | `Developer: Reload Window` |

For ignored worktrees, prefer `Git: Open Repository` when you want deterministic behavior. Select the active worktree directory, for example:

```text
/home/lprintf/ai-space/bs/worktree/codex-task-name
```

## Review Before Finishing

| Check | Command |
| --- | --- |
| Current branch | `git status --short --branch` |
| Worktree list | `git worktree list --porcelain` |
| Unpushed commits | `git log --oneline @{u}..HEAD` |
| Remote branch exists | `git ls-remote origin refs/heads/<branch>` |
| Diff scope | `git diff --stat` and `git diff --cached --stat` |

## Prohibited

- Do not edit in the main worktree for task work.
- Do not use HTTPS Git remotes for this project.
- Do not remove a local worktree before commits are pushed.
- Do not delete a local branch while its merge or abandonment state is unclear.
- Do not leave unreviewed task directories as durable state.
- Do not revert or delete unrelated user changes.
