# Configuration reference

Every setting Membrane reads, what it does, and what it must be set to before
this faces anything real.

All 33 settings are environment variables prefixed **`MEMBRANE_`**. They can
also live in a `.env` file at the repo root or in `apps/api/`. Two of them are
secrets you **must** change; everything else has a working default.

`GET /readyz` reports the ones that are wrong, and the service logs them at
startup.

---

## The short version

**To run locally — nothing.** It defaults to a SQLite file and an in-process
event bus and behaves identically to production, same fail-closed paths, same
audit chain.

**To run for real — these four:**

```bash
MEMBRANE_ENVIRONMENT=prod
MEMBRANE_SIGNING_KEY=$(openssl rand -hex 32)
MEMBRANE_HASH_SALT=$(openssl rand -hex 32)
MEMBRANE_LIVE_PREVIEW_ENABLED=false
```

**To add the Telegram human loop — two more:**

```bash
MEMBRANE_TELEGRAM_BOT_TOKEN=123456789:AAF...
MEMBRANE_TELEGRAM_CHAT_ID=987654321
```

**To add the model escalation tier — one of:**

```bash
MEMBRANE_GEMINI_API_KEY=AIza...              # simplest
# or, on GCP with workload identity:
MEMBRANE_VERTEX_PROJECT=my-project
```

---

## 1 · Secrets — you must change these

| Variable | Default | What it does |
|---|---|---|
| `MEMBRANE_SIGNING_KEY` | `dev-insecure-signing-key-change-me` | HMAC key for intent contracts, Telegram callbacks and the webhook secret token |
| `MEMBRANE_HASH_SALT` | `dev-insecure-hash-salt-change-me` | Salts every span hash in the audit trail |

```bash
openssl rand -hex 32     # generate each one separately
```

**Rotating the signing key** invalidates every outstanding contract immediately.
That is the intended emergency behaviour — it is your kill switch.

**Rotating the hash salt** makes all historical span hashes unverifiable, because
`verify-span` can no longer reproduce them. Rotate it deliberately, not
casually. On the other hand it also makes historical hashes unlinkable, which is
the privacy behaviour you want if a database is ever exposed.

`/readyz` refuses to report healthy config while either is at its default and
`MEMBRANE_ENVIRONMENT=prod`.

---

## 2 · Storage

| Variable | Default | Notes |
|---|---|---|
| `MEMBRANE_DATABASE_URL` | `sqlite+aiosqlite:///./membrane.db` | SQLite for local, PostgreSQL for real |
| `MEMBRANE_REDIS_URL` | *(unset)* | Optional. Without it the event bus is in-process and single-instance |

The DSN is normalised for you — `postgres://`, `postgresql://` and `sqlite:///`
are all rewritten to their async drivers, so you can paste whatever your
provider hands you.

```bash
# PostgreSQL
MEMBRANE_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/membrane

# Cloud SQL over a unix socket
MEMBRANE_DATABASE_URL=postgresql+asyncpg://user:pass@/membrane?host=/cloudsql/PROJECT:REGION:INSTANCE

# Redis
MEMBRANE_REDIS_URL=redis://10.0.0.3:6379/0
```

**When do you need Redis?** Only when running more than one instance. It shares
the live event feed and the held-action queue across them. Without it each
instance has its own feed, and an approval raised on instance A can still be
resolved on instance B — the waiter falls back to a database poll — but the
dashboard would only see events from whichever instance it is connected to.

---

## 3 · The Telegram human loop

| Variable | Default | Notes |
|---|---|---|
| `MEMBRANE_TELEGRAM_BOT_TOKEN` | *(unset)* | From @BotFather |
| `MEMBRANE_TELEGRAM_CHAT_ID` | *(unset)* | Where cards are sent; also the only chat callbacks are accepted from |
| `MEMBRANE_TELEGRAM_API_BASE` | `https://api.telegram.org` | Override only for testing |
| `MEMBRANE_APPROVAL_TIMEOUT_SECONDS` | `60` | Unanswered cards expire to **denial** |
| `MEMBRANE_DASHBOARD_APPROVALS_ENABLED` | `true` | Set `false` to make the bot the only way to approve |

**Both token and chat id must be set** — with either missing, `telegram.configured()`
is false and cards resolve in the dashboard instead. That is a supported mode,
not a broken one.

### Setting up the bot — five minutes

**1 · Create it.** Message [@BotFather](https://t.me/BotFather) → `/newbot` →
choose a name and a username. He replies with a token like
`123456789:AAF-abc123...`. That token is a credential; treat it like a password.

**2 · Get your chat id.** Message [@userinfobot](https://t.me/userinfobot) and it
replies with your numeric id. For a group, add the bot to the group and use the
group's id (negative, e.g. `-1001234567890`).

**3 · Say hello.** Send your bot any message once. Telegram will not let a bot
open a conversation with you first.

**4 · Configure and restart:**

```bash
export MEMBRANE_TELEGRAM_BOT_TOKEN=123456789:AAF-abc123...
export MEMBRANE_TELEGRAM_CHAT_ID=987654321
```

**5 · Register the webhook.** Telegram must be able to reach your service over
**public HTTPS** — this is the one step that does not work on `localhost`.

```bash
curl -X POST "https://your-host/v1/telegram/register?base_url=https://your-host"
```

For local development, tunnel it:

```bash
cloudflared tunnel --url http://localhost:8080     # or: ngrok http 8080
curl -X POST "http://localhost:8080/v1/telegram/register?base_url=https://your-tunnel.example"
```

The register call sets a secret token derived from your signing key, which
Membrane then checks on every inbound webhook.

**6 · Verify:**

```bash
curl localhost:8080/readyz | grep telegram      # telegram_configured: true
cd docs/demo && python demo.py run              # a card should hit your phone
```

### What the card looks like

```
🛑 Membrane held an action

What — payment.transfer · capability payment.transfer · irreversible
  · iban: IBAN in DE ending 3000 · a3f91c2b ⚠️ tainted
  · amount: number 4800.00

Why — argument 'iban' traces to untrusted content
      (retrieved · file:inbox/invoice-2291.pdf · atom:iban)

Provenance
  · iban ← retrieved · file:inbox/invoice-2291.pdf · atom:iban

Diff against the signed intent
  · authorised: docs.read, payment.transfer
  · attempted: payment.transfer
  · inside envelope: yes

No decision within 60s is treated as DENY.

          [ ✅ Approve once ]   [ ⛔ Deny ]
```

The actual IBAN never appears. Neither does the content the agent was reading.

### Security properties, all tested

- Callbacks are HMAC-signed and bound to one action id and one decision
- Callbacks from any chat other than the configured one are rejected
- Telegram's `X-Telegram-Bot-Api-Secret-Token` header is verified
- The first decision wins; a second tap cannot change it
- An expired card cannot be approved, only recorded as denied
- **If Telegram is unreachable, the action stays held and times out to denial** —
  an outage never becomes an approval
- The webhook always answers `200` so a rejected payload is not retried at you

---

## 4 · Model escalation (L2 tier two)

| Variable | Default | Notes |
|---|---|---|
| `MEMBRANE_GEMINI_API_KEY` | *(unset)* | Simplest path; takes precedence over Vertex |
| `MEMBRANE_VERTEX_PROJECT` | *(unset)* | GCP project, uses application default credentials |
| `MEMBRANE_VERTEX_LOCATION` | `us-central1` | |
| `MEMBRANE_VERTEX_MODEL` | `gemini-2.0-flash` | |
| `MEMBRANE_VERTEX_TIMEOUT_SECONDS` | `3.0` | On timeout the span quarantines |
| `MEMBRANE_ESCALATION_ENABLED` | `true` | Set `false` to force the deterministic tier only |

**This is optional and the system is secure without it.** Unconfigured, spans in
the ambiguous band quarantine instead of escalating — utility degrades, security
does not.

**Configure it if:** you handle multilingual content (the deterministic lexicon
is English-centric), or your false-positive rate on the ambiguous band matters
more than the cost of a model call.

Only spans scoring between the ambiguous and quarantine thresholds ever reach it,
so cost tracks the ambiguous minority, not your traffic.

---

## 5 · Policy tuning

| Variable | Default | Notes |
|---|---|---|
| `MEMBRANE_SEPARATOR_QUARANTINE_THRESHOLD` | `0.60` | At or above, quarantine outright |
| `MEMBRANE_SEPARATOR_AMBIGUOUS_THRESHOLD` | `0.35` | Between the two, escalate then fail closed |
| `MEMBRANE_CONTRACT_TTL_SECONDS` | `900` | How long an intent contract stays valid |
| `MEMBRANE_MAX_DECODE_DEPTH` | `4` | Recursive decode limit; bounds decode bombs |
| `MEMBRANE_MAX_CONTENT_BYTES` | `4194304` | 4 MB per ingest call |
| `MEMBRANE_POLICY_FILE` | *(unset)* | Path to a YAML file adding capabilities and tool mappings |

**Lower the quarantine threshold** to catch more and quarantine more. **Raise it**
to reduce false positives at the cost of coverage. Measure the change with
`python -m injectbench` before shipping it — both numbers move together and the
benchmark reports both.

### Breaker and trust

| Variable | Default | Notes |
|---|---|---|
| `MEMBRANE_BREAKER_HOLD_THRESHOLD` | `5` | Holds within the window before tripping |
| `MEMBRANE_BREAKER_WINDOW_SECONDS` | `120` | |
| `MEMBRANE_BREAKER_COOLDOWN_SECONDS` | `300` | How long privileged capabilities stay quarantined |
| `MEMBRANE_TRUST_INITIAL` | `0.70` | Score for a source nobody has seen |
| `MEMBRANE_TRUST_INCIDENT_MULTIPLIER` | `0.35` | Score is multiplied by this on an incident |
| `MEMBRANE_TRUST_RECOVERY_PER_HOUR` | `0.01` | Slow recovery with clean history |

---

## 6 · Privacy

| Variable | Default | Production |
|---|---|---|
| `MEMBRANE_LIVE_PREVIEW_ENABLED` | `true` | **Set `false`** |
| `MEMBRANE_LIVE_PREVIEW_CHARS` | `160` | |
| `MEMBRANE_REPLAY_RETENTION_ENABLED` | `false` | **Leave `false`** |
| `MEMBRANE_REPLAY_RETENTION_HOURS` | `24` | Only relevant if retention is on |

**Live previews** stream a truncated excerpt of inspected content to the
dashboard over SSE. They come from memory and are never written to the audit
table — but there is no reason to emit them in production, and `/readyz` warns
about it when `ENVIRONMENT=prod`.

**Replay retention** is the one setting that makes Membrane store text. It is off
by default, bounded, documented in the policy file, and expires automatically.
Turn it on only if someone has decided they want a bounded content store and
written down why.

---

## 7 · Service and network

| Variable | Default | Notes |
|---|---|---|
| `MEMBRANE_ENVIRONMENT` | `dev` | Set `prod` to enable the config warnings |
| `MEMBRANE_SERVICE_NAME` | `membrane-api` | |
| `MEMBRANE_CORS_ORIGINS` | `http://localhost:3000,...` | Comma-separated; restrict to your dashboard origin |
| `MEMBRANE_CORS_ORIGIN_REGEX` | unset | Matched **in addition** to the list. Only needed where the platform mints a hostname per deployment, so an exhaustive list is impossible — every Vercel preview is its own origin. Anchor both ends: `^https://membrane-[a-z0-9-]+\.vercel\.app$` |
| `PORT` | `8080` | Read by the container entrypoint, not by settings |

### Dashboard

The dashboard takes one variable, and it is **not** prefixed:

| Variable | Notes |
|---|---|
| `NEXT_PUBLIC_MEMBRANE_API` | The proxy URL **as the browser sees it** |

It is inlined into the client bundle at build time, so it must be set when you
build the image, not only when you run it. The Dockerfile takes it as a build
arg.

---

## 8 · Production checklist

```bash
MEMBRANE_ENVIRONMENT=prod
MEMBRANE_SIGNING_KEY=<openssl rand -hex 32>          # from a secret manager
MEMBRANE_HASH_SALT=<openssl rand -hex 32>            # from a secret manager
MEMBRANE_DATABASE_URL=postgresql+asyncpg://...
MEMBRANE_REDIS_URL=redis://...                       # if more than one instance
MEMBRANE_LIVE_PREVIEW_ENABLED=false
MEMBRANE_REPLAY_RETENTION_ENABLED=false
MEMBRANE_CORS_ORIGINS=https://console.yourcompany.com
MEMBRANE_TELEGRAM_BOT_TOKEN=...                      # optional but recommended
MEMBRANE_TELEGRAM_CHAT_ID=...
MEMBRANE_GEMINI_API_KEY=...                          # or MEMBRANE_VERTEX_PROJECT
```

Then, in order:

- [ ] Secrets come from a secret manager, not from the environment of a shell script
- [ ] `curl https://your-host/readyz` returns `ok: true` with an empty `config_warnings`
- [ ] Telegram webhook registered and one test card received
- [ ] **Network policy forces agent egress through the proxy.** Membrane is a
      boundary control, not a sandbox — an agent that can reach the internet
      directly bypasses it entirely. This is the single most important item here.
- [ ] Database backed up like the compliance record it is
- [ ] Alerting on: hold rate, block rate, p95 ingest latency, escalation failure
      count, circuit-breaker trips
- [ ] `python -m injectbench --strict` green against the deployed configuration

---

## 9 · What has and has not been verified

Straight answer, because deploying on an assumption is how outages happen.

### Verified on real infrastructure

| | How |
|---|---|
| Proxy, all four layers, egress, MCP scanner | 83 automated tests plus live end-to-end runs |
| **PostgreSQL** | The full stack runs on Postgres 16 under `docker compose`. Two Postgres-only bugs were found this way and fixed — see below |
| **Redis** | Attached and mirroring the event bus: `event bus attached to redis at redis://redis:6379/0` |
| **Both container images** | Built and running. `api` reports healthy via its `HEALTHCHECK` |
| **Timeout to denial on Postgres** | A card left unanswered for 60 s resolved to `expired` / `timeout`, with zero errors in the log |
| Telegram loop | 15 tests against a stand-in Bot API: delivery, signed callbacks, chat binding, webhook secret, replay refusal, first-decision-wins, delivery failure, timeout, and the blocking wait |
| Dashboard | Built into its image; all 7 routes serve from the container |
| Benchmark | 42/42 stopped, 1.33 % FPR, p95 21 ms — re-run against the Postgres stack |
| SQLite path | Every test and local demo |

### Bugs that only a real Postgres would have revealed

Both were invisible on SQLite and both were security-relevant:

1. **Naive timestamp columns.** Every datetime in the codebase is tz-aware UTC,
   but the columns were `TIMESTAMP WITHOUT TIME ZONE`. SQLite stores whatever it
   is handed; Postgres rejects the comparison. This took out the janitor that
   expires held actions — meaning **"silence is denial" would have silently
   stopped working in production**. Fixed by making every timestamp column
   `timezone=True`.
2. **Foreign key inserted out of order.** `intent_contracts.session_id`
   references `sessions.id`, but the contract row was written before the session
   row existed. SQLite does not enforce foreign keys by default, so this passed
   for the entire project. Postgres refused it and `/v1/intent` returned 500.
   Fixed by touching the session first.

### Still unproven

| | What would close it |
|---|---|
| **Real Telegram** | A bot token and a public HTTPS URL. The protocol is exercised against a stand-in that records exactly what we send, so what remains untested is Telegram's own behaviour, not ours |
| **Vertex AI / Gemini** | An API key or GCP credentials. Unconfigured, ambiguous spans quarantine — safe, and that path *is* tested |
| **Cloud Run** | A GCP project. The images build and run under compose, which is the same artefact |

Nothing else is believed broken; those three are simply unproven.
