# Groq malformed-JSON retry — short design

Date: 2026-11-01. Branch `fix/groq-json-retry`, base main `4168658`.

## Intent

The 2026-11-01 smoke run on staging (4168658) failed one case, `directory-ms-2`, as
"unavailable model".

**What staging showed** (the probes printed codes only):
- For some messages, `openai/gpt-oss-120b` writes a short reply that is malformed JSON ("Expecting
  ',' delimiter"). It happened on about half of 13 calls for that message.
- Groq answers that with HTTP 400, `error.type` `invalid_request_error`, `error.code`
  `json_validate_failed`.
- `GroqClient` (`csa/decide/groq.py`) retries only timeouts, transport errors and 429/5xx. It turns
  the 400 into `groq_http_400`. The workspace planner then raises `PlannerUnavailable`, so staff
  see "The assistant is unavailable right now", when one more call would usually work.

The request for that case is unchanged by S3. The model's behaviour drifted: the same case
returned a clarification in earlier runs.

## Goals

1. **Retry once.** A 400 whose JSON body has `error.code == "json_validate_failed"` gets the same
   single retry that a timeout or a 429/5xx gets, inside the existing `ATTEMPTS = 2` and the same
   per-attempt budget (`timeout / ATTEMPTS`). There is no third call.
2. **Name it right.** If the last attempt still ends in that 400, the client raises
   `GroqError("groq_invalid_output")`, the same code it raises today when a 200 reply fails the
   schema. The workspace planner then raises `PlannerInvalidOutput` (`planner_invalid_output`,
   502), and the panel says "The assistant couldn't produce a usable answer. Try again or
   rephrase."
3. **Otherwise unchanged.** Every other 400, and a 400 without a readable JSON body, is not
   retried and stays `groq_http_400`.

## Non-goals

- Groq's strict JSON-schema mode (option 3). It is a later follow-up, and needs a spike first.
- Any change to the planner, its correction loop, the bot's Gemini fallback or the panel text.
- Repairing the failed reply ourselves.

## Anti-goals

- **Never log, store or return Groq's `failed_generation`,** or the error's `message`. On the bot
  path it can hold customer text. Only the fixed codes leave the client.
- **No wider retry.** Other 4xx stay final (a bad key, a bad request). A retry must not push a call
  past the caller's timeout.

## Effects on other callers (accepted with option 1)

All of these share `GroqClient`:
- the customer bot (`csa/decide/llm.py`: classify, plan, compose and review, with Gemini as the
  compose fallback);
- email drafting;
- HR;
- evals and the smoke run.

When the model writes malformed JSON, each of them makes one more Groq call, about 1 second,
before its own fallback or error. Its recorded failure code changes from `groq_http_400` to
`groq_invalid_output`. None of them branch on either code, except the planner as described above.

## Approaches

1. **Retry inside the shared client (chosen by the owner, 2026-11-01).** One place, every caller
   benefits, and it matches how temporary failures are already retried.
2. Retry only in the workspace planner, with its own error code and a planner note. Rejected: the
   bot and email drafting keep the problem, and it spends the planner's single correction round.
3. Groq strict JSON-schema mode. Deferred: it is the real cure for malformed JSON, but
   `PlannerOutput` is a union and the mode's support for that is unverified.

## Tests

In `tests/test_llm_provider.py`, using `httpx.MockTransport`:
- A 400 `json_validate_failed` followed by a valid 200 returns the parsed reply after two requests.
- Two 400 `json_validate_failed` replies raise `groq_invalid_output` after two requests.
- The error text never contains the `failed_generation` value.
- A 400 with another `error.code`, or with no JSON body, makes one request and raises
  `groq_http_400`.

The workspace planner's mapping from `groq_invalid_output` to `planner_invalid_output` already has
tests.

## Left to the builder

- **How the body is read.** Read it defensively: a non-JSON or odd-shaped body means not
  retryable.
- **Docs.** Update the code list in `docs/ARCHITECTURE.md` line 172, and add a PROGRESS entry.
