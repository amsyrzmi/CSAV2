# Knowledge cleanup retest checklist

Run this on staging after the rollout that brings knowledge cleanup (migration `0550`, branch
`feat/knowledge-cleanup`). Use the staging test company and synthetic files only (a short text or
PDF file you made up). Spec: [the design](superpowers/specs/2026-11-10-knowledge-cleanup-design.md).
Rows marked "manual" depend on staging state or on the console, so only a person can check them.
Tick each row when the result matches.

You need a tenant admin of the test company (or a member with the knowledge writer role), and, for
rows 6 and 7, a way to switch the knowledge assistant off and on (Services, Knowledge assistant). A
failed upload is easiest to make with a file that cannot be read, for example a renamed text file
with a `.pdf` ending, or an empty file.

## Rows

| Check | Setup | Expected | Kind |
| --- | --- | --- | --- |
| Delete a source that has a failed card | Knowledge page. Upload an unreadable file so a card shows Needs attention. Delete the source it belongs to from the list below | The delete finishes without an error and the failed card disappears after the page reloads. No Needs attention count is left for it | manual |
| Failed upload, original still stored | Upload one unreadable file so its card shows Needs attention; leave its source in place | The card reads "We couldn't read this file. You can try again or remove it." with Try again and Remove. Press Try again: the card says it is back in the queue, then shows Reading, then fails again (the file is still unreadable, so a second failure proves it was re-read). No second card appears | manual |
| Remove asks first | On a failed card press Remove | A confirmation reads "This takes the message off your list. Nothing your assistant knows changes." Cancel keeps the card; Remove takes it off the list and the assistant's knowledge is unchanged (ask it something it already knew) | manual |
| Failed upload, original removed | In one upload choose two files: one unreadable and one good. When the card shows Needs attention or Some of this file couldn't be read, delete the source of the good file from the library. The upload's other file is still live, so the card stays | The card reads "The original was removed, so this can't be tried again. Remove it." It offers Remove (and Choose the file again), and no Try again. A Try again from a stale page is refused with a plain sentence and no code | manual |
| Clear all that failed | Needs two failed cards. Upload two unreadable files | "Clear all that failed" shows above the list (it is not there with one failed card). Press it: a confirmation appears; confirm, and both cards go. A card that is Reading or queued, and any finished upload, stays | manual |
| Queued upload with the knowledge assistant off | Switch the knowledge assistant off. Upload a file so it queues | The card reads "Paused: the knowledge assistant is switched off." Cancel works and the card goes. On a second queued card, Try again is disabled and Remove works | manual |
| Delete a document with the assistant off | Keep the knowledge assistant switched off. Delete a document from the library, and then a source | Both deletes work with no "switch it on" refusal and no error banner; the page no longer lists them. Switch the assistant on again afterwards | manual |
| A file being read shows Reading | Switch the assistant on. Upload a larger synthetic PDF (a few dozen pages) and watch the card | The card says Reading, not Needs attention, and the page refreshes by itself until the file is done (no manual reload). The Needs attention count does not include it | manual |
| The company portal view | Repeat rows 1 to 8 in the company's own portal view (as a company member with the knowledge writer role), not the platform admin view | The same cards, notes and buttons, the same results. A read-only member sees no Try again, Remove, Cancel or Clear all | manual |
| Existing stuck cards are gone after rollout | Right after the rollout, open the Knowledge page of every company that had a stuck failed, partial or queued card whose files were all deleted | Those cards are gone on every company (the `0550` cleanup). Cards with at least one live file, and every done upload, are still there. This cannot be undone, so look before you rely on it | manual |

## Notes

- A partial card reads "Some of this file couldn't be read. The rest is in your assistant."
  Try again re-reads every file of that upload; files already read come back as duplicates, so
  nothing is stored twice.
- After a refused action the page reloads the list, so a card that another person already removed
  disappears instead of staying stuck.
