# fx-tool — currency conversion service

A single FastAPI endpoint an agent can call to convert an amount between two
currencies, backed by the ECB rates published via [Frankfurter](https://frankfurter.dev).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows / Git Bash: `source .venv/Scripts/activate`

## Run

```bash
./run.sh
```

Listens on `0.0.0.0:$PORT` (default `8080`). Override with `PORT=9000 ./run.sh`.

## Test

```bash
./test.sh
```

No real network is used. `test_main.py` monkeypatches `httpx.AsyncClient` with
a fake implementation. `test.sh` also provides a closed local
`FX_UPSTREAM_BASE` fallback when the caller has not supplied one.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `8080` | read by `run.sh` |
| `FX_UPSTREAM_BASE` | `https://api.frankfurter.dev` | default lives in `main.py`; `run.sh` never hardcodes the real host, so a fake upstream can be swapped in for review |

The upstream URL is built as `{FX_UPSTREAM_BASE}/v1/{date}`.

## Endpoint

```
GET /tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28
```

`amount`, `from`, `to`, `date` are all required. `from`/`to` must be 3 letters
(format only — not checked against a currency list); `date` must be
`YYYY-MM-DD`.

### Success — 200

```json
{
  "amount": 250.0,
  "from": "EUR",
  "to": "TRY",
  "rate": 47.1234,
  "result": 11780.85,
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-28",
  "source": "ECB via frankfurter.dev"
}
```

- **`asked_date`** always echoes the `date` you passed in.
- **`rate_date`** is copied verbatim from the `date` field of the upstream
  response — the service never computes or guesses it.
  If the upstream returns a rate belonging to an earlier date (for example,
  for a weekend request), that actual upstream date is exposed as `rate_date`.
  The service never relabels it as `asked_date`.
- **`rate`** is passed through exactly as received from upstream — it is
  **not** rounded.
- **`result`** is `amount * rate`, rounded to 2 decimal places.

### Failure — non-2xx

```json
{ "error": "<code>", "message": "<sentence>" }
```

| Code | Status | When |
|---|---|---|
| `validation_error` | 400 | a required param is missing, or fails basic type/format validation (non-numeric `amount`, `from`/`to` not 3 letters, unparsable `date`) |
| `invalid_amount` | 400 | `amount <= 0`, or `amount` has 10 or more decimal places |
| `identical_currencies` | 400 | `from` and `to` are the same currency (case-insensitive) |
| `future_date` | 400 | `date` is after today (server's local date) |
| `upstream_timeout` | 504 | upstream did not respond within 5s |
| `rate_not_available` | 400 | upstream responded with a 4xx (e.g. unknown currency, or a date it rejects) |
| `upstream_error` | 502 | upstream responded with a non-4xx error status (e.g. 500) |
| `upstream_unreachable` | 502 | connection to upstream failed outright (refused, DNS, etc.) |
| `upstream_invalid_response` | 502 | upstream body isn't valid JSON, isn't a JSON object, or is missing its `date` field |
| `rate_not_found` | 404 | upstream returned a well-formed response, but the requested currency isn't in its `rates` |

### Edge-case decisions

- **Weekend/holiday** — no special-cased fallback logic in this service; the
  request is passed straight through with the asked date, and whatever date
  Frankfurter reports back becomes `rate_date`. The difference from
  `asked_date` is never hidden.
- **Date before the series starts / unusual dates** — not given a distinct
  code. It flows through the same generic upstream-error handling above
  (`rate_not_available` if upstream answers with a 4xx, `upstream_invalid_response`
  if the body shape is unexpected).
- **Unknown currency code** — passes the 3-letter format check, then fails at
  the upstream: `rate_not_available` if upstream itself rejects it (4xx), or
  `rate_not_found` if upstream returns 200 without that currency in `rates`.
- **`from == to`** — rejected before any upstream call, no wasted request.
- **`amount` missing / zero / negative / non-numeric / 10+ decimals** — see
  table above; all rejected before any upstream call.
- **Upstream slow / 500 / non-JSON** — each gets its own code and a non-2xx
  status; nothing is ever defaulted to a rate of `0` or similar.

### Cache

In-memory `dict`, keyed by `{from}-{to}-{asked_date}` — `amount` is
deliberately excluded from the key since it doesn't affect the rate. Only a
successful lookup is cached (the rate plus the true `rate_date` upstream
returned); any failure path is never cached, so a transient outage doesn't get
"frozen in." A second identical question (same `from`/`to`/`date`, any
`amount`) is served from cache and never re-hits upstream.
