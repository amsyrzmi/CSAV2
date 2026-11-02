# People & HR, project 1: HR home, navigation and employee management — design

Date: 2026-11-01. Branch `feat/hr-people`, base main `0ef880d`. This is project 1 of the HR
programme ("Project 3, HR completeness" in
`docs/superpowers/specs/2026-10-27-client-growth-program-design.md` section 10).

## Intent

The owner wants an HR area that both a 5-person shop and a corporate HR team can use without
training: friendly, familiar and practical. Today People & HR is:
- **A row of buttons:** Training, Directory, HR requests, Automation and "More HR tools".
- **One long page of raw forms under "More HR tools".** Several of them ask for typed ids
  ("Employee reference", "Interviewer console member reference").
- **No employee management to speak of.** There is no profile page, no documents, no personal
  details and no leaving flow. Departments are free text.
- **Two assistants:** the workspace dock, and a separate HR chat inside "More HR tools".

The HR engine underneath is strong: grants and the B2 access switches, maker/checker payroll, and
immutable audits. Project 1 gives it a proper front door and real employee management. The later
projects then plug into the same navigation.

## The HR programme (owner, 2026-11-01)

The order the owner accepted:
1. HR home, navigation and employee management (this design).
2. Recruitment: openings, candidates, CV files and interview booking.
3. Onboarding, starting from a hire, together with the full offboarding checklist.
4. Leave self-service.
5. Payroll: runs, payslips and statutory items.
6. Claims, performance reviews and announcements.

**The workspace assistant is part of every project, not a separate one** (owner, 2026-11-01):
- **One assistant.** The dock is the only assistant, and the separate HR chat goes away.
- **Anything you can do on an HR screen, you can ask for.** The assistant prepares a card, and
  nothing changes until the person presses Confirm.
- **It knows where you are.** On a profile, "change her manager" means that person.
- **It never goes around access rules.** It prepares only what the person could do on screen.
- **Malay works** as in the rest of the assistant.
- **Each project adds the assistant actions for its own screens.**

## Owner decisions for project 1 (2026-11-01)

- **The proposal below is accepted as played back:** the sub-tabs, Home, People, Profile with five
  sections, leaving, the org chart, small-company and corporate behaviour, and the assistant
  actions.
- **Approach 1:** build on main now, using only the console's shared UI building blocks, so the
  unmerged console redesign (another session's branch `feat/console-redesign-phase-2`) restyles
  HR mostly by itself when it merges. Never touch that worktree.
- **Two PRs** (the split is my proposal, accepted):
  - **1a:** navigation, Home, the People list, profiles (Overview, Access, History), departments,
    the org chart, adding and editing a person, and My HR.
  - **1b:** personal details, documents, leaving, the assistant actions, and retiring the separate
    HR chat.

## Goals

### 1. Navigation

- **Sub-tabs inside People & HR,** each with its own URL, so the back button, bookmarks and the
  assistant's "open" all work. For example `/portal/hr`, `/portal/hr/people`,
  `/portal/hr/people/<id>`, `/portal/hr/recruitment`, `/portal/hr/settings`. Platform admins use
  the same paths under `/hr` with `?client_id=`.
- **Tabs and who sees them:**

  | Tab | Who sees it | In project 1 |
  |---|---|---|
  | Home | everyone with HR | built |
  | People | everyone with the directory switch (all staff by default; owner decision Q1) | built |
  | Recruitment | the recruitment area or recruitment workflow grants | today's recruitment tools, moved |
  | Onboarding | — | not shown until project 3 |
  | Leave & attendance | leave or attendance grants, or the HR owner | today's calendar, balance, attendance and shift tools, moved |
  | Payroll | the payroll area | today's payroll review tools, moved |
  | Training | training grants | today's Training tab, moved |
  | Requests | workflow grants | today's "HR requests", moved |
  | Settings | HR owner (and the HR team where noted) | today's access page, company features (automation switches), background maintenance, work calendar and private access |

- **No empty tabs.** A tab with nothing the person may do is hidden, not greyed.
- **"More HR tools" is removed.** Every tool in it moves into the tab it belongs to, with the same
  behaviour, until that tab's own project rebuilds it.
- **Typed ids give way to pickers.** Where a moved tool asks for a typed employee or member id, it
  becomes a picker of people the person may see. The tools are otherwise unchanged.
- **The platform admin's tenant picker stays,** above the tabs.

### 2. Home

A "needs you today" page, built only from data that already exists. Each item links to where it is
handled:
- training and HR requests waiting for this person's decision;
- the interviews this person is running, today and in the next 7 days (`my_interviews`);
- people starting in the next 14 days, and people whose last day is in the next 14 days (after
  1b);
- unread HR notifications.

Plus quick actions the person may use: "Add person", "Request training", "Open the access page".

For staff without HR duties, Home is **My HR** (Goal 6).

### 3. People list

- **Columns:** name, title, department, manager and status.
- **Search** by name, title or work email. The word-by-word rule from S1 is reused for
  consistency.
- **Filters:**
  - department;
  - manager;
  - status: Active, Starting (start date in the future), Leaving (last day set and not past,
    after 1b), Left (inactive).
- **Paging** past about 50. **"Add person"** for those allowed (Goal 5).
- **Who sees what** (owner decision Q1, plus my field rule):
  - **Everyone with People** sees each active person's basic fields: name, title, department,
    manager, work email, work phone, start date, employment type, and Active, Starting or Leaving
    status. This follows today's directory switch.
  - **The last day** is shown to the person's managers, the HR owner and Manage people holders.
  - **The leaving reason and the employment profile** (region, entity, category) are shown only to
    the HR owner and Manage people holders.
  - **Left people** (the Left filter) are shown only to the HR owner and Manage people holders.

### 4. Departments and org chart

- **Departments are a list per company** (a table), not free text. Employees point at one.
- The migration turns today's distinct free-text values into rows, case- and space-folded, and
  links the employees. Empty values stay without a department.
- The HR owner, and whoever may manage people, can add, rename or archive a department. An
  archived department keeps its people until they are moved.
- **The org chart** is a tree by manager, from the existing reporting lines. A person with no
  manager is a root. A loop is shown flat, with a warning for the owner.
- **Scope:** everyone who sees People sees the whole chart, with basic fields only (owner decision
  Q1). Left people are not shown.

### 5. Profile page (`/…/hr/people/<id>`)

- **Overview** (1a):
  - name, job title, department, manager, work email, work phone, start date and employment
    type (Full-time, Part-time, Contract, Intern);
  - employment profile (region, legal entity and category, as used by payroll matching);
  - active status.
- **Personal** (1b), visible only to the HR owner, Sensitive details holders, and the person
  themselves:
  - IC or passport number;
  - date of birth;
  - home address;
  - personal phone;
  - emergency contact (name, relationship, phone);
  - bank name and account number for payroll.

  Kept apart from the directory data, because PDPA applies. The IC or passport number and the
  bank account number are also encrypted at rest with the existing `csa/core/crypto.py` AES-GCM
  key (my call). So they can't be searched, and they are shown masked until "Show" is pressed;
  each Show is audited.
- **Documents** (1b):
  - **What can be uploaded:** files of kind Contract, Identity, Certificate or Other, as PDF, JPG,
    PNG or DOCX, at most 10 MB each.
  - **Who sees them:** the HR owner and those who may manage people see all. The person sees
    their own Contract and Certificate files, and their Identity files too.
  - **Upload and remove:** done by those who may manage people. Removing is soft: the row is kept
    and the file deleted. Both are audited.
- **Access** (1a): the person's switches, read-only, as on the access page, with a link to change
  them there (owner only).
- **History** (1a): every change to the person, by whom and when.
  - For directory fields, it shows old and new values.
  - For personal fields, it shows which field changed, never the values.
  - For documents, it shows the kind and the file name.

**Editing:**
- **Overview fields:** edited in place by those who may manage people. Each save carries the
  record version; a stale save is refused with "Someone changed this person since you opened it.
  Reload to see their change."
- **Personal fields:** edited by Sensitive details holders and the HR owner. The person may edit
  their own home address, personal phone and emergency contact (1b). Bank details and IC are
  HR-only edits, because they feed payroll.

### 6. My HR (staff without HR duties)

- My profile (Overview, and my Personal details after 1b).
- My leave balances (read-only, from existing balances).
- My training requests (the existing flow).
- My documents (after 1b).

This is Home for anyone without HR duties. HR duties means:
- holding any switch beyond `directory` and `own_requests` (`approves_team`, `hr_team`,
  `recruitment`, `attendance`, `payroll_prepare`, `payroll_review`, `manage_people`,
  `sensitive_details`); or
- being the HR owner.

Staff without HR duties still have the People tab.

### 7. Adding a person, and leaving

- **"Add person"** is a short form:
  - **Required:** name, job title and start date.
  - **Manager:** a person, or "No manager (top of company)".
  - **Department:** chosen from the list, created inline ("New department…"), or left empty.
  - **Optional:** work email, work phone and employment type.

  It replaces the Advanced employee form, which goes away. A console account can be linked
  later, from Access.
- **Leaving** (1b):
  - **Marking:** "Mark as leaving" records a last day and a reason (Resigned, Contract ended,
    Dismissed, Retired, Other with text). It can be undone until the last day.
  - **Their direct reports:** "Mark as leaving" also asks for the reports' new manager. It
    defaults to the leaver's own manager, or "No manager", and the move applies on the last day.
    This keeps training approvals and team access working.
  - **The HR owner:** cannot be marked as leaving until another HR owner is recorded. The form
    says so, with the next step.
  - **On the day after the last day,** the background maintenance worker does all of this, and the
    audit records each step:
    - sets the employee inactive;
    - removes their HR grants and switches;
    - removes their console membership for this company (owner decision Q3). Other companies'
      memberships are untouched.
  - **Rehire:** the HR owner and Manage people holders can rehire a Left person. That makes them
    Active again with a new start date. Console access comes back through a new invitation, not
    automatically.
  - The full offboarding checklist (assets, final pay, exit interview) is project 3.

### 8. Assistant actions (1b)

These are new workspace planner actions, each prepared as a card that needs Confirm, under the
same authority as the screen:
- **Find someone:** the existing `directory_lookup`, with the S1 search.
- **Open a profile:** `navigate` to `hr` section `people` with the employee record. The panel
  opens the profile URL.
- **"Who reports to Siti" / "who is in Finance":** an answer from the list rules above, within the
  person's directory scope.
- **Add a person:** "add Danial as Sales Executive in Sales starting 1 Dec under Razak" becomes an
  "Add person" card with the same fields and checks as the form. Missing required fields get a
  clarification.
- **Update a person:** a card with old and new values for title, department, manager, work phone,
  employment type or start date ("change Siti's manager to Razak").
- **Mark as leaving:** a card with the last day and reason.
- **The page context:** on a profile, the context names that employee, so "change her manager to
  Razak" means them.
- **No personal fields and no documents through the assistant** in project 1. Bank and IC details
  never reach the model.
- **The separate HR chat is retired in 1b:**
  - the "Assistant & voice" screen is removed;
  - the `POST /console/v1/hr/assistant` route and its planner stay for one release, then are
    removed in a cleanup PR;
  - the dock's voice input is the deferred voice-mode follow-on.

## Non-goals

- Recruitment, onboarding and offboarding checklists, leave self-service, payroll runs or
  payslips, claims, reviews and announcements (projects 2 to 6).
- Translating the HR screens to Malay. The console stays English; the assistant follows the
  user's language.
- Any change to grants, the B2 switch rules or the maker/checker rules, except the two new
  switches (owner decision Q2).
- Bulk import of people from a spreadsheet (later; `hr_import_employee` exists).
- Calendar or email integrations.

## Anti-goals

- **Personal data shown or sent where it shouldn't be:**
  - in the directory, the People list, search results, the assistant, the model, logs or audit
    values;
  - to colleagues;
  - to managers. A manager sees their reports' Overview, never their Personal details or
    documents.
- **A screen that offers an action the server would refuse.** The tabs and buttons follow the same
  grants as the server, as the Directory tab does today.
- **A lost edit.** Concurrent edits are refused by version, never silently overwritten.
- **Moving a tool and changing what it does.** Tools that are only moved keep their behaviour and
  their tests.
- **A broken HR page** for a tenant with no HR owner, no employees or no departments yet. Each
  case gets a plain next step.

## Constraints

- **Stack:** Python 3.12, FastAPI, pydantic v2 (strict contracts), psycopg3, Postgres 16, Next.js
  console. Only the shared UI building blocks are used (`components/console/ui` and similar), with
  no new styling system.
- **Migrations** in the range 0520–0529:
  - TikTok holds 0490–0499, the redesign 0501–0502, booking 0510–0519.
  - `EXPECTED_SCHEMA_VERSION` follows the last merge (see the B2 note).
  - Each migration has a rollback comment.
  - No `FORCE ROW LEVEL SECURITY`, and no new database roles.
- **Owner-only writes** keep going through `hr_admin_apply` or an equivalent SECURITY DEFINER path
  with the same checks. New tables use tenant RLS like the existing HR tables.
- **Documents:**
  - stored like KB uploads (`kb_uploads.py`): a per-tenant directory under a new
    `HR_UPLOAD_ROOT`, a uuid file name, durable publish, a content hash, and a tenant- and
    grant-checked download route;
  - never served from a public path;
  - no virus scanning exists today. That is accepted for project 1 and noted in the docs.
- **Tests:** synthetic fixtures and disposable testcontainers only.
- **Local test runs:** run each changed test file on its own, plus `make lint` and the web
  `npm test`, lint and typecheck. The full suite runs in PR CI. New console tests are added to
  `scripts/check-isolated.mjs`.
- **Secrets:** never in code, docs, logs or chat.
- **Staging rollout:** 1a and 1b each carry a migration, so each rollout stops gateway, worker,
  delivery and frontend for the migrate step.

## Approaches

1. **Build on main now with the shared building blocks** (chosen, 2026-11-01). Nothing is
   blocked, and the redesign restyles HR through the shared pieces, plus a small layout pass after
   it merges.
2. **Wait for the redesign to merge first.** Rejected: the delay is unknown, and a long-lived
   branch would collide with other merges.
3. **Build HR on the redesign branch.** Ruled out: it is another session's work.

## Left to the builder

- **The exact table and column names:**
  - departments;
  - employee additions: work phone, start date, employment type, department id, last day,
    leaving reason;
  - the personal-details table;
  - the documents table;
  - the per-employee change history. This may extend existing audit tables, but it must hold old
    and new values for directory fields only.
- **The exact grant actions and the second write path** for `manage_people` and `sensitive_details`, within owner decision Q2.
- **The routing layout** in the Next.js app, and how the proxy allowlist covers the new paths.
- **Reading the existing data:** how Home's items are read, by reusing the records and context
  endpoints where possible.
- **Picker components** for people and members.
- **Malay planner text** for the new actions, matching the existing register.
- **Smoke cases** for the new assistant actions, in English and Malay, plus retest checklist rows.

## Owner decisions after the builder check (2026-11-01)

- **Q1, staff view:** everyone with the directory switch (all staff by default) sees the People
  list and the org chart, with basic fields only. Personal details, documents, the leaving reason
  and Left people stay with HR.
- **Q2, who manages people:** two new access switches on the B2 page, which the HR owner gives out.
  - **`manage_people` ("Manage people"):** add and edit profiles, departments and documents, mark
    people as leaving, rehire, and see Left people, leaving reasons and employment profiles.
  - **`sensitive_details` ("Sensitive details"):** see and edit IC or passport, bank details, home
    address, personal phone and emergency contact.

  Neither switch can change access; that stays HR-owner-only. Both need new grant actions, a
  widened `hr_grants` CHECK, and a second write path beside `hr_admin_apply` with the same checks
  for these actions only. The `hr_team` preset gains `manage_people`. `sensitive_details` is in no
  preset, so the owner gives it person by person.
- **Q3, leaving:** console membership for the company is removed automatically the day after the
  last day (Goal 7).

## Calls I made (for the owner to check)

1. **No manager is allowed** ("top of company"). The department can be empty or created inline
   from the Add person form.
2. **S1 (PR #61) merges first.** 1a starts from main with S1 in it, so the People search reuses its
   word-by-word rule.
3. **Who sees Left people:** only the HR owner and Manage people holders, with the Left filter,
   their History and their documents. Rehire is the way back.
4. **A leaver's direct reports** move to a manager chosen when marking leaving (default: the
   leaver's own manager). The HR owner can't leave until another owner is set.
5. **The assistant's name search stays company-wide,** as today (name, title, work email from the
   company directory). "Who reports to" and "who is in a department" answers follow the person's
   People access.
6. **IC or passport and bank account numbers are encrypted at rest** with the existing crypto key,
   masked until shown, and every Show is audited. Uploaded documents are stored like KB uploads,
   not encrypted on disk and with no virus scan. This is noted as a known limit.
7. **The leaving reason and employment profile are HR-only.** The last day is visible to managers.
