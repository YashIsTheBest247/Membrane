# Architecture

## Where Membrane sits

Membrane is a transparent proxy on an agent's ingest and egress paths. The
agent's code, model and prompts are unmodified; from its perspective it is
reading the web and calling tools as before. The trust boundary is enforced
entirely outside the model, which is precisely why it does not inherit the
model's inability to distinguish trusted from untrusted text.

```
UNTRUSTED ZONE              MEMBRANE (control plane)                 TRUSTED ZONE

┌──────────────────┐    ┌────────────────────────────────────┐
│ web pages,       │    │       SEMI-PERMEABLE BARRIER       │
│ search results   │───▶│  content passes · instructions do  │
├──────────────────┤    │              not                   │
│ email, shared    │───▶│                                    │
│ documents        │    │ L1 SANITISER                       │   clean   ┌──────────┐
├──────────────────┤    │ zero-width · homoglyphs · CSS-     │  context  │LLM AGENT │
│ PDFs, images,    │───▶│ hidden · alt · EXIF · base64       │──────────▶│ planner  │
│ file uploads     │    │            ▼                       │           │ memory   │
├──────────────────┤    │ L2 SEPARATOR                       │           │ executor │
│ MCP tool & API   │───▶│ declarative vs imperative          │           └────┬─────┘
│ responses        │    │ deterministic → model escalation   │                │
├──────────────────┤    │            ▼                       │           ┌────▼─────┐
│ third-party      │───▶│ L3 TAINT TRACKER                   │           │PRIVILEGED│
│ agent messages   │    │ provenance on every span, lineage  │           │  TOOLS   │
└──────────────────┘    │ resolution for tool arguments      │           │mail·pay  │
                        │            ▼                       │           │files·shell│
                        │ L4 CAPABILITY FIREWALL             │           └────┬─────┘
                        │ signed intent contract; any        │                │
                        │ out-of-envelope call is held       │◀───────────────┘
                        └──────┬──────────────────┬──────────┘  every outbound action
                               ▼                  ▼              re-checked before egress
                     ┌──────────────────┐  ┌──────────────┐
                     │ TELEGRAM HITL    │  │ AUDIT TRAIL  │
                     │ one-tap approve  │  │ Redis → PG   │
                     │ silence = deny   │  │ hash-chained │
                     └──────────────────┘  └──────────────┘
```

## Request lifecycle

Every span of untrusted text traverses six stages and resolves to exactly one
of four outcomes. The design constraint is that the common case — clean content
from a reputable source — completes on the deterministic path without invoking
a model at all.

```
  1 INGEST  →  2 NORMALISE  →  3 CLASSIFY  →  4 TAINT  →  5 ENVELOPE  →  6 DECIDE
  untrusted    unicode fold,    content vs    attach       check the      pass·strip
  bytes        strip            instruction   provenance   intent         hold·block
  arrive       invisibles                                  contract

         ┌────────────┬─────────────┬──────────────┬──────────────┐
         ▼            ▼             ▼              ▼
       PASS         STRIP          HOLD          BLOCK
   clean content  payload       frozen,        refused,
   reaches the    excised,      pushed to a    logged, fully
   agent          remainder     human          replayable
   unchanged      forwarded
```

`HOLD` is the outcome that distinguishes Membrane: uncertainty escalates to a
human rather than resolving to a guess.

## Components

| Component | Responsibility | Implementation |
|---|---|---|
| Ingest proxy | Terminates retrieval calls, enforces per-source budgets | FastAPI, async, stateless |
| Sanitiser (L1) | Unicode normalisation, hidden-content stripping, recursive decoding | Pure Python + BeautifulSoup, pypdf, Pillow |
| Separator (L2) | Content vs imperative classification per span | Deterministic ruleset escalating to Vertex AI |
| Taint engine (L3) | Provenance graph, lineage resolution for tool arguments | In-memory DAG, span hashes, Postgres edges |
| Policy engine (L4) | Contract issue, verification, capability matching | Signed JSON contracts, HMAC-SHA256 |
| Approval service | Held-action queue, decision cards, timeout defaults | FastAPI webhook + Telegram Bot API |
| Telemetry sink | Immutable verdict log, replay corpus, metrics | Redis stream → Postgres → Next.js |
| Bench harness | Runs InjectBench against protected and unprotected agents | pytest + Cloud Scheduler |

## The scoring model, and why it is shaped that way

L2's classifier is the piece most likely to be misunderstood, so it is worth
being explicit about its structure.

The naive design scores "does this text contain an instruction?" and fires on
every recipe, runbook and manual on the internet. The observation that fixes it
is that **an imperative is only dangerous when the agent is its audience**. So
the model computes a *targeting* term:

```
targeting = max(ai_target, addressed, authority, override)
```

and almost every other signal is multiplied by it:

```
score = 0.60·override
      + 0.50·addressed
      + 0.50·concealment
      + 0.45·(directive × ai_target)
      + 0.40·authority
      + 0.60·exfil
      + 0.45·tool_poison
      + 0.45·(delayed × targeting)
      + 0.25·(directive × privileged × targeting)
      + 0.10·(ai_target × privileged)
      + 0.05·directive
```

Consequences worth noting:

- A bare imperative contributes `0.05`. "Preheat the oven to 220C" cannot reach
  the quarantine threshold no matter how many privileged-sounding nouns
  surround it.
- Imperative detection can therefore afford to be *liberal* — clause-initial
  verbs, second-person modals, polite imperatives, task assignments — because
  being wrong about mood costs almost nothing when the audience test fails.
  Being strict about audience and generous about mood is the whole trick.
- `addressed` (text speaking **to** a model: "Assistant: …", "note for any AI
  reading this", "AI agents must …") and `concealment` ("do not mention this to
  the user") are near-conclusive alone, because there is no ordinary reason for
  a web page to do either.
- Signals are scored on text with soft line wrapping collapsed. Without that, a
  single newline in the middle of a payload defeats every pattern — a
  one-character bypass. Authority patterns are the exception and see the
  original line structure, since several anchor to line starts.

Two thresholds: at or above `0.60` a span is quarantined outright; between
`0.35` and `0.60` it escalates to the model tier, and quarantines if no verdict
is available. Both thresholds tighten for sources whose trust score has
collapsed — measured against the *initial* trust value, so a source nobody has
seen before is judged at nominal thresholds and only a demonstrated bad actor
is held to a stricter standard.

## Taint resolution

`SessionTaintGraph.trace(value)` matches an argument against every registered
span by, in order of confidence:

1. **Atom identity** (0.99) — the exact email, URL, domain, IBAN, account
   number, UUID, path or handle appears in that span. Atoms are extracted once
   per span at registration.
2. **Verbatim containment** (0.85–0.9) — in either direction, so both "the
   argument is a substring of the page" and "the argument quotes the page" match.
3. **Derivation** (token overlap ≥ 0.55) — the argument is a rewrite of the
   span, which is what catches a summary of a poisoned page being pasted into
   an email body.

The worst provenance across all matches wins, ranked
`system < user < agent < unknown < tool < retrieved`. A value that matches
nothing is labelled `agent` — model-originated, which is not user intent and so
is never treated as trusted, but is distinguishable from content that came from
a page.

Spans that L1 *removed* are registered in the graph too. The agent never saw
them, but the addresses inside them stay traceable, so a value that reaches a
tool call by some other route is still recognised as having originated in that
source rather than in the user's request.

## Decision matrix at the boundary

Evaluated in order; the first match wins, and there is no default-allow branch.

| # | Condition | Verdict |
|---|---|---|
| 1 | No valid contract | `block` |
| 2 | Tool maps to no known capability | `hold` |
| 3 | Capability outside the envelope | `hold` |
| 4 | Circuit breaker open, capability not read-only | `block` |
| 5 | Tainted value in a destination argument on a write | `hold` |
| 6 | Irreversible capability (mail, payment, delete, shell) | `hold` |
| 7 | Tainted value anywhere on a write | `hold` |
| 8 | Otherwise | `pass` |

Egress inspection runs independently over the same arguments and the more
severe of the two verdicts is taken, so credentials in an outbound argument are
blocked even on a capability that is fully authorised.

## Storage

The schema is identical on SQLite and PostgreSQL — enums are stored as
constrained strings rather than native types, so adding a member is a migration
rather than a database-specific type alteration.

| Table | Holds |
|---|---|
| `sessions` | Counters and breaker state |
| `intent_contracts` | Issued envelopes, for revocation and audit |
| `audit_events` | The hash-chained decision log. No content, ever |
| `provenance_edges` | The taint DAG, as hashes |
| `held_actions` | The approval queue and its decision cards |
| `source_trust` | Per-domain scores |
| `corpus_entries` | Deduplicated payload hashes, by family |
| `bench_runs` | InjectBench results for the leaderboard |
| `replay_spans` | The one table that holds text. Off by default, expires automatically |

The audit chain commits each entry to its predecessor
(`entry_hash = H(prev_hash ‖ payload)`), so removing or editing a historical row
breaks every hash after it. `GET /v1/sessions/{id}/integrity` recomputes the
chain and reports the first broken sequence number.
