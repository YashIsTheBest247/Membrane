# Demo runbook

Membrane · Team NullDeity · NGH26_132 · Track 02

Two versions below. **Version A** is 75 seconds and goes inside the deck at
slide 11. **Version B** is four minutes for a dedicated demo slot or Q&A. Run A
unless someone asks for more.

Everything hits the running proxy. Nothing here is a recording.

---

## Pre-flight — do this 10 minutes before

Two terminals, then one command.

```bash
# terminal 1 — the proxy
cd apps/api && .venv/Scripts/python.exe -m uvicorn membrane.main:app --port 8080

# terminal 2 — the dashboard
cd apps/web && npx next start -p 3000

# terminal 3 — check and seed
cd docs/demo
python demo.py check      # tells you what is missing and how to fix it
python demo.py seed       # gives the console a believable history
```

`check` must print **Ready to present.** If it flags anything, fix that first —
the two that matter are *pending held actions* (an old card left on screen makes
your live one hard to spot) and *no benchmark run* (the InjectBench page would
be empty).

**Then set the room up:**

| | |
|---|---|
| Browser | `http://localhost:3000/dashboard` — the **Live feed**, full screen, zoom 100% |
| Second tab | `http://localhost:3000/leaderboard` — for the numbers question |
| Terminal | `docs/demo`, ready to type, large font |
| Do not | leave the browser on Held actions — start on Live feed |

`http://localhost:3000` is the landing page, not the console. If you want to
open on it for effect, click **Enter Membrane** and let the transition run
*before* you start talking — it is about two seconds, and it is dead air if
you play it mid-sentence.

---

## Version A · 75 seconds, inside the deck

You arrive here on slide 11. Alt-tab to the browser.

> **"This is running right now. Every verdict you see arrives over a live
> stream — nothing here is a recording."**

*(Point at the four tiles, then the activity chart. Two seconds, no more.)*

> **"I'm going to attack it. This runs the confused-deputy case against the
> real pipeline."**

**→ Click `Run live attack`** (top right).

The step console fills in on the right while you talk. Track it:

| What appears | What you say |
|---|---|
| *Intent contract issued* | "First it declares intent — web.fetch and mail.send, signed and time-limited." |
| *Poisoned page ingested* — **STRIP** | "It reads a vendor page carrying a hidden instruction. L1 strips it. That never reaches the model — about thirty milliseconds." |
| *Agent proposes mail.send* | "Now the agent does exactly what the user asked: email the billing contact it just read off the page." |
| *Held for a human* — **HOLD** | "And there it stops. The recipient traces back to a web page, not to the user's request." |

*(Point at the sidebar — the Held actions badge has incremented.)*

> **"Nothing was hidden in that address. It's a plausible email on the right
> page. No text filter can see this — that's why we track provenance."**

**→ Click `Held actions`.**

> **"This is what reaches a human. What's being attempted, why it was held, and
> the provenance chain — retrieved, from that vendor page, matched on the email.
> Note the argument values are shapes and hashes, not values. And it's diffed
> against the contract the user signed."**

**→ Click `Deny`.** Return to the deck.

> **"Sixty seconds with no answer and it denies itself. Silence is never
> approval."**

---

## Version B · four minutes, standalone

Version A, then continue.

### 1 · The audit trail (45s)

**→ From the resolved card, click the session link → replay page.**

> **"Every decision in that session, in order, hash-chained. Each entry commits
> to the one before it, so editing history breaks every hash after it."**

*(Point at "Hash chain intact".)*

> **"And nowhere in this trail is the payload. We store salted hashes,
> provenance and verdicts — never the content."**

**→ Click a span button, paste the payload text into the box, click Verify.**

> **"But we can still prove what it was. Re-hash a candidate and it matches the
> audit row. Forensics without keeping the data — that's what makes this
> deployable somewhere that can't afford a second breach target."**

### 2 · The layers, visibly (60s)

**→ Playground → click `Zero-width interleaving` → `Run the layers`.**

> **"This reads normally to you. It's got zero-width characters between every
> letter, so no blocklist matches it."**

*(Point at the L1 panel showing the recovered payload, then the two channels.)*

> **"L1 folds it back and strips it. L2 splits what's left into content and
> instructions. The content goes to the agent; the instruction doesn't."**

**→ Click `Benign: recipe` → `Run the layers`.**

> **"And this matters just as much. A recipe is nothing but imperatives —
> preheat, combine, bake. All of it passes. An imperative is only dangerous when
> it's addressed to the agent."**

### 3 · The supply chain (45s)

**→ MCP scanner → `Scan before connecting`.**

> **"Tool descriptions enter the model's context as trusted text, before any
> content is retrieved. Nobody scans them. This server has three tools — two
> carry instructions, one is honest. We reject the server before the agent ever
> connects."**

### 4 · The numbers (30s)

**→ InjectBench tab.**

> **"42 attacks in 9 families, 23 benign documents, published with the system.
> All 42 succeed against an undefended agent, zero get through Membrane, at a
> 1.33% false-positive rate and about 33 milliseconds. We publish the failures
> too — the one false positive is a security article that quotes an injection
> payload, and we left it in."**

---

## If something breaks

| Symptom | Do this |
|---|---|
| Button does nothing / page frozen | Alt-tab to the terminal: `python demo.py run`. Same endpoints, narrate off the terminal, the console still updates behind you. |
| Dashboard shows "Feed disconnected" | The proxy died. Restart terminal 1. The dashboard reconnects on its own. |
| Console is empty | `python demo.py seed`, then reload the page. |
| A stale card is already pending | `python demo.py seed` clears the queue as part of seeding. |
| Everything is wrong | `python demo.py reset --yes`, restart the proxy, `python demo.py seed`. Ninety seconds. |
| No network at the venue | Nothing here needs network. It is all localhost, SQLite and an in-process event bus. |

**The one thing not to do:** don't apologise for the Telegram card not appearing.
No bot is configured, and that is by design — the dashboard resolves cards
instead. If asked, say: *"Set a bot token and the same card goes to a phone. We
kept it local so nothing leaves this laptop."*

---

## Questions you will get

**"Isn't this just a keyword filter?"**
No — a directive on its own scores almost nothing. It only counts when the span
is addressed to the agent. That's why the recipe passes. And the confused-deputy
case has no keywords at all: it's caught by provenance, not by text.

**"What's your false-positive rate really?"**
1.33% per span on a corpus that deliberately includes recipes, runbooks with
`rm -rf`, legal prose and API docs about credentials. The one failure is a
security article quoting a payload. We report it rather than removing it.

**"Would this work against a real LLM?"**
The reference agent is rule-based and deterministic so the benchmark is
reproducible and both arms use the identical agent. That makes the unprotected
baseline an upper bound. The honest reading is "Membrane removes N% of the
attack surface that reaches the model", not "N% of GPT-class agents are
hijacked". It's written up in the InjectBench README.

**"What does it cost in latency?"**
About 33ms at p95, because the common case never calls a model. Only genuinely
ambiguous spans escalate.

**"What can't it stop?"**
Purely semantic manipulation — a page that states a false fact the model then
repeats, without contributing a traceable value. Also anything where the agent
bypasses the proxy entirely; it's a boundary control, not a sandbox. Both are in
`docs/threat-model.md`, which leads with the gaps.

**"Why should we believe the numbers?"**
The benchmark is in the repo, the harness is `python -m injectbench`, and it
runs in CI every six hours. Run it yourself.

---

## The one-sentence version

If you get thirty seconds and nothing else:

> **"An agent reads the web and acts with your credentials. Membrane sits
> between those two facts: content passes through, instructions don't — and
> anything it isn't sure about goes to a human instead of a guess."**
