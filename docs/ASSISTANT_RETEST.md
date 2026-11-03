# Workspace assistant retest checklist

Run this after every staging rollout that touches the assistant (see `deploy/STAGING_PIPELINE.md`,
"Routine rollouts"). Type each prompt into the assistant dock on staging as a staff member of the
staging test workspace, unless the setup column says otherwise. No step below sends a real email
unless you press Send on a card; press Cancel or Export instead.

## The model smoke run

The smoke run covers the rows with a smoke id. It runs the real model on synthetic facts only (no
database, Gmail or tenant data) and prints one line per case, with no prompts or model text:

```bash
docker exec csa-staging-gateway-1 python -m scripts.assistant_smoke
```

- `--case <id>` runs one case (repeat the flag for more); `--repeat N` samples each case N times
  and prints `RATE <id> k/N`.
- It ends with `N passed, M failed`. Exit 0 is all passed, 1 is a failure, 2 is a setup error: no
  model key in the container, the model unavailable on the very first call (the run stops there),
  or an unknown case id.
- The cases live in `csa/workspace/smoke_cases.json`. A unit test checks that every case id appears
  in this checklist and that every id cited here is a case.
- A run is about 59 model calls (one planner call per case plus one drafting call per draft case),
  up to about 107 when cases are re-planned.

**What a PASS checks, and nothing more.** Each case goes through the turn's own correction loop (at
most two planner calls) with the checks the turn makes before it touches a database:

1. The recipient check: every recipient, cc and bcc is an address written in the message or an
   employee id the facts offered (`my_manager`). A reply goes only to a synced thread. A directory
   query is not blank, and clarification options are within their limits. A plan refused once is
   re-planned with the server's correction; refused twice, the case fails.
2. The content check: on an email turn, a question for the subject or wording is re-planned once;
   asked twice, the case fails.
3. The action is one of the case's allowed actions and not a forbidden one.
4. A navigate reaches a tab this user can open; in a "locked" case it reaches one the user cannot
   open, so the turn answers with the locked text.
5. Where the case says so: no type-it-yourself option (such as "Enter address"), a Send card or a
   draft card by the server's send rule, the email going to exactly the address in the message, and
   a draft from the real drafting call that passes the saved-draft contract.

It does not check any wording, which tab or person was chosen beyond the above, the directory, the
mailbox or anything the console shows. In the tables below, the Expected column of a row with a
smoke id says only what a PASS checks; when you retest such a row by hand, also read the answer.

**Reading a FAIL line.** It names the fields that failed: `actions` (not an allowed action),
`not_actions` (a forbidden action), `recipient` (a recipient the user did not ask for, twice, or not
the expected address), `thread` (a reply to an unsynced thread, twice), `query` (a blank directory
query, twice), `options` (an over-long option, twice), `contract` (invalid output twice, or with no
correction), `content_question` (asked for the content twice), `locked` (a navigate to the wrong
side of the tab rule), `placeholder_options` (a type-it-yourself option was offered; the server
drops these, so this is prompt drift), `query_words` (the directory query missed a word the case
expects, such as a job title searched in Malay instead of English; the query is never printed), `fields` (a people action's field is not the case's value; never printed), `send` (Send where a draft was expected, or the reverse),
`draft` (the drafting call failed or its draft broke the contract) or `model` (the provider failed
after the first call). `replanned` means the one correction was used. The action is the model's own
plan (`invalid_output` when there was none).

Rows marked "manual" depend on staging state or on the console, so only a person can check them.
Tick each row when the result matches.

## Navigate

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Open the HR tab | HR active | A navigate to a tab the user can open | `navigate-en-1` |
| Tolong buka tab HR untuk saya | HR active | A navigate to a tab the user can open | `navigate-ms-1` |
| buka tab HR | HR active | A navigate to the HR tab (short Malay sentence with no verb the old guess knew; the answer line after it is Malay) | `navigate-ms-3` |
| Open the email assistant tab | Read-only member | An answer, or a navigate to a tab the role cannot open; PASS checks only that such a navigate is locked, not the wording | `navigate-en-2` |
| Any prompt | Read-only member | No assistant dock is shown: read-only members cannot post turns, so `navigate-en-2` has no hand retest | manual |
| Tolong buka tab langganan untuk saya | Subscriptions service not active, HR tab open | An answer, or a navigate to a locked tab; PASS checks only that such a navigate is locked, not the wording (check that by hand with the "Buka tab langganan" row below) | `navigate-ms-2` |
| Open HR directory | HR active, the member has the directory switch | The dock says it opened People & HR, and the browser lands on People (`/portal/hr/people`) through the old `?section=directory` redirect. A member without the directory switch lands there too and sees the no-access notice | manual |
| Open subscriptions | Subscriptions service not active | No tab opens and there is no "Opened" line; the answer says the tab needs the service and to ask an administrator | manual |
| Buka tab langganan | Subscriptions service not active | Tiada tab dibuka; jawapan dalam Bahasa Melayu tentang perkhidmatan yang tidak aktif | manual |

## Answer

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| What can you help me with in this console? | none | An answer | `answer-en-1` |
| Apa yang awak boleh bantu saya di sini? | none | An answer | `answer-ms-1` |
| Who is my manager? | Staff linked to an employee with a manager | "Your manager is ..." | manual |
| Siapa pengurus saya? | Staff linked to an employee with a manager | "Pengurus anda ialah ..." | manual |

## Directory

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Find Danial Hakim in the company directory | HR active | A directory lookup with a non-blank query | `directory-en-1` |
| Tolong cari Danial Hakim dalam direktori syarikat | HR active | A directory lookup with a non-blank query | `directory-ms-1` |
| Send an email to Razak about the invoice | HR active | A directory lookup or a question, no type-it-yourself option, no email card | `directory-en-2` |
| Tolong hantar emel kepada Razak tentang invois itu | HR active | A directory lookup or a question, no type-it-yourself option, no email card | `directory-ms-2` |
| send email to danial | HR active (the owner's original failure) | A directory lookup or a question, no type-it-yourself option, no email card | `directory-en-3` |
| hantar emel kepada danial | HR active | A directory lookup or a question, no type-it-yourself option, no email card | `directory-ms-3` |
| Email the finance manager about the budget | HR active | A directory lookup that searched for the finance manager in English, or a question; no email card | `directory-en-4` |
| Tolong emel pengurus kewangan tentang bajet | HR active | A directory lookup that searched for the finance manager in English ("Finance Manager", not "pengurus kewangan"), or a question; no email card | `directory-ms-4` |
| Send an email to Razak about the invoice | HR active, a Razak in the directory | Candidates to pick; picking one continues the email | manual |
| Find Aina Rahman | HR active, an employee named Nur Aina binti Rahman | Nur Aina binti Rahman is listed (words in any order, parts of the name are enough) | manual |
| Emel pengurus kewangan | HR active, an employee whose job title is Finance Manager | That employee is listed to pick | manual |

## People & HR screens

The 1a screens have browser scenarios for the access page and the training flow, and 1b one for a profile's Personal and Documents sections with Mark as leaving and Undo (`tests/test_hr_access_browser.py`), not for every row below, so a person checks them on staging. Sign in as the HR owner unless the setup says otherwise.

| Check | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Open People & HR | HR owner | Tabs for what the owner may use (Home, People, Leave & attendance and Settings always; Recruitment, Payroll, Training and Requests when granted), each with its own address. The old "More HR tools" button is gone | manual |
| Open `/portal/hr?section=training`, `?section=attendance` and `?section=workflows` | HR owner | Each lands on Training, Leave & attendance and Requests | manual |
| Open People, search "finance" and filter by department | At least 3 people, one with the job title Finance Manager | The list narrows; "Clear search and filters" restores it; 50 rows a page with "Load more" | manual |
| Add a person with a new department | Holder of Manage people, or the HR owner | The person appears under People, the department is in the filter and on Departments | manual |
| Edit a person in two tabs, save in the first, then the second | HR owner | The second shows "Someone changed this person since you opened it. Reload to see their change." and keeps nothing | manual |
| Open a person's History | After an edit | The edit is listed with who made it and each changed field, old to new | manual |
| Link a console account on a person's Access section | HR owner, a member without a person record | The account shows on the profile; linking the same account to a second person is refused | manual |
| Open People | Member with only the staff defaults | People shows the colleagues' basic details; no Left filter, no Add person, no Access or History sections | manual |
| Open People | Member whose directory access is a hand-made partial grant | No People tab; opening `/portal/hr/people` shows the no-access notice | manual |
| Open People & HR | Member with no HR duties | Home is My HR: their own profile, leave balances and training requests | manual |
| Turn on Sensitive details for a member on Settings → People & access | HR owner, a member with the Staff preset | "Sensitive details" is a switch under Responsibilities and no preset turns it on; after saving, the member sees a Personal section on other people's profiles | manual |
| Open a person's Personal section | HR owner, a person with an IC number and a bank account number saved | Each number shows as `•••• ` and its last 4 characters; Show reveals that one number and Hide masks it again; reloading the page masks it | manual |
| Open a person's profile | Member with Manage people but not Sensitive details | No Personal section; Documents is there | manual |
| Open My HR and edit my personal details | Member linked to a person, no HR duties | Only home address, personal phone and emergency contact can be changed ("HR keeps the rest"); the saved change shows in that person's History as "Changed Home address" with no value | manual |
| Upload a PDF on a person's Documents, then download it | HR owner | "Document uploaded."; the list shows Contract, the file name, its size and the date; Download gives the same file | manual |
| Upload a file renamed from `.png` to `.pdf` | HR owner | Refused with "Choose a PDF, JPG, PNG or Word (.docx) file."; nothing is listed | manual |
| Open Documents as each viewer | A person with a Contract and an Other document | The HR owner and Manage people holders see both; the person (My HR → My documents) sees the Contract only, with no Upload; their manager and colleagues have no Documents section | manual |
| Mark a person as leaving with a last day a week ahead, then Undo leaving | HR owner | The profile says "Leaving on <date> (Resigned)" and People lists them as Leaving; Undo leaving says "Leaving undone. They stay on." and they are Active again | manual |
| Mark a person with a report and a linked console account as leaving, last day today; check again the next day | HR owner | Today: still Leaving and can still sign in; the report now has the chosen manager. The next day: Left (under the Left filter), History shows the change by "System", the member's HR access switches are gone and they can no longer open the company | manual |
| Mark yourself as leaving | HR owner, on their own profile | "The HR owner can't be marked as leaving. Ask your tenant admin to move HR ownership to someone else first." and nothing changes (the Malay chat says "Pemilik HR tidak boleh ditandakan sebagai akan berhenti. Minta pentadbir syarikat anda memindahkan pemilikan HR kepada orang lain dahulu.") | manual |
| Mark a colleague who has Approves (or any HR access) as leaving | Member with Manage people | "Only the HR owner can mark someone with HR access as leaving." and nothing changes | manual |
| Rehire a person who has left | HR owner, a Left person | Rehire with a start date says "Rehired. Invite them again from Settings to give console access."; the person is Active with no access switches | manual |
| Open People & HR Home | HR owner | No "HR chat (assistant & voice)" block; the assistant dock is the only assistant | manual |

## Knowledge base add-on

| Check | Setup | Expected | Kind |
|---|---|---|---|
| Customer support only | A company with Customer support and no Knowledge base add-on | No Knowledge base in the menu; asking the assistant to open it says it isn't available | manual |
| A customer question at that company | Message the company's bot with a question about a policy, in `auto` and again in `suggest` | The customer gets the handoff message straight away in both, staff get a normal-priority ticket, and the bot stays quiet for that customer until staff act | manual |
| A mixed question, with Orders but no Knowledge base | Ask for a product's price and its warranty | The bot answers the price and says the team will follow up on the rest; no ticket opens | manual |
| Activate the add-on | Platform admin, Services page for that company | Knowledge base shows as an add-on; after activating, the menu entry appears | manual |
| Files after activating | Upload a file, wait for it to process, ask the bot about it | The bot answers from the file. The company must be `pilot` or `active`, or the file stays queued | manual |
| Existing company after the rollout | A company that had Customer support before migration 0530 | Its Knowledge base entry, files and bot answers still work, and Services shows the add-on with the same status as Customer support | manual |

## Moving the HR owner

The browser scenario `test_tenant_admin_moves_hr_owner` covers the panel against a stubbed API (the choice, the confirmation, a lost answer and its retry, `try_again`, `owner_changed`, `not_sent` and `repeat`). A person checks the rest on staging, with emails on and at least four members: a tenant admin who is not the HR owner, a colleague, a read-only member and the current HR owner.

| Check | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Set the first HR owner | Company with the HR package and no HR owner, signed in as a tenant admin | The Team page says "No HR owner yet"; **Set HR owner**, choose a colleague, Confirm; the panel names them and says "HR owner changed." | manual |
| Move to a colleague | Tenant admin, a colleague who has signed in and changed their password | The confirmation names the old owner and says they lose the powers at once and payroll stays off; after Confirm the panel names the colleague | manual |
| Move to yourself | Tenant admin who is not the HR owner | The confirmation adds "You are making yourself the HR owner."; after Confirm the panel names you and the People & HR Settings page opens for you | manual |
| Choose a read-only member | Tenant admin | The read-only member is not in the picker; after changing their role to Staff they are | manual |
| Choose a member who has not changed their password, or is marked as leaving | Tenant admin | They are in the picker; Confirm is refused with "This person can't be the HR owner yet. They need to sign in and change their password first." or "This person is leaving, so they can't be the HR owner. Choose someone else." and the owner does not change | manual |
| Ask for the HR owner as someone who is not a tenant admin | A staff or read-only member calling `GET /console/v1/team/hr-owner` | 403 "tenant administrator required"; the Team page shows no panel | manual |
| Emails received | Mail on; old owner, new owner and two tenant admins | Each address gets exactly one email (a tenant admin who is also the new owner gets one); it names the company, who made the change, old and new owner and the time in the company's timezone, with no link and no HR data | manual |
| The old owner after the move | Old owner signed in on the access page in another tab | Their next action there is refused and the access page is gone on reload; an assistant card made before the move is refused on Confirm | manual |
| The new owner's powers | New owner signed in | They open the access page, approve a training request for a colleague, and see candidates; saving their own switches on the access page keeps their owner rows | manual |
| Payroll stays off | New owner | No Payroll tab and no payroll private area until they turn it on on the access page; the change shows in Recent changes | manual |
| Recent changes | HR owner, on the access page | A line "<actor> made <new owner> the HR owner, replacing <old owner>" | manual |
| Move by a platform admin | Platform admin, the admin Team page for the company | The same panel; the emails and Recent changes name the actor "FourStacks admin" | manual |
| Two admins at once | Two tenant admins with the Team page open; the first moves the owner | The second Confirm is refused with "The HR owner changed since you opened this page. Reload to see who it is now." and the panel then names the new owner | manual |
| Move the old owner who has left | An owner whose account is disabled | The move still works and the old owner is still emailed | manual |
| Mark the HR owner as leaving, then move ownership, then mark them again | HR owner on their own profile | Refused in English and in Malay with the tenant-admin sentence; after the move the same action succeeds | manual |

## People actions

The dock adds, updates and marks people as leaving through a card (nothing changes until
Confirm), answers who reports to someone or who is in a department, and opens a profile. Only the
HR owner and Manage people holders get the changing cards. A PASS checks the action and, where the
row names them, the fields the model wrote (compared without case), never the server's resolution.

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Add Danial Hakim as Sales Executive in Sales starting 1 December 2026 under Razak | HR owner | An add with display_name Danial Hakim, job_title Sales Executive, department Sales, manager Razak | `hr_add-en-1` |
| Tolong tambah Danial Hakim sebagai Sales Executive dalam jabatan Sales bermula 1 Disember 2026 di bawah Razak | HR owner | The same add, from Malay | `hr_add-ms-1` |
| Add Danial as Sales Executive | Member with only the directory view | An answer, no add (the server refuses the card anyway) | `hr_add-en-2` |
| Change Siti's manager to Razak | HR owner | An update with person Siti and manager Razak | `hr_update-en-1` |
| Tukar pengurus Siti kepada Razak | HR owner | The same update, from Malay | `hr_update-ms-1` |
| Mark Aina as leaving, last day 30 November 2026, she resigned | HR owner | A leave with reason resigned | `hr_leave-en-1` |
| Tandakan Aina akan berhenti, hari terakhir 30 November 2026, dia letak jawatan | HR owner | The same leave, from Malay | `hr_leave-ms-1` |
| Who reports to Razak? | Any People viewer | A people query with reports_to Razak | `people_query-en-1` |
| Siapa yang melapor kepada Razak? | Any People viewer | The same query, from Malay | `people_query-ms-1` |
| Open Siti's profile | Any People viewer | An open with person Siti | `hr_open-en-1` |
| Buka profil Siti | Any People viewer | The same open, from Malay | `hr_open-ms-1` |
| Add Danial Hakim as Sales Executive in Sales starting 1 December 2026 under Razak | HR owner, a Razak and a Sales department in People | An "Add Danial Hakim to People" card listing name, job title, department, manager (Razak's full name) and start date; Confirm adds him, with a History entry; confirming again changes nothing | manual |
| Add Danial Hakim as Sales Executive | HR owner | One question: when do they start; answering it gives the card | manual |
| Change her manager to Razak | HR owner, on Siti's profile | An "Update Siti …" card with "Manager: <old> → Razak …" | manual |
| Change Siti's manager to Razak | HR owner, two people named Siti | A question with both, each as "name — title"; picking one gives the card | manual |
| Change Siti's title to Senior Clerk, then edit Siti on her profile before pressing Confirm | HR owner | Confirm says the card is out of date; nothing changes | manual |
| Mark Aina as leaving on 30 November 2026 | HR owner | A question for the reason with five choices; the card shows last day, reason and "Reports' new manager" (Aina's own manager) | manual |
| Add Danial as Sales Executive | Member without Manage people | An answer that changing people needs the HR owner or Manage people; no card | manual |
| Who reports to Razak? | Member with no People access | An answer that the access doesn't include the People list; no names | manual |
| Open Siti's profile | Any People viewer | The browser opens Siti's profile (`/portal/hr/people/<id>`; platform admins `/hr/people/<id>?client_id=…`) | manual |

## Compose and send

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Send an email to aina.rahman@example.com saying the meeting has moved to 3pm | Gmail connected | An email to exactly that address, a Send card, a valid draft | `compose-en-1` |
| Tolong hantar emel kepada aina.rahman@example.com bahawa mesyuarat dipindahkan ke pukul 3 petang | Gmail connected | An email to exactly that address, a Send card, a valid draft | `compose-ms-1` |
| hantar emel kat danial@fourstacks.my pasal mesyuarat esok | Gmail connected | An email to exactly that address, a Send card, a valid draft; the sentence has an address in it and must still read as Malay | `compose-ms-3` |
| Email my manager that I will be late tomorrow morning | Gmail connected, staff linked with a manager | An email to an offered recipient (the manager), a Send card, a valid draft | `compose-en-2` |
| Tolong emel pengurus saya bahawa saya cuti esok | Staff with no employee link | An answer, no email card | `compose-ms-2` |
| send email to danial@fourstacks.my about the meeting | Gmail connected (the owner's 500) | An email to exactly that address, a Send card, a valid draft | `compose-en-3` |
| send email to danial@fourstacks.my about the meeting | Gmail connected | A Send card, never a 500; when the drafting model fails, a plain sentence to try again | manual |

## Draft

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Draft an email to aina.rahman@example.com asking for the March sales report | Gmail connected | An email to exactly that address, a draft card (not Send), a valid draft | `draft-en-1` |
| Tolong draf emel kepada aina.rahman@example.com untuk minta laporan jualan bulan Mac | Gmail connected | An email to exactly that address, a draft card (not Send), a valid draft | `draft-ms-1` |

## Reply

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Reply to Aina about the invoice and say we will pay on Friday | Gmail synced, a thread from Aina | A reply in a synced thread with no unrequested recipients; never a new email | `reply-en-1` |
| Tolong balas kepada Aina tentang invois itu dan kata kami akan bayar pada hari Jumaat | Gmail synced, a thread from Aina | A reply in a synced thread with no unrequested recipients; never a new email | `reply-ms-1` |
| Reply to Aina about the invoice | Gmail synced, a thread from Aina | The reply card is in Aina's invoice thread | manual |
| Reply to Zulkifli about the contract | Gmail synced, no thread from anyone of that name | "I couldn't find that conversation in your synced email." | manual |

## Team

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Email my team that the office closes at 5pm on Friday | Team approver with reports | A team email (not a single email), a valid draft | `team-en-1` |
| Tolong emel pasukan saya bahawa pejabat akan tutup pukul 5 petang pada hari Jumaat | Team approver with reports | A team email (not a single email), a valid draft | `team-ms-1` |
| Email my team that the office closes at 5pm on Friday | Team approver, Gmail connected, one report without a work email | One card to the reports; the one with no work email is listed as left out | manual |
| Email my team about the new roster | Staff who is not a team approver | The answer that emailing the team is for team approvers; no card | manual |

## Attention

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| What needs my attention in my email today? | Gmail synced | An inbox summary | `attention-en-1` |
| Apa emel yang perlu perhatian saya hari ini? | Gmail synced | An inbox summary | `attention-ms-1` |
| What needs my attention in my email today? | Gmail synced | A list of what needs attention, or "Nothing in your synced email needs your attention right now." | manual |
| What needs my attention in my email? | Gmail connected, never synced | "Your email hasn't been synced yet. Open the Email assistant tab and press Sync inbox, then ask again." | manual |
| Apa emel yang perlu perhatian saya? | Gmail connected, never synced | "E-mel anda belum disegerakkan. Buka tab Email assistant dan tekan Sync inbox, kemudian tanya semula." | manual |

## Training

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Request a first aid training course for me | Staff linked with training access | A training request or a question, no type-it-yourself option | `training-en-1` |
| Tolong mohon kursus latihan pertolongan cemas untuk saya | Staff linked with training access | A training request or a question, no type-it-yourself option | `training-ms-1` |
| I want to request a fire safety training | Staff with no employee link | An answer, no training request | `training-en-2` |
| Saya nak mohon latihan keselamatan kebakaran | HR service not active | An answer, no training request | `training-ms-2` |
| Saya nak mohon kursus latihan pertolongan cemas | Staff with no employee link | An answer, no training request | `training-ms-3` |
| i want to send a request for ComptiaA+ training | Staff with no employee link (the owner's staging phrase) | An answer, no training request and no email action | `training-en-3` |
| I want to request a fire safety training | Staff with no employee link | The answer gives the reason (no linked employee record) | manual |

## Inbox decision

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Approve this reply | Customer conversation open with a held reply | An approve card for that held reply | manual |
| Tolak balasan ini | Customer conversation open with a held reply | Kad tolak untuk balasan itu | manual |

## Don't send

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Write an email to aina.rahman@example.com about Friday's meeting but don't send it yet | Gmail connected | An email to exactly that address, a draft card (never Send), a valid draft | `dont_send-en-1` |
| Tolong tulis emel kepada aina.rahman@example.com tentang mesyuarat itu dan jangan hantar dulu | Gmail connected | An email to exactly that address, a draft card (never Send), a valid draft | `dont_send-ms-1` |
| Tulis emel kepada aina.rahman@example.com tentang mesyuarat hari Jumaat, jangan hantar lagi | Gmail connected | A draft card, never Send; the card, its buttons and the sentence above it are in Malay | manual |

## States and failure paths

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Send an email to aina.rahman@example.com saying hello | No mailbox: Gmail never connected (no mailbox row) | No card: the turn is refused with a sentence telling you to connect Gmail; no draft is saved | manual |
| Send an email to aina.rahman@example.com saying hello | Gmail connection pending or revoked | No card: the turn is refused with the same sentence; no draft is saved | manual |
| Tolong hantar emel kepada aina.rahman@example.com untuk ucap terima kasih, sent after an earlier Malay message in the same conversation | No mailbox: Gmail never connected (no mailbox row) | No card: the turn is refused with the sentence telling you to connect Gmail, in Malay; no draft is saved | manual |
| Tolong hantar emel kepada aina.rahman@example.com untuk ucap terima kasih, as the very first message of a new conversation | No mailbox: Gmail never connected (no mailbox row) | No card: the turn is refused with the same sentence in English, because no turn exists yet to give the panel a language (a known limit); no draft is saved | manual |
| What needs my attention in my email? | No mailbox: Gmail not connected | "Connect your Gmail on the Email assistant tab first." | manual |
| I want to request a fire safety training | No employee link | An answer, no training request | `training-en-2` |
| Request a first aid training course for me | No employee link | An answer with the reason. If the model proposes a training request anyway, no card is created: the turn is refused with a sentence that your login isn't linked to an employee record, and no training request is created | manual |
| Send an email to Zulaikha Osman about the invoice | No directory match: nobody of that name in HR | "I couldn't find that person in the directory. Type their email address and I'll continue." Typing an address then gives a Send card | manual |
| Tolong hantar emel kepada Zulaikha Osman tentang invois | No directory match | "Saya tidak menjumpai orang itu dalam direktori. Taip alamat e-mel mereka dan saya akan teruskan." | manual |
| Send an email to aina.rahman@example.com saying the meeting has moved to 3pm | An address in the request | An email to exactly that address and no other recipient | `compose-en-1` |
| Tolong hantar emel kepada aina.rahman@example.com bahawa mesyuarat dipindahkan ke pukul 3 petang | An address in the request | An email to exactly that address and no other recipient | `compose-ms-1` |
| Write an email to aina.rahman@example.com about Friday's meeting but don't send it yet | "Don't send" | A draft card, never Send | `dont_send-en-1` |
| Tolong tulis emel kepada aina.rahman@example.com tentang mesyuarat itu dan jangan hantar dulu | "Don't send" | A draft card, never Send | `dont_send-ms-1` |
| Tolong hantar emel kepada Danial, then, when asked, type only danial@fourstacks.my | Gmail connected, a new conversation | The question and the reply to the typed address stay Malay: the typed address does not switch the panel to English, and the Send card that follows is Malay | manual |
| Tolong hantar emel kepada aina.rahman@example.com bahawa mesyuarat dipindahkan ke pukul 3 petang, then press Hantar | Gmail connected | The Send card's title, summary and field labels are Malay (Kepada, Subjek), its buttons and the status line after pressing (sending, sent) are Malay, and the email preview's labels are Malay. The address, the subject and the body are shown exactly as they are | manual |
| Tolong hantar emel kepada aina.rahman@example.com bahawa mesyuarat dipindahkan ke pukul 3 petang, then press Batal | Gmail connected | The card's status line is Malay and its buttons are disabled | manual |
| A Malay conversation, then in the same conversation "Open the HR tab" typed in English | Gmail connected | The panel follows the new clear language: the next reply, its card and the dock's title, "New conversation" button and placeholder are English | manual |
| A Malay conversation, then reload the page | An earlier Malay turn | The reopened panel's title, placeholder and past cards are still Malay (the language is saved with each turn) | manual |
| Send an email to Razak about the invoice, then ask something else and go back to the first card | An old card's buttons disabled | Only the newest card's options and candidates can be clicked; the older card's are disabled | manual |
| Tolong hantar emel kepada Razak tentang invois, kemudian tanya soalan lain | An old card's buttons disabled | Hanya kad terbaru boleh diklik | manual |
| Any prompt while the model provider is down | Model unavailable (simulate only in a local stack) | One plain sentence saying the assistant is unavailable and to try again; no raw error code | manual |

## Forward-only data shape

Turns stored since the Malay rollout carry `result.language` and new cards carry `card.language`;
the previous images reject both (`extra="forbid"`), so a rollback to them breaks the turn list,
the planner history and proposal reads. If a rollback is ever needed, the owner first runs
`UPDATE public.console_workspace_turns SET result = (result - 'language') #- '{proposal,card,language}';`
and `UPDATE public.console_proposals SET card = card - 'language';`
(see `deploy/STAGING_PIPELINE.md`, "Forward-only data shape").
