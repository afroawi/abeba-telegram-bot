# Abeba Telegram bot — initial implementation

One bot offers **Chat here** (native Telegram messages) and **Open chat app** (the sibling frontend's Mini App). Both use the same Django account, consent, conversation context and quota.

This development slice implements text chat, new/select recent conversations, English/Amharic language choice, Mini App handoff, pause and consent withdrawal. It deliberately uses long polling; durable production webhook ingestion and notifications are next milestones.

## Setup

Requires Python 3.11+ (tested with Python 3.13), the migrated/enabled backend, a development bot token from BotFather, and a real public HTTPS frontend URL.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install -e . --no-deps
cp .env.example .env
```

Set these locally in `.env`:

- `TELEGRAM_BOT_TOKEN`: development bot token; also configure it as `ABEBA_TELEGRAM_BOT_TOKEN` in Django for signed Mini App verification.
- `ABEBA_TELEGRAM_SERVICE_TOKEN`: random secret matching Django; never expose it to the frontend.
- `ABEBA_API_BASE_URL`: normally `http://127.0.0.1:8000/api/v1/` locally; use HTTPS remotely.
- `ABEBA_MINI_APP_URL`: the deployed frontend's `https://your-real-domain/telegram/chat` route.

```sh
.venv/bin/python -m abeba_bot --check
.venv/bin/python -m abeba_bot
.venv/bin/python -m pytest -q
```

`--check` validates configuration without making Telegram/backend requests. Actual startup registers commands and the Open chat app menu on the configured bot, then polls messages and callbacks. Use a dedicated development bot. Startup refuses to run if a webhook is already registered; it does not remove that webhook or drop pending updates.

## User flow

`/start` shows an explanation of AI processing and two buttons. **Chat here** records affirmative AI consent and enables native chat. **Open chat app** opens the web chat; consent is collected there if needed. Replies include a button to continue that exact owned conversation in the Mini App.

Commands: `/ask question`, `/new`, `/history`, `/language`, `/stop`, `/privacy`, `/withdraw`, `/help`. `/stop` pauses native chat but leaves Mini App access available; `/withdraw` disables AI processing in both interfaces. There are no reminder subscriptions in this initial release. Group messages never load a user's private account or call the AI.

The backend creates channel sessions using a trusted service credential and the verified Telegram sender. Do not accept a sender ID from arbitrary web requests. Bot tokens are short-lived in the service response; database credentials are hashed. Current implementation obtains a scoped session per action.

## Limits and next steps

Native messages use deterministic request keys so an API retry cannot charge/generate the same logical request twice. This does not guarantee exactly-once Telegram delivery: a send timeout can be ambiguous. A process crash during polling may lose a transport reply or accepted update; durable updates, background AI jobs and an outbound delivery ledger are required before production.

The initial version renders native answers as plain text, splitting safely within Telegram's text limit. Voice, Learn/Explore menus, quizzes, cycle logging, services, resources, reminders, linking/deletion and broader UI localization remain in the implementation plan. The Mini App uses the shared frontend; it does not read Telegram's transcript directly.

Credentials stay in `.env` and are excluded by `.gitignore`. Avoid enabling HTTP/client debug logging, which can reveal Telegram token paths. Runtime logs record only error categories, never user questions or authorization headers. The Dockerfile is a development polling packaging option, not a production webhook deployment.

## Docker

The image installs the frozen dependencies and bot package, then runs as user 10001. It contains no `.env` file. An HTTP `/healthz` endpoint listens on container port 8081 and is mapped to host port **18151** by Compose. It reports process liveness; chat continues to use Telegram polling and the Mini App continues to load the frontend on port 18150.

```sh
cp .env.example .env
# Configure bot/service credentials, a reachable HTTPS API and public Mini App URL.
docker-compose build
docker-compose run --rm --no-deps bot python -m abeba_bot --check
docker-compose up -d
curl http://127.0.0.1:18151/healthz
```

Set `BOT_PORT` to change the host mapping. Compose fixes the internal listener to `0.0.0.0:8081`; local Python runs default to loopback and can use `ABEBA_BOT_HEALTH_HOST` / `ABEBA_BOT_HEALTH_PORT`. The health listener starts after Telegram command/menu setup and closes when polling exits. Liveness does not guarantee Telegram/backend reachability during retry loops. Use the shared stack or this standalone service to avoid duplicate pollers and port collisions.

For direct image commands:

```sh
docker build -t abeba-telegram-bot .
docker run --rm --env-file .env abeba-telegram-bot python -m abeba_bot --check
docker run --rm --init --read-only --tmpfs /tmp --env-file .env abeba-telegram-bot
docker build --target test .
```

Set `.env` to use a backend reachable from inside Docker. `127.0.0.1` refers to the bot container; use `http://backend:8000/api/v1/` on the shared Compose network or HTTPS for a remote API. The Mini App needs a real public HTTPS URL. Actual startup updates the Telegram commands/menu and begins polling; `--check` performs only local validation. Keep a single running poller for each bot token.

See [`../DOCKER.md`](../DOCKER.md) for the optional Telegram profile in the shared stack when working in the complete Abeba checkout.

## Deploy bot.abebachat.com

Use [DEPLOYMENT.md](DEPLOYMENT.md), `.env.production.example` and `docker-compose.prod.yml` for the existing server. The host Nginx block is in `deploy/host-nginx.conf`.
