# Deploying Membrane on Vercel + Render

The single-host Docker path is in [`deploy/README.md`](../deploy/README.md).
This is the split: **the dashboard on Vercel, the proxy and its state on
Render.** It costs nothing to start, needs no server to administer, and gives
you two public URLs in about fifteen minutes.

---

## 1. Why it splits this way

The dashboard holds no state. It is a window onto the proxy's audit trail — it
fetches, it renders, it stores nothing. That makes it a perfect fit for a CDN
edge, and Vercel is the shortest path to one for a Next.js app.

The proxy is the opposite. It must be long-lived, it owns a PostgreSQL audit
chain, and it holds open server-sent event streams. That is a container on
Render, next to a managed database.

The one consequence you have to design around: **they are on different
origins.** Every request the browser makes is now cross-origin, so CORS is no
longer a formality — get it wrong and the console loads but shows nothing.

```
  browser
     │
     ├── https://membrane.vercel.app        Vercel    the dashboard
     │
     └── https://membrane-api.onrender.com  Render    the proxy
                                              ├── PostgreSQL   audit trail
                                              └── Key Value    event bus
```

---

## 2. Render — the proxy

The repository has a [`render.yaml`](../render.yaml) blueprint that declares
all three services, so you do not click through forms.

1. Push the repository to GitHub.
2. Render dashboard → **New** → **Blueprint** → select the repository.
3. Render reads `render.yaml` and shows you the plan: one web service, one
   PostgreSQL, one Key Value. Apply it.

It generates `MEMBRANE_SIGNING_KEY` and `MEMBRANE_HASH_SALT` for you, wires
the database and Redis URLs automatically, and builds from
`apps/api/Dockerfile`.

Two variables are marked `sync: false` and are left blank on purpose — you
cannot know them until Vercel has given you a URL. Come back to them in step 4.

The service is live when `https://<your-service>.onrender.com/healthz` returns
`{"ok": true}`.

### Plans

| | Free | Recommended |
|---|---|---|
| Web service | spins down after 15 min idle, ~50 s cold start | **Starter**, $7/mo, always on |
| PostgreSQL | expires after 30 days | **Basic 256 MB**, $6/mo |
| Key Value | 25 MB, fine | free is fine |

**The free web service plan will hurt you in a live demo.** Spin-down kills
open event streams, so the console shows *Offline* and the feed stops until
someone reloads; and the first request after idle waits out a cold start while
a judge watches. If this is going in front of an audience, pay the $7.

Keep the database in the **same region** as the service, or the internal
connection string will not resolve.

---

## 3. Vercel — the dashboard

1. Vercel → **Add New** → **Project** → import the same repository.
2. Set **Root Directory** to `apps/web`. This is the only setting that is not
   auto-detected, and getting it wrong is the most common failure — Vercel
   will otherwise look for a Next.js app at the repository root and find none.
3. Add an environment variable, for **Production, Preview and Development**:

   ```
   NEXT_PUBLIC_MEMBRANE_API = https://membrane-api.onrender.com
   ```

   No trailing slash.

4. Deploy.

---

## 4. Wire the two together

Go back to Render → `membrane-api` → **Environment**, and set:

```
MEMBRANE_CORS_ORIGINS = https://membrane.vercel.app
```

Use your real production URL, no trailing slash. If you want Vercel preview
deployments to work too — every pull request gets its own hostname, so they
can never be listed exhaustively — also set:

```
MEMBRANE_CORS_ORIGIN_REGEX = ^https://membrane-[a-z0-9-]+\.vercel\.app$
```

The pattern is matched **in addition to** the list, and it is anchored at both
ends, so `https://membrane-x.vercel.app.attacker.com` does not match.

Save. Render restarts the service.

---

## 5. Verify

```bash
API=https://membrane-api.onrender.com
APP=https://membrane.vercel.app

# the proxy is up and has no unsafe configuration
curl -s $API/readyz | jq

# the browser will be allowed to talk to it
curl -si -X OPTIONS $API/v1/stats \
  -H "Origin: $APP" -H "Access-Control-Request-Method: GET" \
  | grep -i access-control-allow-origin

# the dashboard is up
curl -s -o /dev/null -w '%{http_code}\n' $APP
```

`/readyz` should show `config_warnings: []`. The preflight must echo your
Vercel origin back — if that header is missing, CORS is wrong and the console
will render an empty shell.

Then open the app: landing page → **Enter Membrane** → the transition → the
console. Click **Run live attack**. If the event feed fills and the held-action
badge increments, every layer of this is working: Vercel is serving, the
browser is reaching Render cross-origin, the event stream is open, and the
audit chain is being written to PostgreSQL.

---

## 6. Things that will bite you

**`NEXT_PUBLIC_MEMBRANE_API` is baked in at build time, not read at runtime.**
Next.js inlines it into the client bundle during `next build`. Changing it in
the Vercel dashboard does nothing until you **redeploy**. If you forget to set
it at all, the bundle keeps its development fallback and every request goes to
`http://localhost:8080` — the console will show *Could not reach the proxy at
http://localhost:8080*, which is the tell.

**A blank page with no data is almost always CORS.** The dashboard renders
before its first fetch resolves, so a blocked request looks like an empty
console rather than an error. Check the browser console for
`No 'Access-Control-Allow-Origin' header`, then fix `MEMBRANE_CORS_ORIGINS`.

**Render's free tier drops server-sent event streams on spin-down.** The live
feed is the demo. See the plans table above.

**Render prints a libpq connection string.** `postgres://…?sslmode=require`
selects a synchronous driver we do not install, and `sslmode` is a libpq
spelling that asyncpg rejects outright. The proxy rewrites both on the way in,
so you can paste Render's string unedited — including the external one.

**Never rotate `MEMBRANE_HASH_SALT` on a live audit trail.** Span hashes from
before and after would no longer be comparable, which quietly destroys the
point of the chain. Rotating `MEMBRANE_SIGNING_KEY` is survivable but
invalidates every in-flight intent contract.

**Nothing here is authenticated.** Both URLs are open to anyone who finds them.
That is fine for a hackathon demo and wrong for anything real — put Vercel
behind password protection and the API behind a shared secret before it touches
anything that matters.

---

## 7. Optional: Telegram approvals

Without a bot, held actions are resolved in the dashboard. With one, every held
action is also pushed to a phone with Approve / Deny buttons.

On Render, set `MEMBRANE_TELEGRAM_BOT_TOKEN` and `MEMBRANE_TELEGRAM_CHAT_ID`,
then register the webhook once:

```bash
curl -X POST "https://api.telegram.org/bot$TOKEN/setWebhook" \
  -d "url=https://membrane-api.onrender.com/v1/telegram/webhook" \
  -d "secret_token=$MEMBRANE_SIGNING_KEY"
```

The webhook must be publicly reachable over HTTPS, which Render already is.
Note that on the free plan a sleeping service will miss the callback.

---

## 8. What was verified

Checked against the real code and the real container image before writing this.

| | |
|---|---|
| Test suite | 83 API tests passing after the CORS and DSN changes |
| Driver availability | `asyncpg` and `redis` moved into `requirements.txt`, so a non-Docker build works too; confirmed present in the rebuilt image |
| DSN rewriting | Render, Neon and Supabase connection strings all normalise correctly, including `sslmode=require` → asyncpg's `ssl`, and `channel_binding` stripped |
| CORS — production origin | preflight allowed, header echoed |
| CORS — preview regex | `membrane-nulldeity-git-main-abc123.vercel.app` allowed |
| CORS — unrelated origin | blocked |
| CORS — lookalike (`…vercel.app.evil.com`) | blocked; the pattern is anchored |
| Cross-origin `GET /v1/stats` | 200 with `Access-Control-Allow-Origin` |
| Cross-origin SSE | 200, `text/event-stream`, allow-origin present, `no-transform` |
| Dashboard build | built against `https://membrane-api.onrender.com`; the URL is inlined into the client bundle |

**Not verified:** an actual deployment to Vercel or Render, which needs your
accounts. Everything those platforms do that could break — the connection
string format, the cross-origin request, the event stream, the build-time
inlining — was reproduced locally against the same image that will run there.
