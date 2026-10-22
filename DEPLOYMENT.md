# Deploy bot.abebachat.com on the existing server

The bot connects to `https://dashboard.abebachat.com/api/v1/`. Its Open chat app button opens `https://web.abebachat.com/telegram/chat`. The domain `https://bot.abebachat.com` exposes only the process status/health server; Telegram messages use outbound long polling. Do not register a webhook against this health server. Run one poller for the token.

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

`--check` validates configuration without connecting to Telegram/backend. Real startup registers the Telegram commands and Open chat app menu, then polls. If this token already has a registered webhook, startup refuses to run: remove it intentionally first or use another bot token. The service never removes a webhook automatically.

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

In Telegram, send `/start`, choose Chat here, accept consent, ask a question, then open the chat app and confirm the same conversation appears. Test `/new`, `/history`, `/language` and `/stop`. The existing backend needs a working AI provider/model for actual answers.

After pulling later changes, run `docker-compose -f docker-compose.prod.yml up -d --build`. Stop any previous instance using the same token before starting this one. Current polling/delivery limitations remain as documented in README.md; this deployment configuration does not add a durable webhook queue.

Reference: [Telegram getUpdates/polling](https://core.telegram.org/bots/api#getupdates).

## Compose compatibility

The standalone Compose files use format `3.7` and omit the newer top-level `name` field so they work with legacy `docker-compose` (tested with 1.29.2) and modern Compose. The project name defaults to the project directory; use the same directory/project name for later updates. The startup commands omit the newer `--wait` option. Check `docker-compose -f docker-compose.prod.yml ps` and the `/healthz` endpoint after startup. Modern Compose may print an informational warning that `version` is obsolete.
