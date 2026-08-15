# Deploying Membrane

One host, four containers behind Caddy, automatic HTTPS. From a bare Ubuntu
box to a live URL is about ten minutes, most of it the image build.

Everything below has been exercised end to end against the real stack —
Caddy terminating TLS, PostgreSQL, Redis, both images — not just linted. What
was verified, and what was not, is listed at the bottom.

---

## 1. What you need

| | |
|---|---|
| A host | 2 vCPU / 2 GB RAM is comfortable. 1 GB works if you build the images elsewhere. |
| Docker | Engine 24+ with the Compose plugin. |
| A domain | An `A` record pointing at the host's public IP, already resolving. |
| Ports | 80 and 443 open inbound. Caddy needs 80 to complete the ACME challenge. |

Nothing else. No object storage, no managed database, no cloud account.

---

## 2. Get the code onto the host

```bash
git clone <your-repo> membrane && cd membrane
```

---

## 3. Write `.env.prod`

Four values are required and have **no defaults** — the stack refuses to start
rather than boot with a development key.

```bash
cat > .env.prod <<EOF
MEMBRANE_DOMAIN=membrane.example.com
POSTGRES_PASSWORD=$(openssl rand -hex 24)
MEMBRANE_SIGNING_KEY=$(openssl rand -hex 32)
MEMBRANE_HASH_SALT=$(openssl rand -hex 32)
EOF
chmod 600 .env.prod
```

| Variable | What it does | If you lose it |
|---|---|---|
| `MEMBRANE_DOMAIN` | The public hostname. Drives the certificate, CORS, and the API URL baked into the dashboard bundle. | — |
| `POSTGRES_PASSWORD` | Database password. | The database is unreadable. |
| `MEMBRANE_SIGNING_KEY` | HMAC-SHA256 key for intent contracts and Telegram callback data. | Every issued contract stops validating. Rotating it invalidates live sessions. |
| `MEMBRANE_HASH_SALT` | Salt for span hashes in the audit trail. | Hashes from before the change no longer match. **Never rotate this on a live audit trail** — it breaks the chain's comparability. |

### Optional: Telegram approvals

Without these, held actions can only be resolved in the dashboard. With them,
every held action is also pushed to a phone with Approve / Deny buttons.

```bash
MEMBRANE_TELEGRAM_BOT_TOKEN=123456:AA...
MEMBRANE_TELEGRAM_CHAT_ID=-1001234567890
```

Get them by messaging [@BotFather](https://t.me/BotFather) → `/newbot`, then
sending any message to your bot and reading the chat id from
`https://api.telegram.org/bot<TOKEN>/getUpdates`.

After the stack is up, point Telegram at the webhook once:

```bash
curl -X POST "https://api.telegram.org/bot$MEMBRANE_TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=https://$MEMBRANE_DOMAIN/v1/telegram/webhook" \
  -d "secret_token=$MEMBRANE_SIGNING_KEY"
```

The full list of all 33 settings is in [`docs/CONFIGURATION.md`](../docs/CONFIGURATION.md).
The four above are the only ones you must set.

---

## 4. Bring it up

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --build
```

The first build takes a few minutes. Then:

```bash
docker compose --env-file .env.prod \
  -f docker-compose.yml -f docker-compose.prod.yml ps
```

You should see exactly one service publishing ports:

```
caddy   Up   0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp, 0.0.0.0:443->443/udp
api     Up (healthy)   8080/tcp
web     Up             3000/tcp
db      Up (healthy)   5432/tcp
redis   Up (healthy)   6379/tcp
```

`api` and `web` are reachable only on the compose network. Caddy is the only
thing exposed to the internet.

---

## 5. Verify

```bash
D=membrane.example.com

curl -s https://$D/readyz | jq
curl -s -o /dev/null -w '%{http_code}\n' https://$D/
curl -s -o /dev/null -w '%{http_code}\n' https://$D/dashboard
curl -s https://$D/v1/stats | jq '.events, .latency_ms.p95'
```

`/readyz` should return `config_warnings: []`. Anything listed there is a
setting that is unsafe for production, and the message says what to do.

Then open `https://$D` in a browser: the landing page, **Enter Membrane**, the
transition, and the console.

To prove the pipeline rather than just the plumbing, click **Run live attack**
on the dashboard. It runs the confused-deputy scenario against the live proxy
and you should watch the event stream fill and the held-action badge increment.

---

## 6. Operating it

```bash
# alias, so you stop typing the three flags
alias mc='docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml'

mc logs -f api          # follow the proxy
mc logs -f caddy        # follow the edge, incl. certificate issuance
mc restart api          # safe: state is in Postgres
mc up -d --build        # deploy a new revision
mc down                 # stop (volumes survive)
mc down -v              # stop and destroy the database. There is no undo.
```

### Backups

Everything that matters is in one volume.

```bash
mc exec -T db pg_dump -U membrane membrane | gzip > membrane-$(date +%F).sql.gz
```

The certificate lives in the `caddy-data` volume. Losing it is not fatal —
Caddy re-issues — but Let's Encrypt rate limits apply, so keep it.

---

## 7. Things that will bite you

**The dashboard talks to the API at a URL baked in at build time.**
`NEXT_PUBLIC_MEMBRANE_API` is inlined into the client bundle by Next.js during
`docker build`, not read at runtime. If you change `MEMBRANE_DOMAIN`, you must
rebuild the `web` image — restarting it is not enough.

**The build needs outbound internet.** `next/font/google` fetches Geist and
Geist Mono at build time. On an air-gapped host, build the image elsewhere and
push it to a registry.

**DNS must resolve before the first start.** Caddy requests the certificate
immediately. If the domain does not point at the host yet, issuance fails and
you will get a browser warning until it retries. Fix DNS, then
`mc restart caddy`.

**Do not rotate `MEMBRANE_HASH_SALT` on a live system.** Span hashes before and
after would no longer be comparable, which quietly breaks the audit trail's
whole point.

**Nothing here is authenticated.** The console and the API are open to anyone
who reaches the domain. That is fine for a demo and wrong for anything real —
put the site behind Caddy `basic_auth`, an identity-aware proxy, or a private
network before pointing it at anything that matters.

---

## 8. What was actually verified

Run against the production compose overlay — Caddy with real TLS termination,
PostgreSQL, Redis, both container images — not against the development stack.

| | |
|---|---|
| Test suite | 92 passing (83 API, 9 InjectBench) |
| Caddyfile | `caddy validate` → `Valid configuration` |
| Compose merge | Only `caddy` publishes ports; `api` and `web` are internal |
| HTTP → HTTPS | port 80 returns `308` to `https://` |
| Dashboard routes | `/`, `/dashboard`, `/approvals`, `/sessions`, `/playground`, `/mcp`, `/trust`, `/leaderboard`, `/icon.svg` → all `200` |
| API routes | `/healthz`, `/readyz`, `/v1/stats`, `/v1/capabilities`, `/openapi.json`, `/docs` → all `200` |
| Security headers | HSTS, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy` present; `Server` removed |
| Client bundle | The public HTTPS URL is baked in; **no** stale `localhost:8080` in any chunk |
| Attack, end to end | Injected instruction stripped at ingest and never reached the agent; the follow-on payment held because `account` traced to untrusted retrieved content |
| Audit chain | `ok=true`, 6 events, no broken link |
| Server-sent events | Events arrived through Caddy live and unbuffered |
| Restart | `api` and `web` restarted; all data survived; no errors in the log |
| Startup | `env=prod · db=postgres · redis=True`, `config_warnings: []` |

**Not verified here:** a real Let's Encrypt certificate (the local run used
Caddy's internal CA for `localhost` — the ACME path needs a public domain), and
the Telegram round trip against the real Bot API, which needs a bot token and a
publicly reachable webhook. The Telegram loop is covered by 15 tests against a
stand-in server, and the timeout-to-denial path was verified separately on
PostgreSQL.
