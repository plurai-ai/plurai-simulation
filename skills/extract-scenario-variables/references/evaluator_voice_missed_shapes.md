# Evaluator-voice archetypes the check does not catch

`verify.py` flags, as a `⚠` advisory, an archetype whose `style`, `context` or `motivations` makes
the assistant its object of study. The check is LEXICAL, and it deliberately under-catches. This file is the
list of what it lets through, so "the validator said nothing" is never read as "there is nothing
here".

## The trade, stated

The detector has TWO branches, and they use DIFFERENT noun sets. Reading the narrow one as the
whole rule is how the list below gets over-trusted.

- **The verb branch** pairs an observational verb (`see`, `test`, `probe`, `evaluate`, `assess`,
  `verify`, `observe`, `check`) with `the/this assistant | chatbot | bot` — that set and nothing
  else (`_ASSISTANT_NOUN`).
- **The passive branch** — `<noun> … is being | gets tested | probed | evaluated | assessed` —
  uses the WIDER set (`_STUDIED_NOUN`): the three above PLUS `agent`, `model`, `system`, `flow`.
  "the system is being tested" has no reading in which a caller is describing their own problem,
  so the register is in the CONSTRUCTION, not in the verb's neighbourhood, and the words that are
  unsafe next to a bare `check` are safe here.

`agent`, `system`, `model` and `flow` were removed from **the verb branch only**. They are
ordinary nouns of the customer's own domain — an insurance `agent`, a `model` number on a receipt,
the `flow` of funds on a statement, `system` status — so beside a loose verb they flagged caller
prose that was exactly right: *"wants to check the system status before calling support"*,
*"needs to see the agent notes from last week"*. A false flag is an instruction to rewrite a
correct archetype.

So the undercount is the chosen side. It is not free, and the cost is below.

## The shapes that pass silently

Each of these is an evaluator persona. None is flagged.

```
wants to see how the agent handles an out-of-order cancellation
wants to see how it handles a contradictory request
tries to break the assistant with a contradictory service request
stress-tests the system with contradictory inputs
curious whether the bot will catch the contradiction
is here to red-team the assistant
benchmarks the assistant against last quarter
wants to find out where the assistant breaks
treats every answer as a data point for the evaluation
is running an experiment on the support flow
```

Four families are visible in that list:

- **The domain noun.** The first line is this skill's own canonical example with `agent`
  substituted for `assistant` — and `agent` is this product's word for the thing under test, so
  it is the substitution an author is most likely to make. `system`, `model` and `flow` behave the
  same way.
- **The pronoun.** `how IT handles…` names no noun at all, and there is nothing for the pair to
  match against.
- **The verb outside the set.** `break`, `red-team`, `benchmark`, `stress-test`, `experiment on`
  are the register without one of the eight verbs the detector pairs on.
- **The frame with no verb phrase.** *"treats every answer as a data point for the evaluation"*
  puts the persona outside the conversation without ever saying it is testing anything.

## What to do with this

Read every archetype against the RULE, not against the validator's silence. The rule is one
question: **does this person have a problem, or a hypothesis about the system?** A caller has a
problem. An archetype that is curious about how the agent behaves is an evaluator, whatever
nouns it happens to use, and every scenario dealt that archetype produces user turns that
announce the probe to the agent — the transcript is unfaithful and no eval fix repairs it.

Re-author the named field into what the person WANTS and how they behave when they do not get
it. Do not delete the archetype; the difficulty it was reaching for is usually real and belongs
in `typical_complications` or in the scenario, not in the persona's voice.

## Widening the detector is not the fix

Adding these words back to `_ASSISTANT_NOUN` re-buys the false flags the narrowing ended, each
one an instruction to rewrite a correct archetype. A shape that gets past this check is not
unguarded: the backend judges the same property again at scenario authoring
(its coherence auditor, `scenario.audit_coherence`), so a missed one costs a round rather than a silent unfaithful suite.
