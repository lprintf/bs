---
trigger: model_decision
description: Actions to take after modifying a Python file
---

# After Modify .py File
- Before finishing, verify imports and basic app structure:
  `docker compose exec gemini2api uv run python -c "import server.app"`
- Ensure any new dependencies added to the code are also present in `pyproject.toml`.
- Run relevant tests in `scripts/` if the changes affect core logic.