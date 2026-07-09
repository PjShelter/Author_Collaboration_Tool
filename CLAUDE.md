# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

AstrBot-based QQ blacklist bot for author collaboration communities. It is a parallel rewrite of `Author_Collaboration_Tool` (botpy 版); identifiers here are integer QQ numbers (`group_id` / `user_id`), not OpenAPI `openid`s. The bot exposes a few public slash commands, automatically kicks/warns members of a curated risk list when they join a watched group, and offers Live2D / card query helpers against a public mirror.

## Stack and layout

- **Framework**: AstrBot v4.23.5 (vendored as a git submodule/clone under `AstrBot/`). The custom plugin lives at `AstrBot/data/plugins/author_collaboration/`.
- **QQ transport**: OneBot v11 via `mlikiowa/napcat-docker`, wired to AstrBot over a **reverse WebSocket** (napcat → AstrBot on port 6196).
- **Deployment**: Docker Compose — `astrbot` and `napcat` services share an internal `acbot-net` bridge network.
- **Persistence**: plugin-local `data/risk_profiles.yaml` (gitignored runtime DB at `data/bot.db`) under the plugin's `data/` directory.

Top-level layout:

```
docker-compose.yml          # astrbot + napcat services
.env / .env.example         # ONEBOT_ACCESS_TOKEN, TZ
napcat-config/, napcat-logs/  # napcat persistent state (gitignored)
AstrBot/                    # vendored AstrBot v4.23.5 source
  data/plugins/author_collaboration/   # ← the plugin we own
    main.py                 # Star subclass + @filter handlers
    _conf_schema.json       # AstrBot WebUI config schema
    metadata.yaml           # AstrBot plugin metadata (name must == dir)
    requirements.txt        # PyYAML, APScheduler
    lib/                    # reusable logic (see below)
    data/                   # risk_profiles.yaml, bot.db, live2d_packages/
AstrBot/astrbot/            # upstream AstrBot backend
AstrBot/dashboard/          # upstream Vue 3 dashboard
```

## Common commands

All commands run from the repository root unless noted.

### Run / restart the stack
```bash
docker compose up -d                       # start both services
docker compose logs -f astrbot napcat      # follow logs
docker compose restart astrbot             # restart after plugin code edit
docker compose down                        # stop everything
```

### Verify the plugin loaded
```bash
docker compose logs astrbot | grep -i 'author_collaboration\|plugin'
# expect: "Plugin author_collaboration loaded"
```

### AstrBot WebUI (bot config, plugin config, platform setup)
`http://<host>:6185` — first login sets the admin account.

### napcat WebUI (login the bot QQ, set up reverse WS)
`http://<host>:6099` — Network → Reverse WebSocket → URL `ws://astrbot:6196`, `access_token` copied verbatim from `.env`.

### Backend (AstrBot upstream) — only when touching `AstrBot/astrbot/` or `AstrBot/dashboard/`
```bash
cd AstrBot
uv sync --group dev                      # ~6–7 min, do not cancel
uv run ruff check . && uv run ruff format .   # lint + format (run before committing)
uv run pytest tests                      # backend tests
make pr-test-neo                         # pre-PR validation
cd dashboard && pnpm install && pnpm dev      # local dashboard dev server
cd dashboard && pnpm build                    # type-check + production build
```

Python 3.10+ is required (`AstrBot/.python-version`).

## Plugin architecture (`AstrBot/data/plugins/author_collaboration/`)

`main.py` is the only file with `@filter` decorators. It owns a single `AuthorCollaborationPlugin(star.Star)` that:

- Reads merged config from `lib/config_helper.py` (defaults filled in, paths resolved relative to the plugin root).
- Opens `data/bot.db` via `lib/db.init_db` on construction.
- Instantiates `lib/live2d_api.ShelterLive2DClient` for Bestdori Live2D lookups.
- Starts an `AsyncIOScheduler` (used for delayed kicks after a hit).

`lib/` modules — keep these free of `@filter`/AstrBot-specific imports so they stay unit-testable:

| File | Purpose |
|---|---|
| `config_helper.py` | Plugin-root path constants (`PLUGIN_ROOT`, `PLUGIN_DATA_DIR`), default config, `merge_config`, path resolvers. |
| `risk_profiles.py` | YAML loader/saver; `RiskMatch` dataclass; `find_match` / `bind_member` / `unbind_member` / `list_profiles`. Field renames vs the botpy version: `group_openid` → `group_id`, `member_openid` → `user_id`. `group_id < 0` matches every group. |
| `blacklist.py` | Thin facade over `risk_profiles` — `is_blacklisted`, `get_blacklist_match`, `format_blacklist`. |
| `db.py` | SQLite schema (`bot_groups`, `member_events`, `alert_log`, `group_download_usage`) and CRUD helpers. Schema unchanged from the botpy 版. |
| `notify.py` | `trim_message` (QQ 1800-char ceiling) and `join_lines`. |
| `live2d_api.py` | Async `httpx` client wrapping the `live2d.shelter.net.cn` mirror (cards, models, file lists, ZIP packaging). |

## Commands exposed

All commands work in private chat and in group chat via `/`. Each has Chinese aliases plus a single-digit shortcut (`1`–`6`, `0`).

| Shortcut | Command | What it does |
|---|---|---|
| `/1` | `查询黑名单` | Lists `risk_profiles.yaml`; in a group also scans members via `get_group_member_list` and kicks/warns any matches found. |
| `/2` | `加入ACT作者互助会` | Replies with `act_group_number` (default `621930922`). |
| `/3` | `寻求帮助` | Comfort message + ACT group number. |
| `/4` | `卡面查询` | Bestdori card search via Shelter Live2D mirror; supports pagination (`p N`, `第N页`, `N`) and exact card ID lookup with images. |
| `/5` | `小人下载` | **Currently disabled** (early-return stub at line ~726 of `main.py`). |
| `/6` | `live2d下载` | Bestdori Live2D model search + ZIP packaging; consumes the per-group daily quota (`daily_download_limit_bytes`, default 100 MB). Sends via `upload_group_file` / `upload_private_file` / `File` message in that order. |
| `/0` | `帮助` | Shows the menu text. |

The `@filter.custom_filter(GroupIncreaseFilter())` handler in `main.py` watches OneBot `notice / group_increase` events. On a hit: bind the member, mute 60s, announce, schedule a delayed `set_group_kick(reject_add_request=True)` 60s later via APScheduler. Every outcome is logged through `db.record_*`.

A high-priority (`maxsize - 1`) aiocqhttp-only message handler shows the menu when the bot is `@`-mentioned in a group or private-chatted but no known command is recognized.

## Configuration

Plugin config schema: `AstrBot/data/plugins/author_collaboration/_conf_schema.json`. Defaults live in `lib/config_helper._DEFAULTS`. Notable knobs:

- `trusted_group_ids` (list[int], empty = all groups active): combined with the in-YAML `trusted_groups` to gate the auto-kick pipeline.
- `kick_on_match` (bool, default true): set false to downgrade hits to an `@` warning only.
- `risk_profiles_path` / `bot_db_path` — relative to the plugin root, parent dirs auto-created.
- `act_group_number` — returned by `/2` and `/3`.
- `max_text_len` — applied via `notify.trim_message` before sending.
- `daily_download_limit_bytes` — per-group, per-UTC-day quota for `/6`.

`data/risk_profiles.yaml` shape (see top of file for the live example):
```yaml
admins:           # optional audit list
trusted_groups:   # group allowlist (negative group_id = match all)
profiles:         # one entry per person
  - person_id: risk_xxx
    display_name: ...
    qq_number: '...'
    aliases: [...]
    risk_level: high | medium | low
    reason: "..."
    evidence: [...]
    mapped_members:
      - { group_id: <int or -1>, user_id: <int>, note: "..." }
```
YAML changes are hot-reloaded by AstrBot on the next group-increase event.

## Networking rules (the easy foot-guns)

- AstrBot and napcat **must** share the exact same `ONEBOT_ACCESS_TOKEN` (in `.env`, napcat reverse-WS page, and AstrBot platform config). One mismatch and nothing connects.
- Inside the compose network the napcat → AstrBot URL is `ws://astrbot:6196` (container hostname), **not** `localhost`.
- Exposed ports: `6185` (AstrBot WebUI), `6196` (AstrBot reverse WS, must be reachable by napcat container), `6099` (napcat WebUI).
- Plugin code is mounted read-only-ish via `./AstrBot/data:/AstrBot/data` — AstrBot's main binary stays in the `soulter/astrbot:v4.23.5` image. Only the `data/` subtree is local.
- After editing `main.py` or anything under `lib/`, restart `astrbot` (`docker compose restart astrbot`); AstrBot's plugin loader doesn't hot-reload Python.

## Sensitive / gitignored

`data/bot.db`, `napcat-config/`, `napcat-logs/`, `.env`, and any `data/*.yaml.bak` are gitignored. `data/risk_profiles.yaml` is committed (it is the canonical moderation data) but treat its contents as operational data — no need to add PII beyond what moderation requires. Don't commit real passkeys, napcat `passkey.json`, or production logs. See `AstrBot/.github/copilot-instructions.md` for upstream AstrBot development conventions when modifying files under `AstrBot/astrbot/` or `AstrBot/dashboard/`.