---
trigger: model_decision
description: git branch 管理规则
---

# Branch Manager Rules
- `main`: Production-ready code. Always stable.
- `codex/*`: Agent-created branches.
- `feat/*`: New features or enhancements.
- `fix/*`: Bug fixes for existing features.
- `refactor/*`: Code refactoring without functional changes.
- `docs/*`: Documentation changes.

## Naming Convention
- Use lowercase letters.
- Use hyphens (`-`) as word separators.
- Example: `feat/add-login-system`.

## Worktree Workflow
- Code changes and complex tasks use a local task worktree by default.
- Small documentation/rule changes may be made in the current branch when parallel development is unnecessary.
- Create task worktrees under `worktree/<safe-branch-name>`.
- Convert `/` in branch names to `-` for directory names.
- Keep `worktree/` ignored by Git and Docker.
- Add unmerged local worktrees to `.vscode/settings.json` `git.scanRepositories`.
- `.vscode/settings.json` is local machine state and must not be committed.
- Push remote branches only for collaboration, backup, PRs, or release/publish work.
- After merge or explicit abandonment, remove the worktree from `git.scanRepositories`, remove the local worktree, then delete the local branch if no longer needed.
- Before cleanup, verify the worktree is clean.
- Never delete or revert unrelated user changes from the main worktree.
