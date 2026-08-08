# Repository Instructions

## Feature Documentation

`FEATURES.md` is the source of truth for the bot's implemented user-facing behavior.

Whenever a feature, command, permission rule, moderation behavior, environment variable, persistent file, or known limitation is added, changed, or removed:

1. Update `FEATURES.md` in the same change.
2. Keep `nao_bot/rules.py` help text and the README command examples consistent with runtime behavior.
3. Update `.env.example`, `compose.yaml`, and deployment instructions when configuration changes.
4. Add or update focused behavior tests.
5. Run `python -m pytest -q` and `docker compose --env-file .env.example config -q` before committing.

Do not commit `.env`, QQ login state, production data, API keys, access tokens, server credentials, or real user identifiers.
