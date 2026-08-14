# Deployment

The deployment is deliberately ordinary: two containers, one Postgres, one
Redis, one model API. No cluster, no message broker, no infrastructure
language. That matters because it means the environment you demonstrate in is
the same one you run, and nothing has to be learned on the day.

## Local, no infrastructure

The proxy defaults to a SQLite file and an in-process event bus, so it runs
with one command and behaves identically — the same fail-closed paths, the same
audit chain, the same verdicts.

```bash
make setup
make api     # http://localhost:8080/docs
make web     # http://localhost:3000
```

## docker compose

```bash
cp .env.example .env
# set MEMBRANE_SIGNING_KEY and MEMBRANE_HASH_SALT to real values first
docker compose up --build
```

Postgres and Redis are on the internal network only; nothing but the API and
the dashboard is published.

## Google Cloud Platform

| Service | Role |
|---|---|
| Cloud Run (api) | The FastAPI container: L1–L4 pipeline plus the Telegram webhook route. Scales to zero |
| Cloud Run (web) | The Next.js container: live feed, replay, leaderboard |
| Vertex AI (Gemini) | L2 escalation, for ambiguous spans only |
| Cloud SQL for PostgreSQL | Contracts, policy, trust scores, the append-only audit table |
| Memorystore for Redis | Held-action queue and the dashboard event stream |
| Secret Manager | Signing key, hash salt, bot token. Never enters a model context |
| Cloud Scheduler | Triggers the InjectBench regression run every six hours |
| Cloud Build + Artifact Registry | Builds both containers on push and redeploys |

Every component runs inside a single customer-selected region. There is no
cross-region replication of audit data and no telemetry leaves the project.

```bash
PROJECT=your-project
REGION=us-central1

# Secrets first — never as plain environment variables.
printf '%s' "$(openssl rand -hex 32)" | \
  gcloud secrets create membrane-signing-key --data-file=- --project "$PROJECT"
printf '%s' "$(openssl rand -hex 32)" | \
  gcloud secrets create membrane-hash-salt --data-file=- --project "$PROJECT"

gcloud run deploy membrane-api \
  --source apps/api --region "$REGION" --project "$PROJECT" \
  --set-secrets "MEMBRANE_SIGNING_KEY=membrane-signing-key:latest,\
MEMBRANE_HASH_SALT=membrane-hash-salt:latest" \
  --set-env-vars "MEMBRANE_ENVIRONMENT=prod,\
MEMBRANE_DATABASE_URL=postgresql+asyncpg://membrane:PASS@/membrane?host=/cloudsql/$PROJECT:$REGION:membrane,\
MEMBRANE_REDIS_URL=redis://10.0.0.3:6379/0,\
MEMBRANE_VERTEX_PROJECT=$PROJECT,\
MEMBRANE_LIVE_PREVIEW_ENABLED=false" \
  --add-cloudsql-instances "$PROJECT:$REGION:membrane" \
  --min-instances 1

gcloud run deploy membrane-web \
  --source apps/web --region "$REGION" --project "$PROJECT" \
  --set-env-vars "NEXT_PUBLIC_MEMBRANE_API=https://membrane-api-xxxx.run.app"
```

`--min-instances 1` is worth the cost: a cold start on the *first* request of a
session is a cold start on the security control.

## Before production

`GET /readyz` reports configuration problems, and the service logs them at
startup. The checks that matter:

- **`MEMBRANE_SIGNING_KEY` and `MEMBRANE_HASH_SALT` are not the dev defaults.**
  The signing key authorises contracts and Telegram callbacks; the salt makes
  audit hashes unlinkable across deployments. Rotating the key invalidates
  every outstanding contract — that is the intended emergency behaviour.
  Rotating the salt makes historical hashes unverifiable, so rotate it only
  deliberately.
- **`MEMBRANE_LIVE_PREVIEW_ENABLED=false`.** Previews stream inspected text to
  the dashboard. They are never persisted, but there is no reason to emit them
  in production.
- **`MEMBRANE_REPLAY_RETENTION_ENABLED` stays off** unless someone has decided
  they want a bounded content store and written down why.
- **Configure the L2 model tier**, especially for multilingual content. Without
  it, ambiguous spans quarantine — safe, but it costs utility on exactly the
  content the deterministic tier is least sure about.
- **Restrict `MEMBRANE_CORS_ORIGINS`** to the dashboard's real origin.
- **Force egress through the proxy.** Membrane is a boundary control, not a
  sandbox. An agent that can reach the internet directly can bypass it. Network
  policy is what makes the integration structural rather than voluntary.

## Telegram

```bash
# 1. Create a bot with @BotFather, then find your chat id via @userinfobot.
export MEMBRANE_TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
export MEMBRANE_TELEGRAM_CHAT_ID=987654321

# 2. Point the bot at the deployment.
curl -X POST "https://your-api/v1/telegram/register?base_url=https://your-api"
```

The webhook verifies Telegram's secret-token header, verifies the HMAC on the
callback payload, and rejects any callback from an unexpected chat. It always
answers 200, so a rejected payload is not retried back at you. Cards expire
after `MEMBRANE_APPROVAL_TIMEOUT_SECONDS` and expiry is denial.

Without a bot configured, held actions are resolved from the dashboard instead.
That path can be switched off with `MEMBRANE_DASHBOARD_APPROVALS_ENABLED=false`
if the bot should be the only route.

## Custom capability policy

```yaml
# policy.yaml — MEMBRANE_POLICY_FILE=./policy.yaml
capabilities:
  crm.export:
    sensitivity: irreversible
    description: Export the full customer table
tools:
  - match: "^salesforce_bulk_export$"
    capability: crm.export
always_human:
  - crm.export
```

File rules take precedence over the built-ins, which stay in place, so adding a
tool does not mean restating the catalogue. Any tool matching no rule is held,
not passed — an unknown tool has unknown reach, and that is the case that must
not be guessed at.

## Operating notes

- **Scaling.** The API is stateless apart from the in-memory taint graphs,
  which are per session and bounded by count and TTL. With Redis configured the
  event feed and the approval queue are shared across instances, and the
  approval wait falls back to a database poll so a card raised on one instance
  can be resolved on another. Session affinity is not required, though a
  session's taint graph is rebuilt if its requests land on a different instance
  — route by session where it is cheap to do so.
- **Backups.** The audit table is the artefact worth protecting. It is
  append-only and hash-chained; back it up like any compliance record.
- **Watch.** Hold rate, block rate, p95 ingest latency, escalation failure
  count, and circuit-breaker trips. A rising escalation failure count means
  spans are quarantining on the fail-closed path, which shows up as a
  utility problem rather than a security one.
