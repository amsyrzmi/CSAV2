# Directory search by job title and any word order (S1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The company directory search matches every query word, in any order, against name, job title or work email, ranks name matches first, and the planner searches job titles in English.

**Architecture:**
- `search_people` (`csa/workspace/directory.py`) splits the query into escaped LIKE patterns and passes them as one `text[]` to `db/sql/workspace/people_search.sql`.
- The SQL keeps a row only when every pattern matches one of the three fields, and orders "name or email holds every word" first.
- The planner prompt gains one line. The smoke run gains an optional check of the lookup query's words.

**Tech Stack:** Python 3.12, psycopg3, Postgres 16 (testcontainers), FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-directory-search-design.md`

## Global Constraints

- **Matching:**
  - Every word must appear, case-insensitively, as a substring of `display_name`, `job_title` or `work_email`.
  - Words are split on whitespace and commas, and at most the first 5 count.
  - A query with no words matches nothing.
- **Ranking and limit:**
  - Rows whose `display_name` or `work_email` holds every word come first.
  - Then `display_name`, then `employee_id`.
  - `LIMIT 10` stays.
- **Escaping:** `%`, `_` and `\` in a word are escaped as `_like` does today.
- **Unchanged:**
  - the result fields (`employee_id`, `display_name`, `job_title`, `work_email`);
  - the department, still never searched or returned;
  - roles and the package gate, the throttle, `q` bounds 1–80 with its 422s;
  - the count-only audit row, the M5 address question and the send rule.
- **SQL:** `people_search.sql` keeps `client_id = %s` as its first filter, and all filtering stays in SQL, before `LIMIT`. There is no migration and no new dependency.
- **The planner line**, added verbatim inside the `directory_lookup` bullet of `SYSTEM_PROMPT`: `The query holds only name words or a job title; write a job title in English as the directory stores it, whatever the user's language (for example "pengurus kewangan" becomes "Finance Manager").`
- **Local test runs:**
  - Run each changed test file on its own, plus `make lint`. The full suite runs in PR CI.
  - Use the wrapper from the worktree root: `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest <file> -q -p no:cacheprovider`.
  - No `VAR=` prefixes or compound commands.
- **Formatting:** the editor's format hook rewraps files to 120 columns. After any Edit/Write of a `.py` file, run `uv run ruff format <files>` and check that `git diff --stat` shows only intended lines.

## Review Focus

1. **A one-word query** (the common case: "ali", "danial"). It must behave exactly as today for names and emails, and also find title words. Pinned by the unchanged `test_search_returns_only_three_fields_and_matches_name_or_email`, whose fixture title "Analyst" does not hold "ali".
2. **A word that matches only inside an email**, such as "aina.rahman" or "@example". The email still counts as a name-side match for ranking. Pinned in `test_search_ranks_name_matches_before_title_matches`.
3. **Repeated or extra spaces and commas** ("Aina,  Rahman"). These are split cleanly, with no empty pattern (an empty pattern `%%` would match everything). Pinned in `test_search_matches_every_word_in_any_order`.
4. **More than 5 words.** Only the first 5 count. The query still narrows, and a word after the fifth is ignored rather than causing an error. Pinned in `test_search_matches_every_word_in_any_order`.
5. **A Malay title request planned as a Malay query** ("pengurus kewangan"). It finds nobody, because titles are English. That is caught only by the smoke run's `query_words` check on `directory-ms-4`.

---

### Task 1: Word-by-word search in SQL

**Files:**
- Modify: `csa/workspace/directory.py` (`_like` stays; `search_people` changes)
- Modify: `db/sql/workspace/people_search.sql`
- Test: `tests/test_workspace_people.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `search_people(conn, client_id: UUID, query: str) -> list[PersonOut]`, with the same signature. Callers `csa/workspace/service.py:1707` and `csa/console/workspace.py:151` are unchanged.
  - A private helper `_word_patterns(query: str) -> list[str]`: the escaped `%word%` patterns of the first 5 words, split on whitespace and commas, with empty words dropped.

- [ ] **Step 1: Give the fixture a job title.** Add `job_title: str = "Analyst"` to the `employee` helper, used in the INSERT in place of the literal `'Analyst'`.

- [ ] **Step 2: Write the failing tests** in `tests/test_workspace_people.py`. Each one activates `hr` for `t0`, signs in `staff`, and creates employees with `employee(...)`.
  - `test_search_finds_a_job_title`:
    - employees "Siti Aminah" (title "Finance Manager") and "Bo Other" ("Analyst");
    - `search(browser, t0, "finance manager")` gives names `["Siti Aminah"]`;
    - `"FINANCE"` gives `["Siti Aminah"]`.
  - `test_search_matches_every_word_in_any_order`:
    - employees "Nur Aina binti Rahman" and "Danial Hakim";
    - "Aina Rahman" gives `["Nur Aina binti Rahman"]`;
    - "Hakim Danial" gives `["Danial Hakim"]`;
    - "Aina,  Rahman" gives `["Nur Aina binti Rahman"]`;
    - "Aina Zulkifli" gives `[]`;
    - "nur aina binti rahman x y" (6 words; the 6th, "y", is ignored, but "x" is the 5th) gives `[]`;
    - "nur aina binti rahman nur zzz" gives `["Nur Aina binti Rahman"]` ("zzz" is the 6th word).
  - `test_search_mixes_a_name_word_and_a_title_word`:
    - employees "Danial Hakim" ("Finance Executive") and "Danial Other" ("Sales Executive");
    - "danial finance" gives `["Danial Hakim"]`.
  - `test_search_ranks_name_matches_before_title_matches`:
    - employees "Aaron Lim" ("Sales Manager"), "Zara Sales" ("Analyst") and "Mia Tan" ("Analyst", email `sales.mia@example.test`);
    - "sales" gives `["Mia Tan", "Zara Sales", "Aaron Lim"]`.

    The name or email matches come first, each group ordered by name; Aaron matches on title only.

  The existing `test_search_limit_wildcards_and_bounds` and `test_search_returns_only_three_fields_and_matches_name_or_email` must still pass unchanged.

- [ ] **Step 3: Run the tests to see them fail**

  Run: `bash …/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_workspace_people.py -q -p no:cacheprovider`

  Expected:
  - the four new tests FAIL (no title matching, no word split);
  - the existing tests pass.

- [ ] **Step 4: Implement.**
  - **`_word_patterns`:** `re.split(r"[\s,]+", query.strip())`, dropping empty words, taking the first 5, and mapping each through `_like`.
  - **`search_people`:** returns `[]` without a query when there are no patterns. Otherwise it passes `(client_id, patterns, patterns)` to the SQL.
  - **`people_search.sql`:**

    ```sql
    SELECT employee_id, display_name, job_title, work_email
    FROM public.company_directory d
    WHERE client_id = %s
      AND NOT EXISTS (
        SELECT 1 FROM unnest(%s::text[]) AS w(p)
        WHERE NOT (d.display_name ILIKE w.p OR d.job_title ILIKE w.p OR d.work_email ILIKE w.p))
    ORDER BY EXISTS (
        SELECT 1 FROM unnest(%s::text[]) AS w(p)
        WHERE NOT (d.display_name ILIKE w.p OR d.work_email ILIKE w.p)),
      display_name, employee_id
    LIMIT 10
    ```

    `job_title` and `work_email` can be NULL. `NULL ILIKE p` is NULL, so `NOT (… OR NULL OR …)` must still be true when the other fields fail. If a test shows otherwise, wrap each nullable field in `coalesce(…, '')`.

- [ ] **Step 5: Run the file**, with the same command as Step 3. Expected: all pass.

- [ ] **Step 6: Commit.**

  ```bash
  git add csa/workspace/directory.py db/sql/workspace/people_search.sql tests/test_workspace_people.py
  git commit -m "fix(workspace): directory search matches every word in name, job title or email"
  ```

  End the message with the Co-Authored-By line.

### Task 2: Planner line, smoke query check, checklist and docs

**Files:**
- Modify: `csa/workspace/planner.py` (`SYSTEM_PROMPT`, the `directory_lookup` bullet only)
- Modify: `scripts/assistant_smoke.py` (`Expect`, `run_case`, and the docstring's call count if the case count changes it)
- Modify: `csa/workspace/smoke_cases.json`
- Modify: `docs/ASSISTANT_RETEST.md` (Directory table, the "Reading a FAIL line" list)
- Modify: `docs/PROGRESS.md` (new top entry)
- Test: `tests/test_workspace_planner.py`, `tests/test_assistant_smoke.py`

**Interfaces:**
- Consumes: Task 1's search behaviour (for the docs only).
- Produces: `Expect.query_words: list[str] = []`. When it is non-empty and the final proposal is a `DirectoryLookupProposal`, each word must be a case-insensitive substring of `proposal.query`, or the case fails with the label `query_words`.

- [ ] **Step 1: Write the failing tests.**
  - **In `tests/test_workspace_planner.py`:** `test_planner_prompt_states_the_english_job_title_rule` asserts the exact Global Constraints line is `in SYSTEM_PROMPT`.
  - **In `tests/test_assistant_smoke.py`:** `test_smoke_checks_the_lookup_query_words(capsys)`, using the file's fake-client pattern, as in `test_smoke_expected_recipient_and_locked_tab`.
    - For case `directory-ms-4`, a fake planner reply of `directory_lookup` with query `"pengurus kewangan"` prints a line starting `FAIL directory-ms-4 action=directory_lookup query_words`.
    - A reply with query `"Finance Manager"` prints `PASS directory-ms-4`.
    - The printed line never contains the query text.

- [ ] **Step 2: Run both files and watch the new tests fail.**
  - Run: `bash …/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_workspace_planner.py -q -p no:cacheprovider -k english_job_title`
  - Then run `tests/test_assistant_smoke.py`.
  - Expected: FAIL, because the line is missing and the case id is unknown or the field doesn't exist.

- [ ] **Step 3: Implement.**
  - **Prompt:** add the line to the `directory_lookup` bullet, as the bullet's last sentence.
  - **Smoke check:** add the `Expect.query_words` field, and the check in `run_case` after the `not_actions` check.
  - **Two cases**, in `smoke_cases.json` after `directory-ms-3`:
    - `directory-en-4`: message `"Email the finance manager about the budget"`, preset `staff_basic`, actions `["directory_lookup", "clarification"]`, not_actions `["email_compose"]`, `query_words` `["finance", "manager"]`;
    - `directory-ms-4`: message `"Tolong emel pengurus kewangan tentang bajet"`, with the same expect.
  - **Smoke docstring:** update the "about N model calls, up to M" line by +2/+4.

- [ ] **Step 4: Update the docs.**
  - **`docs/ASSISTANT_RETEST.md`:**
    - In the Directory table, add two rows with the messages above, HR active, Expected "A directory lookup for the finance manager (searched in English) or a question, no email card", and ids `directory-en-4` and `directory-ms-4`.
    - Add one manual row: "Find Aina Rahman" with an employee named "Nur Aina binti Rahman", Expected "Nur Aina binti Rahman is listed".
    - In the FAIL-line list, add `query_words` (the lookup query missed an expected word; the query itself is never printed).
  - **`docs/PROGRESS.md`:** add a top entry in the style of the entries below it. Include:
    - the branch and spec;
    - the owner decisions (English titles, approach 1);
    - what changed (every word, any order, name, title or email; name matches first; the planner's English-title line; the two smoke cases and `query_words`);
    - what is unchanged;
    - the test counts.

- [ ] **Step 5: Run the changed test files, one at a time.**
  - The files: `tests/test_workspace_planner.py`, then `tests/test_assistant_smoke.py`, then `tests/test_workspace_people.py`.
  - Expected: all pass, including `test_every_smoke_case_is_in_the_checklist` and `test_smoke_cases_parse_and_cover_features`.
  - Then run `make lint` (through the wrapper). Expected: clean.

- [ ] **Step 6: Commit.**

  ```bash
  git add csa/workspace/planner.py scripts/assistant_smoke.py csa/workspace/smoke_cases.json docs/ASSISTANT_RETEST.md docs/PROGRESS.md tests/test_workspace_planner.py tests/test_assistant_smoke.py
  git commit -m "feat(workspace): planner searches job titles in English; smoke checks lookup query words"
  ```

  End the message with the Co-Authored-By line.
