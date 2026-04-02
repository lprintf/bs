---
trigger: model_decision
description: git commit 消息撰写规则
---

# Commit Message Rules
- Use clear, descriptive commit messages following Conventional Commits.
- **Format**: `<type>(<scope>): <subject>`
- **Types**:
  - `feat`: New feature.
  - `fix`: Bug fix.
  - `docs`: Documentation changes.
  - `style`: Changes that do not affect the meaning of the code (white-space, formatting, etc).
  - `refactor`: A code change that neither fixes a bug nor adds a feature.
  - `perf`: A code change that improves performance.
  - `test`: Adding missing tests or correcting existing tests.
  - `chore`: Changes to the build process or auxiliary tools and libraries.
- **Rules**:
  - Commits should be atomic (one logical change per commit).
  - Use the imperative, present tense: "change" not "changed" nor "changes".
  - The first letter of the subject should be lowercase (optional, but consistent).
  - No period at the end of the subject line.