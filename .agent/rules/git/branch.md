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
- Do not develop directly in the main worktree.
- Create task worktrees under `worktree/<safe-branch-name>`.
- Convert `/` in branch names to `-` for directory names.
- Keep `worktree/` ignored by Git and Docker.
- Use SSH Git remotes only; do not use HTTPS remotes for this project.
- Commit and push the task branch before cleanup.
- Clean local worktrees only after the branch is merged into `main`, or after the branch is explicitly abandoned and the remote branch is deleted.
- Before cleanup, verify the worktree is clean and has no unpushed commits.
- Remove the local worktree first, then delete the local branch if it is no longer needed.
- Never delete or revert unrelated user changes from the main worktree.
