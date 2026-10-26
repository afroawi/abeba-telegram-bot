# Deploy bot.abebachat.com on the existing server

The bot connects to `https://dashboard.abebachat.com/api/v1/`. Its `/app` command opens `https://web.abebachat.com/telegram/chat`. The domain `https://bot.abebachat.com` exposes only the process status/health server; Telegram messages use outbound long polling. Do not register a webhook against this health server. Run one poller for the token.

## Configure and start

From this project directory, copy the template only if `.env` does not already exist; otherwise merge the values into the existing file:

```sh
cp .env.production.example .env
```

Set `TELEGRAM_BOT_TOKEN` and a random `ABEBA_TELEGRAM_SERVICE_TOKEN`. Generate a service secret with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. Put the same values on the existing Django backend as `ABEBA_TELEGRAM_BOT_TOKEN` and `ABEBA_TELEGRAM_SERVICE_TOKEN`. The API URL and Mini App URL are prefilled for your domains. Keep `.env` out of Git.

Deploy/recreate the existing backend with its channel feature enabled and migration 0027 applied; the frontend deployment guide has the required backend settings. Make the frontend accessible over HTTPS before starting the bot.

```sh
docker-compose -f docker-compose.prod.yml config --quiet
docker-compose -f docker-compose.prod.yml build
docker-compose -f docker-compose.prod.yml run --rm --no-deps bot python -m abeba_bot --check
docker-compose -f docker-compose.prod.yml up -d
```

`--check` validates configuration without connecting to Telegram/backend. Real startup registers Telegram commands and the command menu, then polls. If this token already has a registered webhook, startup refuses to run: remove it intentionally first or use another bot token. The service never removes a webhook automatically.

The host listener is fixed to `127.0.0.1:18151`, mapping container port 8081. The service runs as a non-root user with a read-only filesystem, temporary `/tmp`, rotating logs and Docker's init process. No backend, database or Redis service is started by this Compose file.

## Host Nginx and HTTPS

Append `deploy/host-nginx.conf` to `/etc/nginx/sites-available/default`, outside the existing backend/frontend server blocks. Point the `bot.abebachat.com` DNS A record to this server. Only publish an AAAA record if IPv6 also reaches it.

```sh
sudo nginx -t && sudo systemctl reload nginx
```

Add HTTPS with your existing certificate tooling. For Certbot's Nginx plugin, after adding both project snippets:

```sh
sudo certbot --nginx -d web.abebachat.com -d bot.abebachat.com --redirect
sudo nginx -t && sudo systemctl reload nginx
```

The supplied snippets initially use HTTP so Certbot can set up certificates. Their proxy headers preserve the scheme once TLS is enabled.

## Verify and update

```sh
curl -f http://127.0.0.1:18151/healthz
curl -f https://bot.abebachat.com/healthz
docker-compose -f docker-compose.prod.yml logs --tail=100 bot
```

Both `/` and `/healthz` return a small status response. A 200 response confirms the process/event loop is running after initial Telegram setup; it does not guarantee continuous Telegram/backend reachability during retry loops.

In Telegram, send `/start`, use `/chat`, accept consent, ask a question, then use `/app` and confirm the same conversation appears. Test `/new`, `/history`, `/language` and `/stop`. The existing backend needs a working AI provider/model for actual answers.

After pulling later changes, run `docker-compose -f docker-compose.prod.yml up -d --build`. Stop any previous instance using the same token before starting this one. Current polling/delivery limitations remain as documented in README.md; this deployment configuration does not add a durable webhook queue.

Reference: [Telegram getUpdates/polling](https://core.telegram.org/bots/api#getupdates).

## Compose compatibility

The standalone Compose files use format `3.7` and omit the newer top-level `name` field so they work with legacy `docker-compose` (tested with 1.29.2) and modern Compose. The project name defaults to the project directory; use the same directory/project name for later updates. The startup commands omit the newer `--wait` option. Check `docker-compose -f docker-compose.prod.yml ps` and the `/healthz` endpoint after startup. Modern Compose may print an informational warning that `version` is obsolete.

## Deploy formatted answers, illustrations and Listen

Deploy the updated backend first; the new `POST /api/v1/channels/chat/audio/` route is required for Listen. No new migration or Nginx route is needed. On the backend, set `OPENAI_API_KEY` and enable the existing **Voice (TTS) configuration** in Django admin. The existing `ABEBA_TTS_MODEL`, `ABEBA_TTS_VOICE` and `ABEBA_TTS_MAX_CHARS` defaults are used if no admin configuration exists. Keep provider credentials exclusively on the backend.

Rebuild/recreate the existing backend web service using its normal production Compose deployment, then update the bot:

```sh
# From abeba-telegram-bot; merge these into the existing .env if desired:
# ABEBA_BOT_IMAGES_ENABLED=true
# ABEBA_BOT_AUDIO_ENABLED=true
docker-compose -f docker-compose.prod.yml up -d --build
```

Both flags default to true. To deploy only the formatted text and character art before the backend is updated, set `ABEBA_BOT_AUDIO_ENABLED=false` in the bot's existing `.env` and recreate the bot. The public domains, ports and Nginx configuration are unchanged. The speech request uses the current backend proxy path and finishes each generated part within the existing timeout budget.

In the real Telegram bot, verify `/start` shows the welcome character, use `/chat`, ask a question and check headings/bullets, then tap **Listen**. Confirm there is no Show illustration button or repeated Chat here/Open chat app button. `/listen` reads the latest answer in the active conversation. Try a long answer and confirm all numbered MP3 parts arrive, then repeat with an Amharic answer. Check `/withdraw` blocks audio and that disabling TTS produces a clear message while the text answer remains available. Provider/Telegram integration requires the real credentials; automated tests use mocks and do not send live messages or generate paid audio.


### Existing speech-provider retirement

The official OpenAI documentation states that `gpt-4o-mini-tts`, `tts-1` and `tts-1-hd` shut down on **2027-01-06**. This update deliberately retains the existing backend/admin speech configuration for the current deployment. Schedule migration of the speech-provider adapter before that date. OpenAI's documented replacement uses the Realtime API, so changing only the model string in the existing Speech API call is insufficient. The bot's authenticated MP3 endpoint can remain stable while its provider implementation changes.

Source, checked 2026-10-26: [OpenAI text-to-speech documentation](https://developers.openai.com/api/docs/guides/text-to-speech).


## Command menu and multilingual Learn update

Rebuild/recreate the bot with `docker-compose -f docker-compose.prod.yml up -d --build`. On startup it registers `/learn`, `/chat`, `/app` and the other commands and switches the default Telegram menu button to the command list. No new environment setting or backend deployment is needed for Learn; it reads the existing published API. The earlier Listen feature still requires its documented backend speech endpoint.

Verify `/learn` opens seven reading-language options, choosing a language lists topics, selecting a topic displays its full content, and changing language within the article keeps the same topic. Check the pagination and English fallback notice if a translation is unavailable. `/learn` must work before accepting AI consent and after `/stop` or `/withdraw`.

Verify `/chat` collects explicit AI consent when needed, `/app` opens the current conversation, and AI answers contain only the optional Listen control. Welcome messages and routine chat replies do not show the retired navigation or illustration controls. Older messages already sent in Telegram can retain their original keyboards; clicking the retired illustration button no longer sends an image. Restarting the bot updates the registered command menu without sending test messages.
