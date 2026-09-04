# Review of tool.py

Findings are ranked by customer impact.

## 1. The endpoint does not follow the required query contract

The required endpoint uses `from` and `date`, but `tool.py` expects `from_`
and `on`.

Because these parameters have defaults, a request can be accepted while the
service silently uses `EUR` and the latest rate instead of the values the
caller actually sent.

For example, a customer can ask for a historical USD conversion while the
service calculates a current EUR conversion.

**Customer impact:** the response can look valid even though it was calculated
with the wrong currency or date.

**How I would verify it:** call the exact endpoint from the brief with
`from=USD` and an old `date`, then inspect which parameters are sent to the
upstream.

## 2. Upstream failures are returned as a zero conversion

The endpoint catches every exception and returns `rate: 0.0` and
`result: 0.0` instead of returning a non-2xx error.

A timeout, HTTP 500, network failure, or malformed upstream response can
therefore look like a real conversion.

**Customer impact:** an AI agent could tell a paying customer that their money
converts to zero when the real problem is only that the upstream service is
unavailable.

**How I would verify it:** fake a timeout or upstream 500 and check that the
current implementation returns a zero result instead of an error response.

## 3. The cache does not include the requested date

The cache key only contains the source and target currencies. The requested
date is not included.

This means a rate fetched for one date can be reused for another date.
On a cache hit, the code also reports the requested date rather than
preserving the date that the cached rate actually belongs to.

**Customer impact:** a historical conversion can use the wrong rate while
claiming that the rate belongs to the requested date.

**How I would verify it:** request the same currency pair for two different
dates. The second request can reuse the first cached rate without calling the
upstream again.

## The one I would fix before shipping tonight

I would fix finding #1 first.

It affects the main successful request path. The service can accept the
documented request while calculating with different inputs, which can produce
a believable but incorrect answer even when the upstream is healthy.

## Things that look suspicious but are fine

Using an in-memory dictionary for caching is reasonable for a small service
like this. The problem is the cache key and the missing rate-date information,
not the use of a dictionary itself.

The extra `/health` endpoint is not required, but it does not create a
customer correctness problem by itself.