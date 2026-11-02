# Groq malformed-JSON retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Groq HTTP 400 with `error.code == "json_validate_failed"` gets the client's existing single retry, and becomes `groq_invalid_output` when the last attempt still ends that way.

**Architecture:** One change in `GroqClient.json_completion` (`csa/decide/groq.py`): the attempt loop treats that one kind of 400 like a 429 or 5xx, and the code after the loop names it `groq_invalid_output`. No caller changes. The workspace planner already maps `groq_invalid_output` to `planner_invalid_output`.

**Tech Stack:** Python 3.12, httpx (`MockTransport` in tests), pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-groq-json-retry-design.md`

## Global Constraints

- The retry stays inside `ATTEMPTS = 2` and the per-attempt budget `timeout / ATTEMPTS`. There is never a third request.
- Retryable means `response.status_code == 400` and the JSON body's `error.code == "json_validate_failed"`. A non-JSON or odd-shaped body is not retryable.
- The final code for that 400 is `groq_invalid_output`. Every other 400 stays `groq_http_400`.
- Never log, store or put into an exception Groq's `failed_generation` or the error's `message`. Only fixed codes leave the client.
- Locally, run only the changed test file (`tests/test_llm_provider.py`) plus `make lint`. The full suite runs in PR CI.
- Worktree session rule: no `VAR=` prefixes or compound commands. Run pytest through the session wrapper: `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest <args>` from the worktree root.

## Review Focus

1. **A 400 with no JSON body, or a body that is a JSON list or string.** Expect one request, then `groq_http_400`, and no exception escaping from reading the body. Pinned by `test_groq_other_400_is_not_retried`, which includes these bodies.
2. **The first attempt times out and the second gets `json_validate_failed`.** Expect `groq_invalid_output` after two requests, not a third. Pinned by `test_groq_json_validate_after_timeout_is_final`.
3. **The first attempt gets `json_validate_failed` and the second gets a 503.** Expect `groq_http_503`: the last response decides the code. Pinned by `test_groq_json_validate_then_503_reports_503`.
4. **`failed_generation` holding text the caller sent.** It must not appear in `str(exc)`, `exc.code` or the log records. Pinned in `test_groq_json_validate_twice_is_invalid_output` with `caplog`.
5. **The success log line after a retry.** `llm_call` is logged once, with the total latency. Already covered by the existing retry path; no new test.

---

### Task 1: Retry `json_validate_failed` in `GroqClient`

**Files:**
- Modify: `csa/decide/groq.py` (the attempt loop and the code after it, lines 56–85)
- Test: `tests/test_llm_provider.py` (next to `test_groq_gives_up_after_second_failure`)
- Docs: `docs/ARCHITECTURE.md` line 172, and a new top entry in `docs/PROGRESS.md`

**Interfaces:**
- Consumes: nothing new.
- Produces: no new public names. Optionally a private helper `_json_validate_failed(response: httpx.Response) -> bool` in `csa/decide/groq.py`. It returns True only for a 400 whose body parses to a dict with a dict `error` whose `code` is `"json_validate_failed"`, and it never raises.

- [ ] **Step 1: Write the failing tests** in `tests/test_llm_provider.py`. Use `GroqClient("secret-key", "m", http)` over `httpx.MockTransport`, as `test_groq_error_codes` does, and count requests.

  Define a helper body:

  ```python
  def _json_validate_failed() -> httpx.Response:
      return httpx.Response(
          400,
          json={
              "error": {
                  "message": "Failed to validate JSON. Please adjust your prompt.",
                  "type": "invalid_request_error",
                  "code": "json_validate_failed",
                  "failed_generation": '{"action": "clarification" "question": "SECRET-GENERATION"}',
              }
          },
      )
  ```

  Then add these tests:
  - `test_groq_json_validate_failed_is_retried_then_succeeds`: the first response is `_json_validate_failed()`, the second a valid `ComposeOut` 200 (reuse `_ok(...)` or the existing JSON shape). Assert 2 requests, and that the parsed result is returned.
  - `test_groq_json_validate_twice_is_invalid_output`: both responses are `_json_validate_failed()`. Assert 2 requests and `err.value.code == "groq_invalid_output"`. Assert `"SECRET-GENERATION"` is in none of: `str(err.value)`, `repr(err.value)`, or any `caplog` record message.
  - `test_groq_other_400_is_not_retried`, parametrized over these bodies:
    - `json={"error": {"code": "model_not_found"}}`;
    - `content=b"not json"`;
    - `json=["json_validate_failed"]`;
    - `json={"error": "json_validate_failed"}`.

    Assert 1 request and `code == "groq_http_400"`.
  - `test_groq_json_validate_after_timeout_is_final`: the first request raises `httpx.ReadTimeout`, the second returns `_json_validate_failed()`. Assert 2 requests and `groq_invalid_output`.
  - `test_groq_json_validate_then_503_reports_503`: the first response is `_json_validate_failed()`, the second `httpx.Response(503)`. Assert 2 requests and `groq_http_503`.

- [ ] **Step 2: Run the tests to see them fail**

  Run: `bash …/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_llm_provider.py -q -k "json_validate or other_400"`

  Expected: the retried, twice, after-timeout and then-503 tests FAIL (1 request, or `groq_http_400`). `test_groq_other_400_is_not_retried` already passes.

- [ ] **Step 3: Implement in `csa/decide/groq.py`**
  - In the loop, retry when `response.status_code in _RETRYABLE or _json_validate_failed(response)` and `attempt + 1 < ATTEMPTS`.
  - After the loop, when the final response is not a success and `_json_validate_failed(response)`, raise `GroqError("groq_invalid_output")` with no cause that carries the body (`from None`). Otherwise keep `groq_http_{status}`.
  - Don't log the body.
  - Update the loop comment to name the malformed-JSON 400.

- [ ] **Step 4: Run the whole test file**

  Run: `bash …/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_llm_provider.py -q`

  Expected: all pass, including `test_groq_error_codes`, `test_groq_retries_once_on_timeout_then_succeeds` and `test_groq_gives_up_after_second_failure`.

- [ ] **Step 5: Update the docs**
  - **`docs/ARCHITECTURE.md` line 172:** list `groq_invalid_output` among the codes, and say a 400 `json_validate_failed` is retried once and then reported as `groq_invalid_output`.
  - **`docs/PROGRESS.md`:** add a new first entry after `# Progress`, in the style of the entries below it. It covers:
    - the branch and spec;
    - the smoke failure on 4168658 (34 of 35, `directory-ms-2`), and that 5515ef7's run had the same failure;
    - the probe finding: about half of 13 calls were a 400 `json_validate_failed` with an 85-character malformed reply;
    - the change and its effect on other callers (one more Groq call, about 1 s, and the code becomes `groq_invalid_output`);
    - the deferred option of Groq strict JSON-schema mode;
    - the test count.

- [ ] **Step 6: Run lint**

  Run: `make lint` from the worktree root (through the wrapper if it needs the env).

  Expected: clean. Commit any formatting change with the task.

- [ ] **Step 7: Commit**

  ```bash
  git add csa/decide/groq.py tests/test_llm_provider.py docs/ARCHITECTURE.md docs/PROGRESS.md
  git commit -m "fix(decide): retry Groq's malformed-JSON 400 once, then report groq_invalid_output"
  ```

  End the message with the Co-Authored-By line.
