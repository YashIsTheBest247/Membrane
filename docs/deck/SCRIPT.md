# Presentation script

Membrane · Team NullDeity · NGH26_132 · Track 02: Cybersecurity for the Future

Ten minutes, fifteen slides.

**Measured: 1,253 spoken words.** Once the 75-second demo is taken out, you have
8:45 of speech budget, so:

| Your pace | Speech runs | Against budget |
|---|---|---|
| 130 wpm (slow, deliberate) | 9:38 | 53s over — cut slides 4 and 9 |
| 140 wpm (normal) | 8:57 | 12s over — fine, that's inside the noise |
| 150 wpm (brisk) | 8:21 | 24s spare |

So: **at a normal speaking pace this fits, with nothing to spare.** If you tend
to slow down under pressure — most people do — drop slide 4 and slide 9. They
are marked below and the argument survives without them.

Time yourself once with a stopwatch before the day. That is the only way to know
which of those three rows is actually you.

**How to use this.** Read it aloud twice. Then present from the *bold lines*
only — those are the load-bearing sentences, and everything else is
connective tissue you can improvise. Do not memorise it word for word; a script
delivered from memory sounds like a script.

*Italics in brackets are stage directions, not speech.*

---

## Timing at a glance

| | Slide | Ends at | Cut first? |
|---|---|---|---|
| 1 | Title | 0:30 | |
| 2 | The problem | 1:20 | |
| 3 | Evidence | 2:00 | trim to one example |
| 4 | Why defences fail | 2:45 | **cut this** |
| 5 | The core idea | 3:20 | never |
| 6 | Architecture | 4:05 | |
| 7 | The four layers | 5:00 | trim to L2 + L3 |
| 8 | The confused deputy | 5:50 | **never** |
| 9 | Lifecycle | 6:20 | cut this |
| 10 | Human loop | 6:55 | |
| 11–12 | **Live demo** | 8:10 | never |
| 13 | Measurement | 8:55 | never |
| 14 | Privacy & safety | 9:30 | trim to two lines |
| 15 | Close | 10:00 | |

If you are told "five minutes": **1, 2, 5, 8, demo, 13, 15.** That is the whole
argument and it still lands.

---

## 1 · Title — 0:00

*(Do not read the slide. Look at them, not the screen.)*

**"An AI agent does two things. It reads text from the open internet. And it
acts with your credentials."**

*(Pause. One beat.)*

**"Separately, both are fine. Together, they're the largest unpatched hole in
production software right now."**

"We're Team NullDeity. This is Membrane — a prompt-injection firewall for
agentic AI. Content passes through it. Instructions don't."

---

## 2 · The problem — 0:30

"Here's why this is architectural, not a bug someone can patch.

**Every operating system built in the last fifty years separates code from
data. A document cannot execute itself.**

A language model collapses that. The system prompt, your request, a web page it
fetched, a tool's response — they all arrive as one sequence, and the model
attends over all of it the same way. Nothing in that representation says *this
part is trusted and this part is hostile*."

*(Point at the specimen block.)*

"**That's a real page. One line of invisible text is enough** — the user sees a
price, the model sees an instruction."

"An agent is exploitable when three things are true at once: it reaches private
data, it reads untrusted content, and it can act. Every agent worth building has
all three by design. **You can't remove any of them — so the defence has to live
in the space between them.**"

---

## 3 · Evidence — 1:20

"This isn't hypothetical, and I'd rather quote the industry than ourselves.

**EchoLeak — a CVE from June last year.** A crafted email made Microsoft 365
Copilot leak the user's context. No click. No interaction at all. The vector was
ordinary inbound mail.

Slack AI in 2024, Bing Chat in 2023 — same pattern.

OWASP ranks prompt injection **LLM01 — highest severity in the category.** NIST
calls indirect injection unsolved."

*(Beat.)*

"And it scales with autonomy. **Five thousand agent sessions a day, at a one
percent success rate, is fifty successful hijacks every day** — each one
carrying the user's full credentials."

---

## 4 · Why the defences fail — 2:00

*(This is the slide to cut if you are behind. If you keep it, do not read the
table — pick the pattern out.)*

"Everything currently used against this breaks in the same place.

Blocklists enumerate badness — that never terminates. Alignment can't help:
at the token level, an injected instruction and a real one are identical.
Guardrails are too late — the call already fired. And sandboxing misses the
point entirely, because **the hijacked action isn't an escape. It's an
authorised API call the agent was allowed to make.**

They all try to judge the text, from inside the model's context."

---

## 5 · The core idea — 2:45

*(Slow down here. This is the sentence they should remember.)*

"So we inverted the question.

**Every filter ever built asks: is this input malicious?** That's open-ended,
and no filter has ever won it.

**Membrane asks: was this action authorised?** That's closed. Decidable. Finite."

*(Beat.)*

"**Enumerating what is permitted terminates. Enumerating what is forbidden does
not.**

The name is exact, by the way. A membrane isn't a wall — a wall would starve the
cell. It's selectively permeable, and the selectivity belongs to the barrier,
not to whatever's trying to cross it."

---

## 6 · Architecture — 3:20

"Architecturally it's a transparent proxy at the trust boundary. **No change to
the agent's model, prompts, or code** — it goes in the way a web application
firewall goes in front of a web server.

Untrusted content enters on the left. Four layers. What reaches the agent is
clean context.

Then every outbound tool call gets re-checked on the way out. Anything held goes
to a human. Every verdict goes to a hash-chained audit trail.

**The important part: enforcement sits outside the model. So it doesn't inherit
the model's blindness.**"

---

## 7 · The four layers — 4:05

*(Do not narrate all four evenly. Two sentences on L1 and L4, dwell on L2 and
L3.)*

"L1 strips what a human can't see — zero-width characters, Unicode tags, hidden
CSS, alt-text, EXIF, nested encodings. Deterministic, no model call.

**L2 is where the interesting idea is.** It splits content from instructions.
And the thing that makes it work: **an imperative is only dangerous when it's
addressed to the agent.** So a directive on its own scores almost nothing — it
only counts multiplied by whether the text is talking to a model.

That's why a recipe page passes. It's nothing but commands — preheat, combine,
bake — and none of them are attacks.

L3 I'll show you in a second. And L4 is the capability firewall — the agent
declares its intent up front, gets a signed time-limited contract, and anything
outside that envelope is held."

---

## 8 · The confused deputy — 5:00

*(The most important slide. Do not rush it.)*

"This is the case nothing else catches.

The user says: read the vendor's contact page, email our renewal summary to
their billing contact.

*(Point at the specimen.)*

"**Read that page. Nothing is wrong with it.** It's a contact directory. There's
a billing address — which the attacker edited.

The agent emails it. And that call is *perfect*: right tool, right schema,
sensible reasoning. In every log it looks like ordinary authorised activity.

**Nothing here is hidden, encoded, or imperative. There is no text signal to
find, because there's nothing wrong with the text.**

Membrane holds it — because the recipient traces back to a page the agent read,
not to anything the user said. **That's a fact about provenance, not about
content.** That's L3."

---

## 9 · Lifecycle — 5:50

*(Fast. Thirty seconds. Cut if behind.)*

"Six stages, four verdicts — pass, strip, hold, block.

The constraint we designed to: **the common case finishes without ever calling a
model.** That's what keeps latency, cost and data exposure down together.

Hold is the one that matters. When we're not sure, we don't guess."

---

## 10 · The human loop — 6:20

"We ask instead. And we ask on the operator's phone, over Telegram — because a
control that needs someone watching a web console gets bypassed the first time
it's inconvenient.

The card carries four things: what's being attempted, why it was held, the
provenance chain, and a diff against what the user actually authorised.

**It does not carry the content the agent was reading.** Argument values are
reduced to shapes — 'an email address at this domain' — because the destination
is the decision.

**Sixty seconds with no answer is a denial. Silence is never approval.**"

---

## 11–12 · Live demo — 6:55

*(Alt-tab to the browser. Follow `docs/demo/RUNBOOK.md`, Version A. Budget 75
seconds. The full click-path and lines are there — the short form:)*

> "This is running right now."
> **→ Run live attack**
> Narrate: contract → strip → agent proposes → **held**
> "The recipient traces to a web page, not to the user's request."
> **→ Held actions** — walk what / why / provenance / diff
> **→ Deny**
> "Sixty seconds and it denies itself."

*(Return to the deck. Do not linger — you have the numbers coming.)*

---

## 13 · Measurement — 8:10

*(Confidence here. These are your numbers and they are good.)*

"A security claim without a number is an anecdote, so we built the benchmark and
published it with the system.

**InjectBench: 42 attacks in 9 families, 23 benign documents, reproducible
harness.**

**All 42 succeed against an undefended agent. Zero get through Membrane.
1.33% false positives. About 33 milliseconds added.**

Two honesty notes, because a benchmark you wrote yourself deserves them.

**First: the unprotected baseline reproduces all 42.** A defence can't take
credit for an attack that never worked in the first place.

**Second: our one false positive is a security article that quotes an injection
payload verbatim.** We left it in the corpus and we report it."

---

## 14 · Privacy and safety — 8:55

"A control that inspects everything sees everything. So privacy was the design
constraint, not an afterthought — **a security tool that becomes its own breach
target is a net loss.**

Content lives in memory for one request. The audit trail stores salted hashes
and provenance. **No column holds text.** And you can still prove a given string
was the payload behind a row by re-hashing it — forensics without ever having
kept the data.

Everything fails closed, irreversible actions always need a human, and the audit
export is shaped as **Article 12 and ISO 42001 evidence** — which is what
regulated deployments are actually blocked on."

---

## 15 · Close — 9:30

"Where we are: it's built. The proxy, the console, the Telegram loop, the
benchmark. 77 tests, all green.

It's open source and self-hostable, and that's a deliberate choice.

**Agents are going into benefits administration, healthcare intake, legal
aid** — where the people whose records are exposed have the least ability to
absorb the consequences, and the institutions can't buy commercial AI security
tooling or ship citizen data to a third party."

*(Beat. Slow down.)*

**"Content passes through. Instructions do not."**

"Thank you. Happy to take questions — or run the attack again if you'd like to
see it."

---

## Delivery notes

**Pace.** You have slack. The instinct under pressure is to speed up; resist it.
The three places to deliberately slow down are the inversion on slide 5, the
"nothing is wrong with that page" line on slide 8, and the final sentence.

**Hands.** Point at the specimen blocks on slides 2 and 8, and at the held-action
card in the demo. Everything else, leave the slide alone.

**Don't read bullets.** Slides 7 and 14 have the most text on them. Speak *about*
them; the judges can read faster than you can talk.

**If the demo fails**, don't apologise twice. Say "let me run that from the
terminal" and use `python demo.py run`. It takes eight seconds and it's the same
code path. Then carry on.

**If you're asked to stop**, jump straight to slide 15 and give the last two
lines. They work as an ending from anywhere.

**Questions you'll get** are answered in `docs/demo/RUNBOOK.md` — the two you
should rehearse are *"isn't this just a keyword filter?"* and *"would this work
against a real LLM?"* Answer the second one honestly; the limitation is written
down in our own README and saying so is worth more than dodging it.
