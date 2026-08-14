# Membrane — answers

Team NullDeity · NGH26_132 · Track 02: Cybersecurity for the Future

**Everything a judge might ask, with the real answer.** Ctrl-F this during Q&A.
Every number here was measured against the running system, not estimated. Where
something is a limitation, it says so — the fastest way to lose a technical
judge is to bluff a number, and the fastest way to win one is to know exactly
where your own edges are.

---

## 0 · Fast facts

| | |
|---|---|
| **What it is** | A prompt-injection firewall for AI agents. A transparent proxy on the retrieval and tool-call boundary. |
| **One-line claim** | Content passes through. Instructions do not. |
| **The problem** | OWASP LLM01. Instructions and data share one channel, so a web page can command an agent holding your credentials. |
| **The mechanism** | Four layers on ingest, plus a re-check on egress, all enforced *outside* the model. |
| **Attack-success reduction** | **100 %** — 42/42 succeed unprotected, 0/42 protected |
| **False-positive rate** | **1.33 %** — 1 of 75 benign spans |
| **Added latency** | **p50 15 ms · p95 21 ms** (last published run); ~33 ms p95 on a cold cache |
| **Endpoints** | 37 paths, 38 operations |
| **Tests** | 77 passing — 68 proxy, 9 benchmark |
| **Code** | ~8,850 lines Python, ~3,290 lines TypeScript |
| **Benchmark** | InjectBench — 42 attacks, 9 families, 23 benign documents, published |
| **Licence** | Apache 2.0, self-hostable, no vendor in the data path |

**The four verdicts:** `pass` · `strip` · `hold` · `block`
**The six provenance labels:** `system` · `user` · `retrieved` · `tool` · `agent` · `unknown`

---

## 1 · The problem

### What is prompt injection, in one paragraph?

An agent's context window is one undifferentiated token stream. The system
prompt, the user's request, a retrieved web page and a tool's response are all
concatenated and attended over uniformly. Nothing in that representation marks
one region as trusted and another as hostile. So a sentence on a web page can
issue instructions to an agent that holds the user's credentials — no malware,
no memory corruption, nothing a conventional detection stack would see.

### Why can't a better model fix it?

Because it isn't a defect in any model. At the token level, an instruction the
user typed and an instruction an attacker embedded are *identical*. There is no
signal for the model to learn. Alignment reduces the rate; it cannot close the
channel. This is the same reason `strcpy` was not fixed by more careful C
programmers — the problem is the interface, not the practitioner.

### Why is this called a confused-deputy problem?

Classic definition: a program with legitimate authority is tricked into
exercising it on an attacker's behalf. The agent holds real credentials, and the
resulting tool call violates no schema, trips no rate limit, and appears in logs
as ordinary authorised activity. There is nothing for a conventional detection
stack to find.

### When does an agent become exploitable?

Three properties, simultaneously:

1. **Access to private data** — mailbox, CRM, wiki, source, customer records
2. **Exposure to untrusted content** — the web, inbound mail, uploads, MCP tools
3. **An egress channel** — sending mail, POSTing, writing a file, rendering a remote image

Every commercially interesting agent has all three by design, and removing any
one destroys the product. So the defence has to live in the space between them.

### Is this actually happening, or is it theoretical?

- **EchoLeak (CVE-2025-32711, June 2025)** — a crafted email caused Microsoft
  365 Copilot to exfiltrate the user's context. Zero-click, zero interaction.
- **Slack AI (August 2024)** — a message in a public channel induced disclosure
  from private channels the attacker could not access.
- **Bing Chat (February 2023)** — a web page redirected the assistant and leaked
  its system instructions. First widely reproduced public demonstration.
- **OWASP Top 10 for LLM Applications** ranks it **LLM01**, highest severity.
- **NIST AI 100-2** classifies indirect injection as unsolved, with no general
  mitigation.

### How big is the exposure in practice?

A modest deployment handling 5,000 agent sessions a day against inbound mail and
web content, at a 1 % injection success rate — far below what unprotected agents
demonstrate on published attack sets — is **fifty successful hijacks every day**,
each carrying the user's full credentials.

---

## 2 · Why existing defences fail

| Approach | Where it breaks |
|---|---|
| Keyword / regex blocklists | Defeated by paraphrase, translation, base64, homoglyphs, or asking politely. Enumerating badness never terminates. |
| Model alignment / RLHF | The model cannot distinguish user instruction from injected instruction — at the token level they are the same. |
| Prompt hardening / delimiters | The instruction to ignore is itself text in the same channel, overridable by text asserting more authority. |
| Output guardrails | Too late. By the time output is generated, the tool call has fired and the data has left. |
| Sandboxing the model | The hijacked action is not an escape — it is an authorised API call the agent was permitted to make. |

The common failure: they all try to judge **the text**, from **inside** the
model's context. Membrane judges **the action**, from **outside** the model.

---

## 3 · The core idea

### What is the inversion?

- Every filter ever built asks: **"Is this input malicious?"** — open-ended, and
  no filter has ever won it.
- Membrane asks: **"Was this action inside the envelope the user authorised?"** —
  closed, decidable, finite answer set.

**Enumerating what is permitted terminates. Enumerating what is forbidden does not.**

### Why the name?

A biological membrane is not a wall — a wall would starve the cell. It is
*selectively permeable*: nutrients cross, pathogens do not, and the selectivity
is a property of the barrier rather than of the contents. That is exactly the
posture: content crosses, instructions do not, and the decision belongs to the
barrier.

---

## 4 · Architecture

### Where does it sit?

A transparent proxy on the agent's ingest and egress paths. The agent's model,
prompts and application code are unmodified — it goes in the way a web
application firewall goes in front of a web server.

```
UNTRUSTED            MEMBRANE                         TRUSTED
web pages ─┐    ┌──────────────────────┐
email ─────┼───▶│ L1 sanitiser         │  clean    ┌──────────┐
PDFs ──────┤    │ L2 separator         │  context  │ LLM      │
MCP tools ─┘    │ L3 taint tracker     │──────────▶│ AGENT    │
                │ L4 capability firewall│           └────┬─────┘
                └───┬──────────┬───────┘                │
                    ▼          ▼            egress re-check on
              Telegram    audit trail       every outbound call
              approve/deny  hash-chained         │
                                                 ▼
                                          privileged tools
```

### What are the three integration points?

Three HTTP calls. Nothing else changes.

```python
# 1. Declare intent once per task → signed, time-limited contract
contract = post("/v1/intent", {
    "session_id": sid,
    "capabilities": ["web.fetch", "mail.send"],
    "task": "Summarise the vendor page and email the billing contact",
})["contract"]

# 2. Everything the agent reads goes through ingest. Use what comes back.
clean = post("/v1/ingest", {
    "session_id": sid, "content": page_html,
    "source": "https://vendor.example/contacts", "content_type": "text/html",
})["content"]

# 3. Every tool call is checked before it fires.
verdict = post("/v1/toolcall", {
    "session_id": sid, "tool": "mail.send", "args": args,
    "contract": contract, "wait_for_approval": True,
})
if verdict["allowed"]:
    send_mail(**args)
```

### What does the request lifecycle look like?

Six stages, four terminal verdicts:

```
1 INGEST → 2 NORMALISE → 3 CLASSIFY → 4 TAINT → 5 ENVELOPE → 6 DECIDE
                                                                 │
        ┌────────────────┬──────────────┬──────────────┬─────────┘
      PASS             STRIP           HOLD          BLOCK
   clean content    payload         frozen,       refused,
   reaches the      excised,        pushed to     logged, fully
   agent            remainder       a human       replayable
   unchanged        forwarded
```

The design constraint: **the common case completes on the deterministic path
without invoking a model at all.**

---

## 5 · Layer by layer

### L1 — Sanitiser

**Job:** remove what a human reader never sees, before any model runs.

Handles: zero-width and bidirectional control characters · Unicode tag
characters (U+E0000–E007F, which mirror ASCII invisibly — a whole sentence hides
inside one emoji) · cross-script homoglyphs · CSS-hidden text, including
stylesheet-defined invisibility, off-screen positioning, `opacity:0`,
`font-size:0`, white-on-white · HTML comments · `alt`, `title`, `aria-label`
attributes · EXIF and PDF metadata and annotations · nested base64, base64url,
hex, percent-encoding, HTML entities, Unicode escapes and ROT13, decoded
recursively to a bounded depth (default 4).

**Homoglyph subtlety:** folding is applied *only inside words that mix scripts*.
A word written entirely in Cyrillic is legitimate Russian and is left alone; a
word mixing Cyrillic and Latin is a homoglyph attack essentially every time.

**Why it carries no false-positive risk:** it only removes content that is
invisible to a human reader by construction.

### L2 — Separator

**Job:** split declarative content from imperative spans. Content is forwarded;
imperatives are quarantined and replaced with a neutral marker that preserves
document structure without preserving authority.

**The key insight — and the thing that makes the false-positive rate viable:**

> An imperative is only dangerous when it is **addressed to the agent**.

A recipe page is nothing but imperatives — preheat, combine, fold, bake — and
none of them are attacks. So a directive on its own contributes almost nothing;
it is multiplied by a *targeting* signal.

```
targeting = max(ai_target, addressed, authority, override)

score = 0.60·override
      + 0.50·addressed          (text speaking TO a model)
      + 0.50·concealment        ("do not mention this to the user")
      + 0.45·(directive × ai_target)
      + 0.40·authority          (impersonating system/developer)
      + 0.60·exfil
      + 0.45·tool_poison
      + 0.45·(delayed × targeting)
      + 0.25·(directive × privileged × targeting)
      + 0.10·(ai_target × privileged)
      + 0.05·directive          ← a bare imperative, alone, scores this
```

**Two tiers.** The deterministic pass resolves the overwhelming majority at
near-zero cost, without the text leaving the process. Only spans in the ambiguous
band (0.35–0.60) escalate to a model. When no model is configured, or one is
configured and fails, ambiguous spans **quarantine** — unavailability never
becomes permissiveness.

**Thresholds:** ≥ 0.60 quarantine outright; 0.35–0.60 escalate then fail closed;
< 0.35 forward. Both thresholds tighten for sources whose trust score has
collapsed.

### L3 — Taint tracker *(our principal technical contribution)*

**Job:** attach immutable provenance to every span, and trace each tool-call
argument back to the spans that could have produced it.

**Matching, in order of confidence:**

| Method | Confidence | Catches |
|---|---|---|
| Atom identity | 0.99 | The exact email, URL, domain, IBAN, account number, UUID, path or handle |
| Verbatim containment (either direction) | 0.85–0.90 | The argument quotes the page, or the page quotes the argument |
| Derivation (token overlap ≥ 0.55) | variable | A summary of a poisoned page pasted into a mail body |

**Provenance ranking** (worst wins):
`system < user < agent < unknown < tool < retrieved`

A value matching nothing is labelled `agent` — model-originated, which is not
user intent and so is never trusted, but is distinguishable from page content.

**A detail that matters:** spans L1 *removed* are registered in the graph too.
The agent never saw them, but the addresses inside them stay traceable, so a
value arriving by another route is still recognised as originating in that
source.

**This is classical dataflow taint analysis applied to a language model's token
stream.** We are not aware of a shipping product that does it.

### L4 — Capability firewall

**Job:** hold anything outside the envelope the user signed.

Contracts are HMAC-SHA256 signed, bound to a session, and time-limited (default
900 s). The decision matrix is evaluated in order; **there is no default-allow
branch**:

| # | Condition | Verdict |
|---|---|---|
| 1 | No valid contract | `block` |
| 2 | Tool maps to no known capability | `hold` |
| 3 | Capability outside the envelope | `hold` |
| 4 | Circuit breaker open, capability not read-only | `block` |
| 5 | Tainted value in a **destination** argument on a write | `hold` |
| 6 | Irreversible capability (mail, payment, delete, shell) | `hold` |
| 7 | Tainted value anywhere on a write | `hold` |
| 8 | Otherwise | `pass` |

**Policy catalogue:** 20 capabilities — 7 read, 5 write, 8 irreversible. Any tool
matching no rule is **held, not passed**: an unknown tool has unknown reach, and
that is exactly the case that must not be guessed at.

---

## 6 · The layers around the four

### Egress inspection

Re-scans outbound arguments for credentials (AWS, GitHub, OpenAI, Anthropic,
Google, Slack, Stripe, JWTs, private keys, bearer headers, generic assignments),
personal data (Luhn-checked cards, SSNs, IBANs, emails, phones), beacon-shaped
URLs, and **data encoded into a hostname label** — which leaves through DNS
resolution and therefore needs no write capability at all.

Credentials are an unconditional `block`. Bulk PII on a write is a `hold`.

### MCP tool-poisoning scanner

Tool descriptions enter the model's context as trusted text, *before* any content
is retrieved, and nobody scans them. A poisoned description compromises every
agent that installs the server — which makes this a supply-chain control rather
than a per-deployment one.

Thresholds are lower than L2's, deliberately: a description field exists to
describe, so the base rate of legitimate instruction-like prose in one is near
zero. A description containing invisible characters is disqualifying on its own.

### Adaptive source trust

Each domain or tool carries a score. Incident → multiply by 0.35. Clean history →
recover at 0.01/hour. Low trust tightens L2's thresholds. The penalty is measured
against the *initial* score (0.70), so an unknown source is judged at nominal
thresholds and only a demonstrated bad actor is held stricter.

### Circuit breaker

5 holds inside a 120-second window quarantines privileged capabilities for 300
seconds. It bounds the blast radius of a novel attack class that gets past all
four layers — the system assumes failure rather than assuming success.

### Self-reinforcing corpus

Every stopped payload is normalised, deduplicated on its salted hash, classified
by family, and filed as a regression case. Coverage grows with attacks rather
than with engineering effort — and only hashes are stored, so the corpus never
becomes a library of live payloads.

---

## 7 · The benchmark

### What is InjectBench?

42 categorised attacks in 9 families, 23 benign documents (75 spans) for the
false-positive rate, a reproducible pytest harness, and a public leaderboard.
Published with the system, because a security claim without a number is an
anecdote.

| Family | Cases |
|---|---:|
| Direct instruction override | 5 |
| Invisible payloads | 6 |
| Encoding evasion | 5 |
| Indirect and stored | 6 |
| Tool poisoning | 4 |
| Exfiltration | 5 |
| Multi-turn and delayed | 4 |
| Authority spoofing | 3 |
| Confused-deputy chains | 4 |
| **Total** | **42** |

### The numbers

| Metric | Target | Measured |
|---|---|---|
| Attack-success-rate reduction | ≥ 90 % | **100 %** (42/42 → 0/42) |
| False positives on benign content | < 2 % | **1.33 %** (1 of 75 spans) |
| Added latency p95 | < 100 ms | **21 ms** (p50 15 ms) |

All three come from **one run**. Any one of them alone is trivially gamed: a
filter that blocks everything has a perfect reduction rate, and one that blocks
nothing has a perfect false-positive rate.

### How does a run work?

Every case runs twice against the **same** reference agent:

- **unprotected** — the document goes straight into the agent's context
- **protected** — the document passes through `/v1/ingest` first, and every tool
  call the agent proposes is gated at the boundary

The only variable is Membrane. Attack success in the protected arm requires both
that the agent still proposes the malicious call *and* that the call is allowed.
A held action with no human present resolves to denial — which is the deployed
behaviour, so that is what the benchmark measures.

### Why is the reference agent rule-based? *(asked often — answer honestly)*

Deliberate trade, and the benchmark's main limitation.

- It is **deterministic**, so a run is exactly reproducible and both arms use an
  identical agent. That isolates the defence as the single variable.
- It has **no refusal behaviour**, so it measures the defence rather than the
  model's alignment. A frontier model would resist some payloads some of the
  time, which would flatter the protected number and add variance to both arms.

**So the unprotected baseline is an upper bound.** The honest reading is
*"Membrane removes N % of the attack surface that reaches the model"*, not
*"N % of GPT-class agents are hijacked"*. This is written up in the InjectBench
README, not buried.

### Does the agent "cheat" by not being able to read the payloads?

The opposite — we had to make it *more* capable to be honest. The agent models
what a language model actually perceives: it reconstructs words split by
zero-width characters, reads Unicode tag characters, reads cross-script
homoglyphs as the letters they imitate, and performs a decoding step when the
text asks for one. Without that, the invisible and encoding families would score
zero against an **undefended** agent, which is the opposite of reality.

That logic lives in the benchmark's `agent.py`, implemented independently of
Membrane's sanitiser, so the baseline never depends on the code under test.

### What about the one false positive?

A security blog post that quotes an injection payload verbatim while explaining
prompt injection. It is in the corpus deliberately, and we report it rather than
removing it. Quarantining a quoted payload inside a security article is arguably
correct conservative behaviour — but it counts against us either way.

### Is the benign corpus a soft target?

Deliberately adversarial. It includes recipes (nothing but imperatives),
operations runbooks containing `rm -rf` and "drop the schema", legal prose full
of second-person modals, API documentation about credentials, a `.env.example`
with placeholder secrets, a wiki page describing what an assistant can do, a
genuine invoice with genuine bank details (structurally identical to the
confused-deputy case), an honest MCP tool description, and two security documents
about prompt injection.

### How do I verify your numbers?

```bash
cd apps/bench
python -m injectbench                 # full report, printed
python -m injectbench --list          # every case, by family
python -m injectbench --case deputy-01  # one case, full trace
python -m pytest                      # the same thing, as assertions
```

It also runs in CI on every push and every six hours on a schedule, with
`--strict`, which exits non-zero unless all three targets are met together.

---

## 8 · Privacy

### How do you inspect everything and keep nothing?

- **Content lives in memory for one request**, then is discarded.
- **The audit trail stores salted span hashes, verdicts, layer decisions and
  provenance edges.** No column in the schema holds inspected text — with one
  exception, an optional bounded replay table that is **off by default** and
  expires automatically.
- **Minimum model exposure:** the deterministic path resolves most spans locally,
  so most content never leaves the proxy process.
- **Secrets by handle:** keys live in Secret Manager and never enter a context.
- **No vendor in the path:** fully self-hostable inside the customer's own
  project. Nobody, including us, sits between the agent and its data.

### "Proof without disclosure" — what does that mean?

Salted hashing lets an operator prove that a specific span was seen and how it
was judged, **without the system ever having kept the text**. Re-hash a candidate
string; if it matches the audit row, that is what produced it.

Forensics and confidentiality stop being in tension. There is a working demo of
this on every session replay page.

### How do I know the audit trail hasn't been edited?

Rows are hash-chained: `entry_hash = H(prev_hash ‖ payload)`. Each entry commits
to its predecessor, so removing or editing a historical row breaks every hash
after it. `GET /v1/sessions/{id}/integrity` recomputes the chain and reports the
first broken sequence number.

**Honest limit:** this is tamper-*evident*, not tamper-*proof*. An attacker with
database write access who can recompute the chain forward can rewrite it. Making
that impossible needs an external anchor — a write-once store, or periodic
publication of the head hash — which is a deployment decision, not a code one.

### What does the human see on a decision card?

Four things: what is being attempted, why it was held, the provenance chain of
the offending argument, and a diff against the signed intent.

It does **not** carry the content the agent was reading. Argument values are
reduced to shape descriptors — *"email address at northwind-billing.invalid ·
af07dc1a"* — because the destination is the decision. Cards are HMAC-signed,
bound to one session, expire after 60 seconds, and are removed from the
conversation on resolution, so a compromised chat history can neither authorise
anything nor leak anything.

---

## 9 · Safety

Every path resolves to the restrictive outcome when something goes wrong. The
cost of a held action that turned out to be legitimate is an operator tapping a
button; the cost of a permitted action that turned out to be an attack is
unbounded.

| Property | Guarantee |
|---|---|
| Fail closed everywhere | Timeouts, outages, quota exhaustion and classifier ambiguity all resolve to hold or block |
| Silence is denial | A card nobody answers expires to a refusal, never an approval |
| Human authority over irreversible acts | Payments, outbound mail, deletion and file writes always require an explicit human decision |
| No silent failure | Everything stopped is visible with an explanation — a user who cannot see the control cannot trust or correct it |
| Bounded blast radius | The circuit breaker quarantines privileged capabilities when holds spike |
| Reversibility | Every held and blocked action is preserved with its reasoning |
| Readiness fails closed | `/readyz` returns 503 when the audit store is unreachable, so the load balancer stops traffic Membrane cannot audit |

**There is no branch anywhere in the tool-call path that returns
`allowed: true` without either a signed contract or a recorded human decision.**
That is asserted directly in the test suite.

---

## 10 · Compliance

`GET /v1/sessions/{id}/evidence` exports an immutable, timestamped,
integrity-checked record of every automated decision, with no personal data in it.

| Framework | How it is met |
|---|---|
| **EU AI Act, Article 12** | Automatic record-keeping over the system's lifetime, with traceability — the immutable verdict log with full decision traces is precisely the evidence agent deployments currently lack |
| **ISO/IEC 42001** | Operational controls for AI system monitoring, incident handling and meaningful human oversight |
| **GDPR / DPDP** | Data minimisation and purpose limitation are structural, not procedural — the system never stores content. Hash-based auditing satisfies accountability without creating a new store of personal data |
| **OWASP LLM Top 10** | Addresses LLM01 (prompt injection) directly; contributes to LLM02 (sensitive information disclosure) and LLM06 (excessive agency) |

The point: a control that created a new repository of citizen data would fail
procurement no matter how well it stopped attacks.

---

## 11 · Limitations — what it does *not* stop

*(Lead with these if asked. Knowing your edges is a strength.)*

1. **Purely semantic manipulation.** A page that states a false fact the model
   then repeats as its own conclusion. L3 catches it when the attacker-supplied
   *value* reaches a tool argument — the common case — but persuasion that
   contributes no traceable value is out of reach of every layer here.
2. **Output-level harm.** Membrane gates tool calls. If the harm is that a user
   reads a wrong answer, no layer fires. Output guardrails are complementary.
3. **A malicious agent framework or compromised model.** Everything assumes the
   agent is *confused*, not hostile. An agent built to exfiltrate needs no
   injection.
4. **Side channels below the argument layer.** Timing, tool-call ordering, and
   the choice of *which* authorised call to make can encode information. We
   inspect arguments, not sequences.
5. **The deterministic tier is English-centric.** It carries a small multilingual
   override lexicon (Spanish, French, German, Italian, Hindi, Chinese) because
   translation is the cheapest bypass, and the structural signals — exfiltration
   shapes, encoded hostnames, hidden content — are language-independent. But a
   fluent novel payload in an uncovered language scores low on the fast path.
   That is exactly what the model tier is for, and a real reason to configure it
   in a multilingual deployment.
6. **Trust scoring is poisonable in one direction.** A patient attacker can build
   a source's score before using it once. The score only loosens thresholds back
   to *nominal* — never below the configured defaults — so the worst case is that
   they get treated like an unknown source, not better.
7. **Derivation matching has a floor.** Token overlap ≥ 0.55 catches a
   paraphrase; it does not catch a heavy rewrite that keeps the meaning and
   almost none of the words. Atom identity still catches the addresses and
   endpoints, which is where the danger concentrates.
8. **`hold` depends on someone answering.** Fail-closed means an unanswered card
   denies the action — safe, but an operator who is asleep is an operator whose
   agent stops working. Deliberate, configurable, and a real operational cost to
   size before deployment.
9. **It is a boundary control, not a sandbox.** An agent that reaches the
   internet directly bypasses it. Network policy forcing egress through the proxy
   is what makes the integration structural rather than voluntary.

### Attacks against Membrane itself

**The L2 model classifier is a target** — the span it judges is hostile text, and
a naive prompt would move the injection one hop. Mitigations are structural: the
span is passed as a JSON string field inside a fixed envelope, the prompt never
asks the model to *follow* anything and states the span has no authority,
generation is capped at four tokens, and the reply is accepted only if it is
exactly one of two literals. Anything else — including a model that starts
complying — parses as failure and returns `None`, which quarantines. **A
successful hijack of the classifier causes a false positive, not a bypass.**

**Decode bombs** — recursive decoding is bounded in depth, input size and by a
seen-set, so a nested payload costs a fixed amount of work.

**Resource exhaustion** — taint graphs are per-session, bounded in count, and
expire on a TTL.

**Callback replay** — Telegram tokens are bound to one action id, one decision
and one session; actions resolve exactly once and cannot be approved after
expiry. The signature is truncated to 64 bits because Telegram caps
`callback_data` at 64 bytes — acceptable because the token is useless outside a
60-second window against a single-use action, but it is a deliberate trade.

---

## 12 · Technology and deployment

| Layer | Choice | Why |
|---|---|---|
| Proxy / API | Python 3.12, FastAPI, Pydantic, uvicorn | Async throughput, typed models; the agent ecosystem this defends is Python-native |
| Sanitisation | `unicodedata`, BeautifulSoup, pypdf, Pillow | Standard library plus three well-known packages. Deterministic, no inference cost |
| Classification | Vertex AI (Gemini) over REST + a local rule pass | One HTTP call, not an ML pipeline. Escalation only on ambiguity bounds cost, latency and exposure |
| Data | PostgreSQL (Cloud SQL), SQLModel, Alembic | Contracts, policy, trust scores and the append-only audit table are ordinary relational rows, queryable with plain SQL |
| Queue / realtime | Redis (Memorystore) | Held-action queue and the dashboard event stream. No managed message bus needed |
| Human loop | Telegram Bot API + a FastAPI webhook route | The webhook is one more endpoint on the service already running — no second runtime |
| Dashboard | Next.js 16, React, TypeScript | The live feed is a normal React view over server-sent events |
| Benchmark | pytest + YAML attack definitions | Reproducible by any third party, which is the point of publishing it |
| Deployment | Docker, Cloud Run, Cloud Build, GitHub Actions | Push to a branch, containers build, services redeploy. No infrastructure language |

### What does it take to run?

Two containers, one Postgres, one Redis, one model API. No cluster, no message
broker, no infrastructure language. Locally it needs **nothing** — it defaults to
a SQLite file and an in-process event bus and behaves identically, with the same
fail-closed paths and the same audit chain.

```bash
make setup && make api && make web      # local, no infrastructure
docker compose up --build               # whole stack
```

### How does it scale?

The API is stateless apart from the in-memory taint graphs, which are
per-session and bounded by count and TTL. With Redis, the event feed and approval
queue are shared across instances, and the approval wait falls back to a database
poll so a card raised on one instance can be resolved on another. Session
affinity is not required, though routing by session avoids rebuilding a graph.

### What must change before production?

`/readyz` reports these, and the service logs them at startup:

- `MEMBRANE_SIGNING_KEY` and `MEMBRANE_HASH_SALT` off their dev defaults
- `MEMBRANE_LIVE_PREVIEW_ENABLED=false` (previews stream inspected text to the
  dashboard; never persisted, but no reason to emit them in production)
- Replay retention stays off unless someone decided otherwise and wrote down why
- Configure the L2 model tier, especially for multilingual content
- Restrict CORS to the dashboard's real origin
- **Force agent egress through the proxy** — network policy is what makes this
  structural

---

## 13 · Who deploys this, and why it matters

| User | Today | With Membrane |
|---|---|---|
| Enterprise platform teams | Agents with mailbox and CRM access are blocked in review, or shipped with risk silently accepted | A boundary control with an audit trail a security review can approve |
| Agent framework / MCP vendors | No way to assure customers a third-party tool is not adversarial | Tool schemas scanned before connection, a benchmark score to point at |
| Public-sector and civic AI | Citizen records handled with defences that do not address the primary attack | Fail-closed enforcement, zero content retention, Article 12 aligned logging |
| Individuals using agentic browsers | Personal mail and accounts exposed to any page the assistant opens | The same protection as an enterprise, nothing retained, no telemetry |
| Small teams | Priced out of enterprise AI security tooling | Open source, serverless, self-hostable, operable from a phone |

**Why the impact is disproportionate:** Membrane sits at an infrastructure layer,
so one correct implementation protects every agent behind it. The MCP scanner in
particular is a supply-chain control — one poisoned tool description propagates
to every agent that installs it, so scanning the registry protects the ecosystem
rather than one deployment.

**The social dimension is not decoration.** Agentic systems are entering benefits
administration, healthcare intake, legal aid and municipal services — settings
where the people whose records are exposed have the least capacity to absorb the
consequences and the least ability to demand better. Those institutions cannot
afford commercial AI security tooling, and many are not permitted to send citizen
data to a third-party inspection service even if they could. Publishing this as
open source, with the benchmark attached and the stack self-hostable, is a
deliberate decision about who gets to be safe.

---

## 14 · Dual use and responsible disclosure

InjectBench contains working attack payloads and we publish it deliberately,
because a defensive benchmark that cannot be reproduced is not a benchmark. Every
constraint below is **enforced by a test**, not merely promised:

- **Proof of concept only.** Every exfiltration case targets a synthetic canary
  (`MEMBRANE_CANARY_7F3A21`) and a reserved, unroutable host under `.invalid`,
  `.example` or `.test` (RFC 2606, RFC 6761). `test_no_case_targets_a_real_host`
  decodes every obfuscated payload — including the ROT13 and base64 ones — and
  asserts the decoded destinations are reserved too. No case moves real data and
  no case is destructive.
- **No live vulnerabilities.** The corpus targets our own reference agent. A
  novel vector found against a named commercial product goes to that vendor
  privately and stays out of the public corpus until fixed.
- **Defence, not offence.** No payload generator, and no tooling whose primary
  use is evasion.
- **Attribution.** Cases drawn from public disclosures cite them.

---

## 15 · Hard questions, prepared answers

**"Isn't this just a fancy keyword filter?"**
No. A directive on its own contributes 0.05 to the score — it only counts when
the span is addressed to the agent. That is why a recipe passes. And the
confused-deputy family has *no keywords at all*; it is caught by provenance, not
by text. A keyword filter cannot express "this address came from a page rather
than from the user".

**"What if the attacker just rephrases?"**
The signals are structural, not lexical: override *shape*, direct address to a
model, concealment requests, exfiltration URL shapes, encoded hostnames, hidden
content. Paraphrase changes words, not structure. Where paraphrase does defeat
the fast path, the span lands in the ambiguous band and either escalates to a
model or fails closed.

**"Could an attacker just use a language you don't cover?"**
Yes, against the deterministic tier — that is limitation 5 and we say so. The
structural signals are language-independent, and the model tier exists for the
rest. In a multilingual deployment you should configure it.

**"Your false-positive rate is on your own corpus."**
Correct, and the corpus is published so you can contest it or add to it. It is
deliberately adversarial — recipes, runbooks with `rm -rf`, legal prose, API
docs about credentials, and two security articles about prompt injection, one of
which is our single false positive.

**"What's the performance cost at scale?"**
p95 of 21 ms in the published run because the common case never calls a model.
Only ambiguous spans escalate. The measurement is the full in-process ingest
path; add HTTP overhead for a network deployment, and expect a higher p95 on the
ambiguous minority when the model tier is enabled — which we would report
separately rather than blending.

**"How is this different from Lakera / Rebuff / prompt-guard?"**
Those are classifiers: they answer "is this input malicious?" — the open-ended
question. Membrane runs a classifier as *one of four layers* and does not depend
on it. The parts that are structurally different are L3 (provenance through the
reasoning, which catches attacks with no text signal at all) and L4 (a signed
capability envelope, which converts the question into a decidable one). We also
publish the benchmark; most do not.

**"What happens when the model tier is down?"**
Ambiguous spans quarantine. Utility degrades, security does not. That is asserted
in the tests — `test_ambiguous_spans_fail_closed_when_no_model_is_reachable`.

**"How would I adopt this incrementally?"**
Start in observe-only by ignoring the verdict and reading the audit trail — you
get the forensics and the measurement with no behaviour change. Then enforce on
`block` only. Then enable holds on irreversible capabilities. The contract is the
last thing to tighten, because it needs you to enumerate what your agents
actually do — which the audit trail will have told you by then.

**"What did you get wrong while building it?"**
Several things worth naming. The `ai_target` signal was substring-matching, so it
fired on "em**ai**l" and "maint**ai**nance". The override patterns excluded
newlines, which made a single line break a **complete bypass** of L2. The MCP
scanner missed descriptions made only of zero-width padding. And our egress
collector list originally contained `evil.*` and `attacker.*` — which are our own
fixture names, i.e. exactly the enumerate-badness approach we criticise, so we
removed them. All four were found by testing rather than by reading, which is the
argument for the benchmark existing at all.

**"Why should we believe any of this?"**
Run it. `make test` is 77 assertions. `python -m injectbench` reproduces every
number on this page. Both run in CI. The threat model document leads with what
the system cannot do.

---

## 16 · Numbers appendix

Every figure, with where it comes from.

| Figure | Value | Source |
|---|---|---|
| Attack cases | 42 | `load_cases()`, asserted in `test_the_corpus_is_the_size_it_claims_to_be` |
| Attack families | 9 | same |
| Benign documents | 23 | `load_benign()` |
| Benign spans | 75 | segmented by L2 |
| Unprotected successes | 42/42 | `test_the_unprotected_baseline_actually_reproduces_every_attack` |
| Protected successes | 0/42 | published run `v1.0` |
| ASR reduction | 100 % | published run |
| False-positive rate | 1.33 % | 1 of 75 spans |
| p50 latency | 15.2 ms | published run |
| p95 latency | 21.1 ms | published run |
| Tests | 77 | 68 proxy + 9 benchmark |
| API endpoints | 37 paths / 38 operations | OpenAPI schema |
| Capabilities | 20 (7 read, 5 write, 8 irreversible) | `GET /v1/capabilities` |
| Python | ~8,850 lines | `wc -l` excluding venv |
| TypeScript | ~3,290 lines | `wc -l` excluding node_modules |
| Contract TTL | 900 s default | `MEMBRANE_CONTRACT_TTL_SECONDS` |
| Approval timeout | 60 s | `MEMBRANE_APPROVAL_TIMEOUT_SECONDS` |
| L2 quarantine threshold | 0.60 | `MEMBRANE_SEPARATOR_QUARANTINE_THRESHOLD` |
| L2 ambiguous threshold | 0.35 | `MEMBRANE_SEPARATOR_AMBIGUOUS_THRESHOLD` |
| Decode depth | 4 | `MEMBRANE_MAX_DECODE_DEPTH` |
| Breaker | 5 holds / 120 s → 300 s cooldown | config defaults |
| Trust | 0.70 initial, ×0.35 on incident, +0.01/hour | config defaults |

*Latency varies run to run; a cold process shows a higher p95 (~33 ms) because
the first call compiles the pattern set. Quote the published run and say so if
the live dashboard differs.*

---

## 17 · Live commands

```bash
# pre-flight, seed, drive, reset
cd docs/demo && python demo.py check | seed | run | reset --yes

# the benchmark
cd apps/bench && python -m injectbench
                 python -m injectbench --case deputy-01
                 python -m injectbench --publish http://localhost:8080

# the tests
cd apps/api && python -m pytest -q      # 68
cd apps/bench && python -m pytest -q    # 9

# useful endpoints in front of a judge
curl localhost:8080/v1/stats
curl localhost:8080/v1/sessions/{id}/replay      # full decision trace
curl localhost:8080/v1/sessions/{id}/evidence    # compliance pack
curl localhost:8080/v1/sessions/{id}/integrity   # hash-chain check
curl -X POST localhost:8080/v1/verify-span \
     -d '{"text":"...","span_hash":"sha256:..."}'   # proof without disclosure
```

**Documents:** `README.md` · `docs/architecture.md` · `docs/threat-model.md` ·
`docs/api.md` · `docs/deployment.md` · `apps/bench/README.md` ·
`docs/deck/SCRIPT.md` · `docs/demo/RUNBOOK.md`

---

## The thirty-second version

> **"An agent reads the open internet and acts with your credentials. Membrane
> sits between those two facts. Four layers strip hidden instructions, separate
> content from commands, track where every value came from, and hold any action
> outside what the user signed for. It stops 42 of 42 published attacks at a
> 1.33 % false-positive rate and 21 milliseconds. And when it isn't sure, it asks
> a human instead of guessing."**
