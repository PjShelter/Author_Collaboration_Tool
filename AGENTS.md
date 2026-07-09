# Repository Guidelines

## Project Structure & Module Organization

This repository wraps an AstrBot deployment for the Author Collaboration Tool. Root files such as `docker-compose.yml`, `.env.example`, and `napcat-config/` define the OneBot/NapCat runtime. The customized plugin lives in `AstrBot/data/plugins/author_collaboration/`: `main.py` registers commands and events, `lib/` holds reusable logic, and `data/risk_profiles.yaml` stores risk data. Upstream AstrBot backend code is under `AstrBot/astrbot/`; backend tests are in `AstrBot/tests/`. The Vue dashboard is in `AstrBot/dashboard/`.

## Build, Test, and Development Commands

- `docker compose up -d`: start AstrBot and NapCat from the repository root.
- `docker compose logs -f astrbot napcat`: follow runtime logs and verify plugin loading.
- `cd AstrBot && uv sync --group dev`: install Python runtime and dev dependencies.
- `cd AstrBot && ruff format . && ruff check .`: format and lint backend code.
- `cd AstrBot && pytest tests`: run backend tests.
- `cd AstrBot && make pr-test-neo`: run recommended pre-PR validation.
- `cd AstrBot/dashboard && pnpm install && pnpm dev`: run the dashboard locally.
- `cd AstrBot/dashboard && pnpm build`: type-check and build the dashboard.

## Coding Style & Naming Conventions

Python uses Ruff with 88-character lines; prefer 4-space indentation, typed helpers where practical, and `snake_case` names. Keep plugin entry points in `main.py` and reusable logic in `lib/`. Dashboard code uses Vue 3, TypeScript, ESLint, and Prettier; name components in `PascalCase.vue`, composables as `useThing.ts`, and stores by domain.

## Testing Guidelines

Add or update `pytest` tests under `AstrBot/tests/` for backend or plugin behavior. Follow existing `test_*.py` naming and use `tests/fixtures/` for sample configs or messages. Dashboard tests use `.test.mjs` files in `AstrBot/dashboard/tests/`; run relevant checks before touching routing, storage, or build scripts. For deployment-sensitive changes, also run `make pr-test-neo`.

## Commit & Pull Request Guidelines

The AstrBot history and contributor docs use semantic prefixes such as `fix:`, `feat:`, `docs:`, `style:`, `refactor:`, `test:`, and `chore:`. Use `fix/...` or `feat/...` branch names when possible. PR descriptions should be in English, explain the user-visible change, link issues when applicable, and include screenshots for dashboard UI changes. Note any config, database, or deployment impact.

## Security & Configuration Tips

Do not commit real `.env` secrets, NapCat passkeys, production logs, or private risk evidence. Keep `ONEBOT_ACCESS_TOKEN` identical in AstrBot and NapCat configs. Treat `data/risk_profiles.yaml` as sensitive operational data and avoid adding personal information beyond what moderation requires.
