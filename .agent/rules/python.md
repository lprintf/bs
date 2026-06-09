---
trigger: model_decision
description: Actions to take after modifying a Python file
---

# After Modify .py File
- Before finishing, verify imports and basic app structure:
  `cd tabshare && PYTHONPYCACHEPREFIX=/tmp/bs_pycache python3 -m compileall server`
- Ensure any new dependencies added to the code are also present in `pyproject.toml`.
- Run the relevant project script when the change touches an existing script-tested path, for example:
  `docker compose exec tabshare python3 /app/tests/test_stealth.py`
