"""Speaker notes and timing for the deck.

Ten minutes is 600 seconds. The budget below leaves ~45s of slack for the demo
overrunning, which it always does. Notes are written as things to *say*, not as
a transcript to read.
"""

NOTES = {
    1: ("0:00 – 0:30", """
Membrane. We're Team NullDeity, track 02.

One line before anything else: an AI agent reads text from the open world, and
it acts with your credentials. Membrane is the barrier between those two facts.

Content passes through it. Instructions do not.
"""),
    2: ("0:30 – 1:20", """
Every operating system for fifty years separates code from data — a document
cannot execute itself. A language model collapses that. System prompt, your
request, a web page it fetched, a tool response: one sequence, attended over
uniformly. Nothing marks one region as hostile.

An agent becomes exploitable when three things are true at once — private data,
untrusted content, and a way to act. Every commercially interesting agent has
all three by design. You can't remove any of them, so the defence has to live
in the space between them.
"""),
    3: ("1:20 – 2:00", """
This is not a projection. EchoLeak, disclosed June last year — a crafted email
made Microsoft 365 Copilot exfiltrate the user's context. Zero clicks. Slack AI,
2024: a public message pulled content out of private channels. Bing Chat, 2023.

OWASP ranks it LLM01 — highest severity. NIST calls indirect injection unsolved.

And it scales with autonomy: five thousand sessions a day at a one-percent
success rate is fifty hijacks a day, each with the user's full credentials.
"""),
    4: ("2:00 – 2:45", """
Everything currently used against this breaks in the same place.

Blocklists enumerate badness — that never terminates. Alignment can't help,
because at the token level an injected instruction and a real one are
identical. Delimiters fail because the instruction to ignore is itself text in
the same channel. Output guardrails are too late — the tool call already fired.
Sandboxing misses the point: the hijacked action isn't an escape, it's an
authorised API call.

The pattern: they all try to judge the text, from inside the model's context.
"""),
    5: ("2:45 – 3:20", """
So we inverted the question.

Every filter ever built asks "is this input malicious?" — open-ended, and no
filter has ever won it. Membrane asks "was this action authorised?" — closed,
decidable, finite.

Enumerating what is permitted terminates. Enumerating what is forbidden does not.

That's the whole idea. The membrane metaphor is exact: not a wall, which would
starve the cell — selectively permeable, and the selectivity belongs to the
barrier, not the contents.
"""),
    6: ("3:20 – 4:05", """
Architecturally it's a transparent proxy at the trust boundary. No change to
the agent's model, prompts, or code — it goes in the way a WAF goes in front of
a web server.

Untrusted content enters on the left, passes four layers, and reaches the agent
as clean context. Every outbound tool call is re-checked on the way out. Held
actions go to a human; every verdict goes to a hash-chained audit trail.
"""),
    7: ("4:05 – 5:00", """
The four layers, quickly.

L1 strips what a human can't see — zero-width characters, Unicode tags, hidden
CSS, alt-text, EXIF, nested encodings. Deterministic, no model, no false
positives on visible content.

L2 splits declarative content from imperatives. The key idea is that an
imperative is only dangerous when it's addressed to the agent — so a directive
alone scores almost nothing. That's why a recipe page full of commands passes
and a payload doesn't.

L3 is the one we think is novel — I'll show it next.

L4 is the capability firewall: a signed, time-limited contract of what this
task may do. Anything outside is held with an explanation.
"""),
    8: ("5:00 – 5:50", """
This is the case nothing else catches.

The user says: read the vendor's contact page, email our summary to their
billing contact. The page lists a billing address — which the attacker edited.
The agent emails it. That call is perfectly well-formed: right schema, right
tool, sensible reasoning, and it looks like ordinary authorised activity in
every log.

Nothing is hidden, encoded, or imperative. No text classifier can see this,
because there's nothing wrong with the text.

Membrane holds it because the recipient traces back to a page, not to the
user's request. That's a fact about provenance, not about content.
"""),
    9: ("5:50 – 6:20", """
Six stages, four verdicts. Pass, strip, hold, block.

The design constraint is that the common case — clean content from a decent
source — finishes on the deterministic path without ever calling a model. That
is what keeps latency and cost and data exposure down all at once.

HOLD is the one that matters: when we're uncertain we don't guess, we ask.
"""),
    10: ("6:20 – 6:55", """
And asking means a card on the operator's phone, over Telegram — because a
control that needs someone watching a web console gets bypassed the first time
it's inconvenient.

The card carries four things: what's being attempted, why it was held, the
provenance chain, and a diff against the signed intent. It does not carry the
content the agent was reading — argument values are reduced to shapes.

Sixty seconds with no answer is a denial. Silence is never approval.
"""),
    11: ("6:55 – 7:25", """
This is running now, not a mockup.

Live verdict feed over server-sent events, which layer stopped what, added
latency against a 100ms budget, and a one-click attack that drives the real
pipeline — if there's time at the end I'll run it live.
"""),
    12: ("7:25 – 8:00", """
And this is a held action.

The tainted recipient is flagged, its lineage is named — retrieved, from that
vendor page, matched on the email atom. Argument values are shapes and hashes,
not values. And the attempted capability is diffed against the contract the
user actually signed.

That's what an operator needs to decide in five seconds, and what an auditor
needs six months later.
"""),
    13: ("8:00 – 8:50", """
Numbers. We built and published InjectBench alongside the system — 42 attacks
in 9 families, 23 benign documents, reproducible harness.

100% reduction: 42 of 42 succeed unprotected, zero succeed through Membrane.
1.33% false positives on benign content. About 33 milliseconds added at p95.

Two honesty notes, because a benchmark you wrote yourself deserves them. First:
the unprotected baseline reproduces all 42 — a defence can't take credit for an
attack that never worked. Second: our one false positive is a security article
that quotes an injection payload verbatim. We left it in the corpus and we
report it.
"""),
    14: ("8:50 – 9:30", """
A control that inspects everything is a control that sees everything, so
privacy was the design constraint, not an afterthought.

Content lives in memory for one request. The audit trail stores salted hashes
and provenance — no column holds text. You can still prove a specific string
was the payload behind a row by re-hashing it, without us ever having kept it.

Everything fails closed. Irreversible actions always need a human. And the
audit export is shaped as compliance evidence — Article 12, ISO 42001.
"""),
    15: ("9:30 – 10:00", """
Where we are: it's built. 37 endpoints, the console, the Telegram loop, the
benchmark. 77 tests green.

It's open source and self-hostable, and that's deliberate. Agents are going
into benefits administration, healthcare intake, legal aid — where the people
exposed have the least ability to absorb the consequences, and the institutions
can't buy commercial AI security tooling or ship citizen data to a third party.

Content passes through. Instructions do not. Thank you — happy to run the live
attack if we have a minute.
"""),
}
