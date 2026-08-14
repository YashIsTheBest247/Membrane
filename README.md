# Membrane

**A semi-permeable barrier for AI agents. Content passes through. Instructions do not.**

An AI agent does two things that are safe apart and dangerous together: it
reads text from the open world, and it acts with the full authority of its
user. Every operating system built in the last fifty years separates code from
data — a document cannot execute itself. A language model collapses that
separation: instructions and content arrive in one undifferentiated token
stream, and the model has no reliable way to tell the user's intent apart from
a sentence hidden inside a product page.

Membrane is a transparent proxy that restores the separation from outside the
model. It needs no change to the agent's model, prompts, or application code.

```
UNTRUSTED ZONE                 MEMBRANE                          TRUSTED ZONE

web pages ─┐         ┌──────────────────────────────┐
email ─────┤         │ L1  sanitiser                │  clean     ┌──────────┐
PDFs ──────┼────────▶│ L2  separator                │  context   │ LLM AGENT│
MCP tools ─┤         │ L3  taint tracker            │───────────▶│          │
messages ──┘         │ L4  capability firewall      │            └────┬─────┘
                     └───────┬──────────────┬───────┘                 │
                             ▼              ▼                    privileged
                     Telegram HITL     audit trail                   tools
                   one-tap approve      Redis → Postgres         ▲    │
                                                                 └────┘
                                              every outbound action re-checked
```

---

## Results

Measured by [InjectBench](apps/bench/), which is published with the system and
run on every commit. All three numbers come from the same run, because any one
of them alone is trivially gamed — a filter that blocks everything has a
perfect reduction rate, and one that blocks nothing has a perfect
false-positive rate.

| Metric | Target | Measured |
|---|---|---|
| Attack-success-rate reduction | ≥ 90 % | **100 %** (42/42 → 0/42) |
| False positives on benign content | < 2 % | **1.33 %** (1 of 75 spans) |
| Added latency, p95 | < 100 ms | **≈ 33 ms** |

The unprotected baseline reproduces **all 42** attacks, which is what makes the
reduction figure mean anything — a defence cannot take credit for an attack
that never worked. The single false positive is a security blog post that
quotes an injection payload verbatim; it is in the corpus deliberately and we
report it rather than removing it. See [the methodology and its
limitations](apps/bench/README.md#methodology-and-limitations).

```
  ATTACK SUCCESS
    unprotected    42/42  ██████████████████████  100.0%
    protected       0/42  ······················    0.0%
    reduction          100.0%   target ≥ 90%   PASS
```

---

## Quick start

No infrastructure required — the proxy runs against a local SQLite file and an
in-process event bus, and degrades to exactly the same behaviour it has in
production.

```bash
make setup          # venv + dependencies + dashboard packages
make api            # proxy      → http://localhost:8080/docs
make web            # dashboard  → http://localhost:3000
make bench          # InjectBench, printed report
make test           # 77 tests across both suites
```

Or the whole stack, with Postgres and Redis:

```bash
cp .env.example .env
docker compose up --build
```

---

## Integrating an agent

Three calls. Nothing else changes.

```python
import httpx

MEMBRANE = "http://localhost:8080"

# 1. Declare intent before the task starts. You get a signed, time-limited
#    envelope; the task text becomes the trusted provenance anchor.
contract = httpx.post(f"{MEMBRANE}/v1/intent", json={
    "session_id": session_id,
    "capabilities": ["web.fetch", "mail.send"],
    "task": "Summarise the vendor page and email the summary to my billing contact",
}).json()["contract"]

# 2. Everything the agent reads goes through ingest. Use what comes back.
clean = httpx.post(f"{MEMBRANE}/v1/ingest", json={
    "session_id": session_id,
    "content": page_html,
    "source": "https://vendor.example/contacts",
    "content_type": "text/html",
}).json()["content"]

# 3. Every tool call is checked before it fires.
verdict = httpx.post(f"{MEMBRANE}/v1/toolcall", json={
    "session_id": session_id,
    "tool": "mail.send",
    "args": {"to": recipient, "subject": subject, "body": body},
    "contract": contract,
    "wait_for_approval": True,   # block until a human decides, or it times out
}).json()

if verdict["allowed"]:
    send_mail(**args)
```

A verdict is one of four:

| | |
|---|---|
| `pass` | clean content reaches the agent unchanged |
| `strip` | payload excised, benign remainder forwarded |
| `hold` | frozen, pushed to a human, **silence is denial** |
| `block` | refused, logged, fully replayable |

---

## How it works

### L1 — Sanitiser: remove what the user cannot see

Injection payloads overwhelmingly hide where a human reader never looks. This
layer is deterministic, runs before any model, and carries no false-positive
risk on visible content: zero-width and bidirectional control characters,
Unicode tag characters (an entire sentence can hide inside one emoji),
cross-script homoglyphs folded only inside mixed-script words, CSS-hidden and
off-screen text — including invisibility defined in a stylesheet rather than
inline — HTML comments, alt and title attributes, EXIF and PDF metadata, and
nested base64, hex, percent and ROT13 blobs decoded recursively to a bounded
depth.

### L2 — Separator: split content from imperatives

Surviving text is parsed into two channels. Declarative content is forwarded;
imperative spans are quarantined and replaced with a neutral marker that
preserves document structure without preserving authority.

The scoring model encodes one idea that blocklists miss: **an imperative is
only dangerous when it is addressed to the agent.** A recipe page is nothing
but imperatives and none of them are attacks. So a directive contributes almost
nothing on its own — it is multiplied by a *targeting* signal that measures
whether the span is speaking to a model. That single design decision is why the
false-positive rate is 1.33 % on a corpus that deliberately includes recipes,
runbooks with `rm -rf`, legal prose, and API documentation about credentials.

Two tiers: a deterministic pass resolves the overwhelming majority of spans at
near-zero cost and without the text leaving the process; only genuinely
ambiguous spans escalate to a model. When no model is configured, or one is
configured and fails, ambiguous spans quarantine. Unavailability never becomes
permissiveness.

### L3 — Taint tracker: provenance that survives reasoning

The principal technical contribution. Every span entering the context carries
an immutable provenance label. When the agent later proposes a tool call, each
argument is traced back to the spans that could have produced it — by atom
identity (the exact address, URL, IBAN or account number), by verbatim
containment in either direction, and by token-overlap derivation for values the
model rewrote.

A recipient address that originated in the user's typed request is trusted. The
same address, first seen in a page the agent happened to read, is tainted, and
a tainted argument on a privileged call is held regardless of how reasonable
the model's justification sounds.

This is what catches the confused-deputy family, where there is no injected
instruction anywhere: the document is not hidden, not encoded, not imperative,
and not suspicious in any way a text classifier could detect. The payload is
legitimate-looking data.

### L4 — Capability firewall: the intent contract

The inversion the design rests on. Instead of the open-ended question "is this
input malicious?", which no filter has ever won, the enforcement point asks the
closed one: "is this action inside the envelope the user authorised?"
Enumerating what is permitted terminates. Enumerating what is forbidden does
not.

Contracts are HMAC-signed, bound to a session, and time-limited. Anything
outside is held with an explanation rather than silently dropped, because a
silent drop teaches an operator nothing.

### Around the four layers

- **Egress inspection** re-scans outbound arguments for credentials, personal
  data and beacon shapes — including data smuggled into a hostname label, which
  leaves through DNS resolution and so needs no write capability at all.
- **The MCP scanner** audits tool schemas before an agent connects. A poisoned
  description compromises every agent that installs the server, which makes
  this a supply-chain control.
- **Adaptive source trust** collapses a domain's score on incident and recovers
  it slowly, tightening L2's thresholds around sources that have already
  attacked.
- **The circuit breaker** quarantines privileged capabilities when held actions
  spike, bounding the blast radius of an attack class that gets past all four.
- **The self-reinforcing corpus** files every stopped payload — as a hash — as a
  regression case, so coverage grows with attacks rather than with engineering
  effort.

---

## Privacy

A control that inspects everything an agent reads sees a great deal of private
data. That was the central design constraint, not an afterthought: a security
tool that becomes its own breach target is a net loss.

- **Inspected, never retained.** Raw content lives in memory for the life of a
  request. The audit trail stores salted span hashes, verdicts, layer decisions
  and provenance edges. No column in the schema holds inspected text, with one
  exception — an optional, bounded, off-by-default replay table that expires
  automatically.
- **Proof without disclosure.** An operator who suspects a specific string was
  the payload behind an audit row can prove it by re-hashing the candidate.
  Forensics and confidentiality stop being in tension. There is a working demo
  of this on every session replay page.
- **Tamper-evident.** Audit rows are hash-chained: each entry commits to its
  predecessor, so editing or deleting history breaks every hash after it and
  the replay endpoint reports exactly where.
- **Minimum exposure to models.** The deterministic path resolves most spans
  locally; a span reaches a model only when the fast path is inconclusive.
- **No vendor in the path.** The whole stack is self-hostable. Nobody,
  including us, sits between the agent and its data.

Decision cards carry only what a human needs to decide: the action, the reason,
the provenance chain, and a diff against the signed intent. Argument values are
reduced to shape descriptors — *"email address at evil.invalid · a3f91c2b"* —
because the destination is the decision, and the content the agent was reading
is never mirrored into the chat.

---

## Safety

Every path resolves to the restrictive outcome when something goes wrong. The
cost of a held action that turned out to be legitimate is an operator tapping a
button; the cost of a permitted action that turned out to be an attack is
unbounded.

| Property | Guarantee |
|---|---|
| Fail closed everywhere | Timeouts, outages, quota exhaustion and classifier ambiguity all resolve to hold or block |
| Human authority over irreversible acts | Payments, outbound mail, deletion and file writes always require an explicit human decision |
| Silence is denial | A decision card nobody answers expires to a refusal, never to an approval |
| No silent failure | Everything stopped is visible with an explanation |
| Bounded blast radius | The circuit breaker quarantines privileged capabilities when holds spike |
| Reversibility | Every held and blocked action is preserved with its reasoning |

There is no branch anywhere in the tool-call path that returns `allowed: true`
without either a signed contract or a recorded human decision. That is asserted
directly in the test suite.

---

## Repository

```
apps/api/       the proxy — FastAPI, the L1–L4 pipeline, forensics, webhook
  membrane/layers/    l1_sanitiser, l2_separator, l3_taint, l4_capability,
                      egress, mcp_scan
  membrane/routers/   gateway, approvals, forensics, stream, mcp, bench
  tests/              68 tests
apps/web/       the dashboard — Next.js, live feed, replay, approvals, leaderboard
apps/bench/     InjectBench — 42 attacks, 23 benign documents, the harness
docs/           architecture, API reference, deployment, threat model
```

Further reading: [architecture](docs/architecture.md) ·
[API reference](docs/api.md) · [threat model and known
limitations](docs/threat-model.md) · [deployment](docs/deployment.md) ·
[InjectBench](apps/bench/README.md)

---

## Compliance

The audit trail is shaped to be usable as evidence, not only as debugging
output. `GET /v1/sessions/{id}/evidence` exports an immutable, timestamped,
integrity-checked record of every automated decision with no personal data in
it — the record-keeping expectation of **EU AI Act Article 12**, the monitoring
and human-oversight controls of **ISO/IEC 42001**, and the accountability
requirement of **GDPR** without creating a new store of personal data.
Membrane addresses **OWASP LLM01** directly and contributes to LLM02 and LLM06.

---

## Responsible disclosure and dual use

InjectBench contains working attack payloads and is published deliberately,
because a defensive benchmark that cannot be reproduced is not a benchmark. The
constraints are enforced by a test, not just promised:

- **Proof of concept only.** Every exfiltration case targets a synthetic canary
  and a reserved, unroutable host under `.invalid`, `.example` or `.test`. A
  test decodes every obfuscated payload and asserts that nothing in the corpus
  can reach a real system.
- **No live vulnerabilities.** The corpus targets our own reference agent.
- **Defence, not offence.** No payload generator, and no tooling whose primary
  use is evasion.

---

## Licence

Apache 2.0. Open source, self-hostable, and operable from a phone — a
deliberate decision about who gets to be safe, given that the institutions
introducing agents into benefits administration, healthcare intake and legal
aid are the ones least able to afford commercial AI security tooling and often
not permitted to send citizen data to a third-party inspection service at all.

*Team NullDeity · 2nd NextGen Hackathon · ACM Fremont Chapter in association
with the Soft Computing Research Society.*
