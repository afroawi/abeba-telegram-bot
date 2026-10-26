# Abeba Telegram bot — initial implementation

One bot offers native Telegram AI chat (`/chat`), the sibling frontend’s Mini App (`/app`), and published multilingual Learn articles (`/learn`). AI chat and the Mini App share the same Django account, consent, conversation context and quota. Learn reading uses the public content API independently.

This development slice implements text chat, formatted answers, a welcome character, multilingual topic browsing, on-demand spoken replies, new/select recent conversations, English/Amharic language choice, Mini App handoff, pause and consent withdrawal. It deliberately uses long polling; durable production webhook ingestion and notifications are next milestones.

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

`--check` validates configuration without making Telegram/backend requests. Actual startup registers commands and the Telegram command menu on the configured bot, then polls messages and callbacks. Use a dedicated development bot. Startup refuses to run if a webhook is already registered; it does not remove that webhook or drop pending updates.

## User flow

`/start` shows the welcome character and command guide. Open Telegram’s command menu or type `/` for `/learn`, `/chat`, `/app` and the other commands. `/chat` explains AI processing and requires an explicit “I agree — start chatting” tap before enabling AI chat. Previously consented users can use `/chat` to resume a paused conversation. `/app` returns a one-off Mini App launcher for the current owned conversation; it is not repeated below answers. AI answers keep only their optional Listen button. The retired Show illustration option is no longer displayed.

Commands: `/learn`, `/chat`, `/app`, `/ask question`, `/new`, `/history`, `/language`, `/listen`, `/stop`, `/privacy`, `/withdraw`, `/help`. `/stop` pauses native chat but leaves Mini App access available; `/withdraw` disables AI processing in both interfaces. There are no reminder subscriptions in this initial release. Group messages never load a user's private account or call the AI.

The backend creates channel sessions using a trusted service credential and the verified Telegram sender. Do not accept a sender ID from arbitrary web requests. Bot tokens are short-lived in the service response; database credentials are hashed. Current implementation obtains a scoped session per action.

## Limits and next steps

Native messages use deterministic request keys so an API retry cannot charge/generate the same logical request twice. This does not guarantee exactly-once Telegram delivery: a send timeout can be ambiguous. A process crash during polling may lose a transport reply or accepted update; durable updates, background AI jobs and an outbound delivery ledger are required before production.

Native answers render an escaped Markdown subset as Telegram HTML: headings, emphasis, lists, inline code and HTTPS links. Structured examples, follow-up suggestions and reading links appear as separate sections. Long replies preserve formatting, emoji and words across message boundaries. Incoming voice-note transcription, quizzes, cycle logging, services, resources, reminders, linking/deletion and broader UI localization remain in the implementation plan. The Mini App uses the shared frontend; it does not read Telegram's transcript directly.

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

## Formatted replies, welcome character and spoken answers

`/start` sends the supplied welcoming Abeba character and a formatted introduction. There is no Show illustration button or repeated chat/app navigation under answers. The welcome image is packaged in the Python wheel and Docker image; a failed upload does not prevent text. Existing topic-character assets remain bundled for future use, but no chat command sends them. AI-supplied media URLs are never fetched.

Tap **Listen · በድምፅ** under an answer to hear that exact saved answer. `/listen` reads the latest assistant answer in the active conversation. Exact messages such as `read it aloud` and `send audio` also work. Audio is generated only on request, uploaded as MP3 with an AI-voice disclosure, and split into numbered parts for long answers. It reads the answer and up to three examples; reading links and suggested next questions remain in the text. Failures leave the text answer available. This feature does not transcribe incoming voice notes.

The backend must include `POST /api/v1/channels/chat/audio/`. It accepts `{ "message_id": 123, "part": 1 }`, validates channel authentication, answer ownership, current AI consent and native-chat pause state, and streams MP3 bytes. It accepts only saved assistant answers, not arbitrary TTS text. It uses the backend's existing OpenAI key and admin-managed TTS model/voice/tone settings. No OpenAI key belongs in the bot environment. Long text is split using character and conservative byte budgets, including Amharic. The first 300 UTF-8 bytes of tone instructions are used to leave space within the mini-TTS input limit.

Audio is cached privately for 15 minutes, with keys scoped to the user, answer, text and voice configuration; no public media URL is created. The default Django cache is process-local, so different workers may generate the same part independently. A shared Django cache can deduplicate across workers. Requests are throttled at 20 audio parts per minute per account. Speech uses provider billing separately from the chat token allowance. English and Amharic text are preserved; pronunciation should be checked with the configured voice on the real bot.

Optional bot settings (enabled by default, passed through both standalone Compose files):

```dotenv
ABEBA_BOT_IMAGES_ENABLED=true
ABEBA_BOT_AUDIO_ENABLED=true
```

Set either to `false` to disable it. The backend TTS configuration can also disable spoken replies app-wide. Deploy the backend update before enabling audio on the bot. The existing mobile TTS endpoint remains unchanged.

References: [Telegram formatting](https://core.telegram.org/bots/api#formatting-options), [Telegram MP3 audio](https://core.telegram.org/bots/api#sendaudio), [OpenAI text-to-speech](https://developers.openai.com/api/docs/guides/text-to-speech).


### Existing speech-provider retirement

The official OpenAI documentation states that `gpt-4o-mini-tts`, `tts-1` and `tts-1-hd` shut down on **2027-01-06**. This update deliberately retains the existing backend/admin speech configuration for the current deployment. Schedule migration of the speech-provider adapter before that date. OpenAI's documented replacement uses the Realtime API, so changing only the model string in the existing Speech API call is insufficient. The bot's authenticated MP3 endpoint can remain stable while its provider implementation changes.

Source, checked 2026-10-26: [OpenAI text-to-speech documentation](https://developers.openai.com/api/docs/guides/text-to-speech).


## Read published topics with /learn

`/learn` opens a reading-language picker: English, Amharic, Tigrinya, Oromo, Afar, Sidama and Somali. Selecting a language lists the published topics with pagination. Select a topic to read the full article in Telegram, including its summary, Markdown or structured sections, key points, support contacts, source link and any published audio-resource links. Long articles are split safely, with **Topics** and **Reading language** controls on the final message. Changing language from an article keeps the same topic. UI labels use English and Amharic; article content uses all seven published languages.

Learn uses unauthenticated `GET learn/topics/?language=…&page=…` and `GET learn/topics/<slug>/?language=…`; it never sends a service credential or channel bearer token to these public endpoints, creates no user session, and makes no AI requests. Reading does not require AI consent and continues while AI chat is paused or consent is withdrawn. The reading language is stored in navigation callbacks and does not change the shared AI-chat language preference.

Only published content returned by the backend is shown. The existing API can fall back to English when a translation is missing; the bot labels that fallback instead of presenting English as a translation. Topics are selected by a stable numeric catalog ID and resolved against the current page, keeping callback data within Telegram’s 64-byte limit even for long slugs. An edited/deleted/moved topic prompts the user to choose from the refreshed list. Pagination follows only the page number from API links and always requests the configured backend path.

Startup now sets `MenuButtonCommands`, so Telegram’s menu opens the slash-command list. The Mini App is available through `/app`. No backend change, migration, new port or Nginx update is required for Learn browsing.
