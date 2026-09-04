# Notes

## Decisions

FastAPI + httpx for a small async service with minimal dependencies. The core
decision is the split between `asked_date` and `rate_date`: `rate_date` is
taken verbatim from the `date` field the upstream itself returns, never
computed or guessed locally. When the upstream returns a rate belonging to a
different date than the one asked for, the service preserves and exposes that
actual upstream date instead of pretending the rate belongs to the requested
date. No branch invents a number: anything that can't produce a trustworthy
rate exits through an explicit error code and message, never a default or zero
rate. Caching is a simple in-memory dict keyed on
`(from, to, asked_date)`, deliberately excluding `amount` since it doesn't
affect the rate; only successful lookups are cached, so a transient upstream
failure self-heals on the next request instead of getting stuck. Scope was
kept minimal — one endpoint, no auth/db/Docker — per the brief's "a smaller
thing done carefully."

## With another day

- Bound or TTL the cache — currently unbounded and process-lifetime, fine for
  a case study, not for a long-running service.
- A few opt-in integration tests against the real Frankfurter response shape,
  to catch upstream contract drift (everything today is intentionally mocked,
  per the brief's no-network requirement).
- Carry `Decimal` all the way through the rate/result math instead of
  dropping to `float` after the initial amount validation — more defensible
  for anything payments-adjacent.
- Give "date before the series starts" its own error code instead of folding
  it into the generic upstream-4xx path, if that distinction turns out to
  matter to callers.

## AI tools

Gemini, ChatGPT, and Claude were all used during this case. Gemini for the
first pass at the implementation and test suite. ChatGPT for reviewing the
brief's requirements, reasoning through edge cases, and checking the
test/verification flow. Claude for the run/test scripts, dependency list, and
this documentation. The final code was run and checked manually against the
README's requirements before submitting — nothing here was accepted purely on
an assistant's say-so.

## One thing the AI got wrong

An early AI suggestion validated `amount` against an assumed 2- or 4-decimal
limit that isn't in the brief. Re-reading the case brief made clear the actual
requirement is about *ten* decimal places, not two or four. The invented
2/4-decimal constraint was removed, and the current behavior — reject only at
10 or more decimal places — was verified against the brief and against
`test_validation_amount_decimals`.
