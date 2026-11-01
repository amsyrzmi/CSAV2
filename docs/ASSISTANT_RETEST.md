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
- A run is about 46 model calls (one planner call per case plus one drafting call per draft case),
  up to about 81 when cases are re-planned.

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
drops these, so this is prompt drift), `send` (Send where a draft was expected, or the reverse),
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
| Send an email to Razak about the invoice | HR active, a Razak in the directory | Candidates to pick; picking one continues the email | manual |

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
| What needs my attention in my email? | Gmail connected, never synced | "Your email hasn't been synced yet. Open the Email assistant tab and press Sync, then ask again." | manual |
| Apa emel yang perlu perhatian saya? | Gmail connected, never synced | "E-mel anda belum disegerakkan. Buka tab Pembantu E-mel dan tekan Segerak, kemudian tanya semula." | manual |

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
