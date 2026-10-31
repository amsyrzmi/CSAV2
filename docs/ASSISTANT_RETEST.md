# Workspace assistant retest checklist

Run this after every staging rollout that touches the assistant (see `deploy/STAGING_PIPELINE.md`,
"Routine rollouts"). Type each prompt into the assistant dock on staging as a staff member of the
staging test workspace, unless the setup column says otherwise. No step below sends a real email
unless you press Send on a card; press Cancel or Export instead.

**The model smoke run covers the rows with a smoke id.** It runs the real model on synthetic facts
only (no database, Gmail or tenant data) and prints one line per case, with no prompts or model
text:

```bash
docker exec csa-staging-gateway-1 python -m scripts.assistant_smoke
```

- `--case <id>` runs one case (repeat the flag for more); `--repeat N` samples each case N times
  and prints `RATE <id> k/N`.
- It ends with `N passed, M failed`. Exit 0 is all passed, 1 is a failure, 2 is a setup error (for
  example no model key in the container).
- A `FAIL` line names the fields that failed: `actions` (the action is not one of the allowed
  ones), `not_actions` (a forbidden action), `send` (the card would be Send where a draft was
  expected, or the reverse), `placeholder_options` (the model offered a type-it-yourself option),
  `draft` (the drafting call failed or its draft broke the email contract), `content_question` (it
  asked for the subject or wording twice), `contract` (invalid output twice) or `model` (the
  provider failed). `replanned` means the server's one correction was used.
- The cases live in `csa/workspace/smoke_cases.json`; a unit test checks every case id appears in
  this checklist.

Rows marked "manual" depend on staging state or on the console, so only a person can check them.
For each row, tick it when the result matches.

## Navigate

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Open the HR tab | HR active | The HR tab opens; the dock says it opened it | `navigate-en-1` |
| Tolong buka tab HR untuk saya | HR active | Tab HR dibuka | `navigate-ms-1` |
| Open subscriptions | Subscriptions service not active | No tab opens. The answer says the tab needs the service and to ask an administrator, with no "Opened" line | manual |
| Buka tab langganan | Subscriptions service not active | Tiada tab dibuka; jawapan dalam Bahasa Melayu tentang perkhidmatan yang tidak aktif | manual |

## Answer

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| What can you help me with in this console? | none | A short answer in English, no card | `answer-en-1` |
| Apa yang awak boleh bantu saya di sini? | none | Jawapan ringkas dalam Bahasa Melayu, tiada kad | `answer-ms-1` |
| Who is my manager? | Staff linked to an employee with a manager | "Your manager is ..." | manual |
| Siapa pengurus saya? | Staff linked to an employee with a manager | "Pengurus anda ..." in Malay | manual |

## Directory

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Find Danial Hakim in the company directory | HR active | A directory lookup with candidates to pick | `directory-en-1` |
| Tolong cari Danial Hakim dalam direktori syarikat | HR active | Carian direktori dengan calon untuk dipilih | `directory-ms-1` |
| Send an email to Razak about the invoice | HR active, a Razak in the directory | Candidates to pick, or a question; never an "Enter address" option, never a card with a made-up address | `directory-en-2` |
| Tolong hantar emel kepada Razak tentang invois itu | HR active, a Razak in the directory | Calon untuk dipilih, atau soalan; tiada pilihan "Masukkan alamat" | `directory-ms-2` |

## Compose and send

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Send an email to aina.rahman@example.com saying the meeting has moved to 3pm | Gmail connected | A Send card to that address with a written subject and body | `compose-en-1` |
| Tolong hantar emel kepada aina.rahman@example.com bahawa mesyuarat dipindahkan ke pukul 3 petang | Gmail connected | Kad Hantar kepada alamat itu, dalam Bahasa Melayu | `compose-ms-1` |
| Email my manager that I will be late tomorrow morning | Gmail connected, staff linked with a manager | A Send card to the manager | `compose-en-2` |
| Tolong emel pengurus saya bahawa saya cuti esok | Staff with no employee link | An answer that the manager can't be found because the account has no employee link; no card | `compose-ms-2` |

## Draft

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Draft an email to aina.rahman@example.com asking for the March sales report | Gmail connected | A draft (Export) card, not Send | `draft-en-1` |
| Tolong draf emel kepada aina.rahman@example.com untuk minta laporan jualan bulan Mac | Gmail connected | Kad draf (Export), bukan Hantar | `draft-ms-1` |

## Reply

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Reply to Aina about the invoice and say we will pay on Friday | Gmail synced, a thread from Aina | A reply card in Aina's thread (or a question picking between her threads); never a new email | `reply-en-1` |
| Tolong balas kepada Aina tentang invois itu dan kata kami akan bayar pada hari Jumaat | Gmail synced, a thread from Aina | Kad balasan dalam bebenang Aina | `reply-ms-1` |
| Reply to Zulkifli about the contract | Gmail synced, no thread from anyone of that name | "I couldn't find that conversation in your synced email." | manual |

## Team

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Email my team that the office closes at 5pm on Friday | Team approver with reports that have work emails, Gmail connected | One team card to the reports; people with no work email listed as left out | `team-en-1` |
| Tolong emel pasukan saya bahawa pejabat akan tutup pukul 5 petang pada hari Jumaat | Team approver with reports, Gmail connected | Satu kad pasukan | `team-ms-1` |
| Email my team about the new roster | Staff who is not a team approver | The answer that emailing the team is for team approvers; no card | manual |

## Attention

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| What needs my attention in my email today? | Gmail synced | A list of what needs attention, or "Nothing in your synced email needs your attention right now." | `attention-en-1` |
| Apa emel yang perlu perhatian saya hari ini? | Gmail synced | Senarai dalam Bahasa Melayu | `attention-ms-1` |
| What needs my attention in my email? | Gmail connected, never synced | "Your email hasn't been synced yet. Open the Email assistant tab and press Sync, then ask again." | manual |
| Apa emel yang perlu perhatian saya? | Gmail connected, never synced | "E-mel anda belum disegerakkan. Buka tab Pembantu E-mel dan tekan Segerak, kemudian tanya semula." | manual |

## Training

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Request a first aid training course for me | Staff linked with training access | A training card, or a question about the training; no "Enter ..." option | `training-en-1` |
| Tolong mohon kursus latihan pertolongan cemas untuk saya | Staff linked with training access | Kad latihan, atau soalan | `training-ms-1` |
| I want to request a fire safety training | Staff with no employee link | An answer giving the reason (no linked employee record); no card | `training-en-2` |
| Saya nak mohon latihan keselamatan kebakaran | HR service not active | Jawapan dengan sebabnya; tiada kad | `training-ms-2` |

## Inbox decision

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Approve this reply | Customer conversation open with a held reply | An approve card for that held reply | manual |
| Tolak balasan ini | Customer conversation open with a held reply | Kad tolak untuk balasan itu | manual |

## Don't send

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Write an email to aina.rahman@example.com about Friday's meeting but don't send it yet | Gmail connected | A draft (Export) card, never Send | `dont_send-en-1` |
| Tolong tulis emel kepada aina.rahman@example.com tentang mesyuarat itu dan jangan hantar dulu | Gmail connected | Kad draf, bukan Hantar | `dont_send-ms-1` |

## States and failure paths

| Prompt | Setup | Expected | Smoke id |
| --- | --- | --- | --- |
| Send an email to aina.rahman@example.com saying hello | No mailbox: Gmail not connected | The card is refused with a sentence telling you to connect Gmail on the Email assistant tab; no draft is saved | manual |
| Tolong hantar emel kepada aina.rahman@example.com untuk ucap terima kasih | No mailbox: Gmail not connected | Ayat dalam Bahasa Melayu untuk menyambung Gmail; tiada draf | manual |
| What needs my attention in my email? | No mailbox: Gmail not connected | "Connect your Gmail on the Email assistant tab first." | manual |
| Request a first aid training course for me | No employee link: staff account not linked to an employee | An answer with the reason; if a card appears anyway, pressing it is refused with a plain sentence and no training request is created | manual |
| Saya nak mohon latihan pertolongan cemas | No employee link | Jawapan dengan sebabnya; tiada permohonan latihan dibuat | manual |
| Send an email to Zulaikha Osman about the invoice | No directory match: nobody of that name in HR | "I couldn't find that person in the directory. Type their email address and I'll continue." Typing an address then gives a Send card | manual |
| Tolong hantar emel kepada Zulaikha Osman tentang invois | No directory match | "Saya tidak menjumpai orang itu dalam direktori. Taip alamat e-mel mereka dan saya akan teruskan." | manual |
| Send an email to aina.rahman@example.com saying the meeting has moved to 3pm | An address in the request | The card goes to exactly that address, with no other recipient | `compose-en-1` |
| Tolong hantar emel kepada aina.rahman@example.com bahawa mesyuarat dipindahkan ke pukul 3 petang | An address in the request | Kad kepada alamat itu sahaja | `compose-ms-1` |
| Write an email to aina.rahman@example.com about Friday's meeting but don't send it yet | "Don't send" | A draft card, never Send | `dont_send-en-1` |
| Tolong tulis emel kepada aina.rahman@example.com tentang mesyuarat itu dan jangan hantar dulu | "Don't send" | Kad draf, bukan Hantar | `dont_send-ms-1` |
| Send an email to Razak about the invoice, then ask something else and go back to the first card | An old card's buttons disabled | Only the newest card's options and candidates can be clicked; the older card's are disabled | manual |
| Tolong hantar emel kepada Razak tentang invois, kemudian tanya soalan lain | An old card's buttons disabled | Hanya kad terbaru boleh diklik | manual |
| Any prompt while the model provider is down | Model unavailable (simulate only in a local stack) | One plain sentence saying the assistant is unavailable and to try again; no raw error code | manual |
