# HR assistant slice 1c-ii: "mark my task done" as a confirm card

Date 2026-11-19. Follows slice 1c-i (read only answers, PR #162 and #165). Authority: the HR processes
spec section 9 and the 1c-i design section 9. The owner said "Start 1cii" on 2026-11-19 and chose to
skip the questions; the calls below are mine and are marked as rulings.

## Goal

A person says "mark my task done" (or "tandakan tugasan saya sebagai selesai"). The assistant finds
that person's own open step in My tasks and proposes one confirm card. Confirm completes that one
step through the console's own `task.complete` operation. Nothing else changes.

## Non-goals and anti-goals

- Never approve, send back, skip, reschedule, reassign, cancel or close a step, and never complete
  one on someone else's behalf: those stay in the console (the 1c-i sentence is kept for them).
- Never a note: the action has no free-text field, so no personal text reaches the planner or the
  card payload (the payload is the step id and its version only, the result the operation's
  receipt). The card's own summary and fields name the step and the person and are kept on the card
  and turn rows, tenant-readable, exactly as the people cards keep a person's name (the pick list's
  stored copy keeps ids and a counter only).
- Not an automatic-check step (the person's contact details, the account link, access removal):
  the console refuses them while the check fails, and `access_removed` is the system's. They get
  the console sentence.
- Not part of a plan card (no `PlanStepKind`): a plan confirms several things at once, and a step
  completion is its own confirm.
- No routing group for Jev (the 1c-i ruling holds).

## Behaviour

1. Planner action `hr_task_complete` with an optional `task`: words of the step title the user
   named, or the option value (a task id) they picked. Nothing else.
2. The resolver reads the caller's My tasks as the caller (the same read session as 1c-i) and keeps
   the steps that are `mine`, `todo` or sent back (`rejected`), not `pool`, not
   `needs_my_approval`, with no `auto_check`.
   - none: one sentence (nothing of yours to mark done; the rest is in My tasks).
   - one, or exactly one matching `task`: the card.
   - several: a pick list of up to five, valued by task id; a typed word that matches nothing gets
     the pick list too. The stored copy of the list keeps no title, name or date.
3. The card: title "Mark this step done", summary naming the step, fields Step, For (kind and
   person, e.g. onboarding for Aina Rahman), Due, and "After you confirm" saying either that the step
   is done or that it then waits for approval by whom (HR, manager). The card is bound to the
   step's `version`: a changed step makes it stale ("proposal_stale").
4. Confirm runs `HrProcesses.task_action("task.complete", ...)` with an assistant mutation session,
   so the tenant's `hr_assistant` switch applies as for the people cards. The receipt (operation,
   status, version) is the stored result; never a title or name.

## Rulings (mine)

- One action with one optional word field, not a note or a person: the smallest addition to a
  37 KB prompt that the model is sensitive to. Measured with the whole smoke file, as in 1c-i.
- Steps needing approval are allowed: completing them is the person's own act, and the card says
  the approval follows. Approving stays out.
- HR pool steps (`pool`) are not "my" task, so they are not offered; the HR team completes them in
  My tasks.
- The two 1c-i smoke cases `hr_process_neg-en-5` and `-ms-5` (the console sentence) now expect
  `hr_task_complete`; a new case checks an approval request still answers with the sentence.

## Parts

- Migration `0637_assistant_task_card.sql`: `console_proposals_kind_check` also allows `hr_task` and
  `console_workspace_turns_action_check` also allows `hr_task_complete`, each read from the live
  list as 0542 does. Raises `EXPECTED_SCHEMA_VERSION` to 0637.
- `csa/workspace/hr_task_card.py`: the resolver and `HrTaskExecutor` (kind `hr_task`).
- `csa/workspace/contracts.py`, `executors.py`, `planner.py`, `service.py`: the proposal model, the
  kind, the registry entry, the prompt bullet and rule, the dispatch.
- Web: the kind in `workspace-api.ts` and the executed summary in `assistant-view.ts`, with a test.
- Smoke cases, `docs/ASSISTANT_RETEST.md`, `docs/PROGRESS.md`, `docs/hr/PROGRESS.md`,
  `docs/hr/SETUP.md`, the migration README.
