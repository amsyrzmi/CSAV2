# Directory search by job title and any word order (S1) — short design

Date: 2026-11-01. Branch `fix/directory-search`, base main `0ef880d`. Follow-up S1 of the
assistant audit (`docs/superpowers/specs/2026-10-31-assistant-audit-fixes-design.md`).

## Intent

When staff ask the assistant to find or email a colleague, the search should find:
- people by job title ("email the finance manager");
- names typed in another order, or in part ("Hakim Danial" for Danial Hakim, "Aina Rahman" for
  Nur Aina binti Rahman).

**Today:** `db/sql/workspace/people_search.sql` matches the whole query as one substring of the name
or work email, and returns at most 10. The job title is returned but never searched, although the
planner prompt says "search the company directory by a person's name or job title". So title
requests get "No matching colleague", or the address question on an email turn (M5). Non-adjacent
name parts also miss.

## Owner decisions (2026-11-01)

- Job titles in the HR directory are written in English.
- **Approach 1:** a word-by-word match in the existing search.
- **The behaviour as played back and accepted:**
  - every word in any order;
  - name matches listed first;
  - several matches use the existing "Choose a colleague" list;
  - a Malay request searches with the English title.

## Goals

1. **Every word, any order.** The server splits the query into words, and each word must appear,
   case-insensitively, as a substring of the person's `display_name`, `job_title` or `work_email`.
   For example:
   - "Aina Rahman" finds "Nur Aina binti Rahman";
   - "Hakim Danial" finds "Danial Hakim";
   - "finance manager" finds a person titled "Finance Manager";
   - "Danial finance" finds Danial when his title contains "Finance".
2. **Name matches first.** People whose name or work email holds every word are listed before
   people who need the job title to match. Within each group the order stays `display_name`, then
   `employee_id`. The limit stays 10.
3. **The planner searches with the English title.** One line is added to the `directory_lookup`
   instruction in `csa/workspace/planner.py` `SYSTEM_PROMPT`:
   - the query holds only name words or the job title;
   - a job title is written in English, as the directory stores it, whatever the user's language
     (for example "pengurus kewangan" becomes "Finance Manager").
4. **Unchanged:**
   - what a result holds (`employee_id`, `display_name`, `job_title`, `work_email`);
   - the department, which is still never searched or returned;
   - who can search, the throttle, the endpoint's `q` bounds (1–80) and 422s;
   - the audit row (candidate count only);
   - the M5 address question and the send rule.

## Non-goals

- **Typo tolerance or fuzzy matching** (approach 2). It is a later option if staff hit it.
- **Sending the directory to the model** (approach 3).
- **The 10-result hint and the token limit.** They are S5.
- **Translating job titles.** Titles are English, so a Malay title is never searched as is.

## Anti-goals

- **More wrong-person risk on email.** Every word must match, so a longer query narrows the list,
  never widens it.
- **Wildcard injection.** `%`, `_` and `\` in a word are escaped as today, so
  `test_search_limit_wildcards_and_bounds` still holds.
- **A query that reaches the model or a log.** The audit stays count-only.

## Constraints

- No migration and no new dependency.
- `people_search.sql` keeps `client_id` as the first filter.
- `company_directory` is a `security_barrier` view, so filtering stays in SQL, not in Python after
  the `LIMIT`.
- Locally, run the changed test files one at a time, plus `make lint`. The full suite runs in PR CI.

## Approaches

1. **Word-by-word match in the existing search** (chosen). No migration, no wrong-person risk added.
   It does not forgive typos.
2. **Fuzzy search with Postgres trigram similarity.** It catches typos, but it needs a migration and
   an index, and it can put the wrong person on an email's colleague list.
3. **Give the planner the whole directory.** Rejected: every colleague's name and title would go to
   the model on every turn, and it does not scale.

## Left to the builder

- **How words are split.**
  - Use whitespace and commas. An address like `ali.h@example.test` stays one word.
  - Use at most the first 5 words, to bound the SQL.
  - A query with no words after splitting matches nothing. The endpoint's 422 and the planner's
    blank-query refusal already stop that.
- **How the SQL is written.** For example, an escaped pattern array with `unnest` and `NOT EXISTS`
  for "every word matches", plus an `ORDER BY` key for "name or email holds every word".
- **Tests** in `tests/test_workspace_people.py`. The `employee` helper gains a `job_title`
  argument, defaulting to `'Analyst'`. Cover:
  - a title-only match;
  - reversed and non-adjacent names;
  - a word in the name with a word in the title;
  - every word required (an extra unrelated word gives no match);
  - name matches ranked before title matches;
  - the existing wildcard and limit cases.

  A planner-prompt test pins the new instruction line.
- **Smoke cases.** Add an English and a Malay job-title case to `csa/workspace/smoke_cases.json`,
  expecting `directory_lookup`. If the smoke run can check the query cheaply, add an optional
  `expect.query_has` list (words that must appear in the lookup query, case-insensitively), so the
  Malay case checks "finance" and "manager".
- **Docs.** Add a retest row in `docs/ASSISTANT_RETEST.md` (a title lookup in English and in Malay)
  and a PROGRESS entry.
