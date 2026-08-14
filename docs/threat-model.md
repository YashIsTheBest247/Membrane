# Threat model and known limitations

A security tool that does not say what it cannot do is not usable in a review.
This document is written for the person whose job is to decide whether to
deploy Membrane, so it leads with the gaps.

## What Membrane assumes

- **The proxy itself is trusted.** It sits inside the operator's own
  deployment, holds the signing key and the hash salt, and terminates the
  agent's retrieval and tool calls. Compromise of the proxy is total.
- **The agent framework cooperates.** Membrane is a boundary control, not a
  sandbox. An agent that retrieves content without calling `/v1/ingest`, or
  executes a tool without calling `/v1/toolcall`, is unprotected. Enforcing the
  call is the integrator's responsibility — network policy that permits egress
  only through the proxy is the deployment pattern that makes this structural
  rather than voluntary.
- **The human in the loop is the human.** Telegram callbacks are HMAC-signed,
  bound to one action, expire, and are rejected from any chat other than the
  configured one. But an attacker with the operator's unlocked phone can approve
  a card, exactly as they could authorise anything else on that device.
- **Capability mapping is honest.** If a tool named `docs.read` in fact sends
  mail, the policy file is wrong and L4 will authorise the wrong thing. The
  mapping is operator-supplied configuration, and it is the right place for the
  operator's own judgement about their own tools.

## What it is designed to stop

| Threat | Layer | Notes |
|---|---|---|
| Instructions hidden where a human cannot see them | L1 | Deterministic, no model, no false positives on visible content |
| Instructions in plain sight, addressed to the agent | L2 | Paraphrase- and translation-tolerant; not a signature |
| Poisoned data becoming a privileged argument | L3 | No text signal exists for this; provenance is the only handle |
| Any action outside what the user authorised | L4 | Closed question, decidable |
| Credentials or bulk personal data leaving in a tool argument | Egress | Independent of whether the call was authorised |
| Data smuggled in a hostname label | Egress | Structural entropy test, not a domain blocklist |
| Poisoned MCP tool schemas | MCP scanner | Before connection, so the description never enters the context |
| A novel class that gets past all four layers | Circuit breaker | Bounds the blast radius rather than preventing the first hit |

## What it does not stop

Stated plainly.

**1. An attack whose payload is neither hidden, nor instruction-shaped, nor
tainted.** The clearest example is *semantic* manipulation: a page that states
a false fact ("the billing contact changed to X last month") which the model
then repeats as its own conclusion. L3 catches this when the attacker-supplied
*value* reaches a tool argument, which is the common case, but a purely
persuasive attack that changes what the model believes without contributing a
traceable value is out of reach of every layer here.

**2. Attacks against the model's output rather than its actions.** Membrane
gates tool calls. If the harm is that a user reads a wrong answer, no layer
here fires. Output guardrails are complementary and Membrane does not replace
them.

**3. A malicious agent framework, or a compromised model.** Everything assumes
the agent is confused rather than hostile. An agent deliberately built to
exfiltrate does not need an injection.

**4. Side channels below the argument layer.** Timing, tool-call ordering, and
the choice of *which* authorised call to make can encode information. Membrane
inspects arguments, not the sequence.

**5. The deterministic tier is English-centric.** L2 carries a small
multilingual override lexicon (Spanish, French, German, Italian, Hindi,
Chinese) because translation is the cheapest possible bypass of an English
blocklist, and the structural signals — exfiltration shapes, encoded hostnames,
authority markers, concealment requests, hidden content — are
language-independent. But a fluent, novel payload in a language the lexicon
does not cover will score low on the deterministic path. That case is exactly
what the model escalation tier exists for, and it is a real reason to configure
it in a multilingual deployment.

**6. Trust scoring is per-source and therefore poisonable in one direction.**
An attacker who controls a high-volume clean source can build its score up
before using it once. The score only *loosens* thresholds back to nominal — it
never loosens them below the configured defaults — so the worst case is that a
patient attacker gets the same treatment as an unknown source, not better.

**7. Derivation matching has a floor.** Token-overlap derivation (≥ 0.55)
catches a paraphrase; it does not catch a heavily rewritten summary that
retains the attacker's *meaning* but almost none of the words. Atom identity
still catches the addresses and endpoints, which is where the danger
concentrates.

**8. The `hold` verdict depends on someone answering.** Fail-closed means an
unanswered card denies the action, which is safe but is not free: an operator
who is asleep is an operator whose agent stops working. That trade is deliberate
and configurable via the approval timeout, but it is a real operational cost and
should be sized before deployment.

## Attacks against Membrane itself

**The L2 model classifier is a target.** The span being judged is hostile text,
and a naive prompt would just move the injection one hop. The mitigations are
structural rather than instructional: the span is passed as a JSON string field
inside a fixed envelope, the prompt never asks the model to *follow* anything
and explicitly states the span has no authority, generation is capped at four
tokens, and the reply is accepted only if it is exactly one of two literals.
Anything else — including a model that starts complying with the payload —
parses as a failure and returns `None`, which quarantines. An injection that
successfully hijacks the classifier can therefore cause a *false positive*, not
a bypass.

**Decode bombs.** Recursive decoding is bounded in depth, in input size, and by
a seen-set, so a nested payload costs a fixed amount of work.

**Resource exhaustion via the taint graph.** Graphs are per session, bounded in
count, and expire on a TTL; a session can be dropped explicitly with
`POST /v1/sessions/{id}/forget`.

**Audit tampering.** Rows are hash-chained, so editing history is detectable
and localisable. It is not *prevented* — an attacker with write access to the
database and the ability to recompute the chain forward can rewrite it. Making
that impossible requires an external anchor (a write-once store, or periodic
publication of the head hash), which is a deployment decision rather than a
code one.

**Callback replay.** Telegram callback tokens are bound to one action id, one
decision, and one session; actions are resolved exactly once and cannot be
re-approved after expiry. The signature is truncated to 64 bits because
Telegram caps `callback_data` at 64 bytes — acceptable because the token is
useless outside a 60-second window against a single-use action, but it is a
deliberate trade and worth knowing about.

## Residual risk, stated as a number

On InjectBench, Membrane stops 42 of 42 reproduced attacks with a 1.33 %
false-positive rate. That is a statement about *this corpus*, which we wrote.
It is a floor for confidence, not a ceiling: the corpus is published precisely
so that other people can add the cases we did not think of, and the
self-reinforcing corpus exists because we assume they will.
