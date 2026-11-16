# The alive assistant dock (PR 3b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The collapsed dock button shows the assistant's pixel avatar that reacts to hover, focus and click, and after a quiet moment offers one idea in a small bubble, all inside the existing motion rules.

**Architecture:** A pure module decides when a bubble may show and which idea it carries; a small launcher component owns the avatar states, the click hop and the bubble; the dock renders the launcher where it renders its plain button today. Ideas come from Task 3 of plan 3a (`assistant-ideas.json`, `ideasFor`). Front end only.

**Tech Stack:** Next.js App Router, React, TypeScript, CSS keyframes in `W/app/globals.css`, `node --test`, Playwright via pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-overview-assistant-design.md`, section "3b. The alive dock". **Depends on plan 3a being merged** (`docs/superpowers/plans/2026-11-16-overview-assistant-3a.md`): it needs the shared conversation provider, `ideasFor`, `assistant-ideas.json`, and the dock hiding on Overview. `W` below means `CSA_WebConsole/csa-console/web`.

## Global Constraints

- Same constraints as plan 3a: public repo, synthetic data; no migration or server change; Write or python-replace instead of Edit (formatter hook); the repo venv and the scratchpad wrapper for `NO_LOCAL_ENV=1 ENV=ci`; one browser test file at a time; commit trailers `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_012Xj1GQFBtX7jxjqQanM4hp`; merge main, never rebase or force-push; new `W` files and tests are added to `W/scripts/check-isolated.mjs`; stop at "PR open, CI green", no rollout.
- **One motion gate:** `useMotionAllowed(ref)` (`W/components/console/fs/useMotionAllowed.ts`) decides whether the avatar loops, the wake animation and the hop run. It already covers `prefers-reduced-motion`, a hidden tab, an offscreen element and the product pause preference (`fs-motion-paused`). No second gate, no new preference.
- **Only existing avatar frames.** `AgentAvatar` states used: `idle`, `working`, `wake` (`W/components/console/fs/AgentAvatar.tsx`). `working` and `wake` exist only on the 48 px grid (the 32 px grid plays the idle loop for both), so the launcher avatar is `size={48}`. **This deviates from the spec's "32 px" and is a call to flag in the PR body.**
- The bubble is a button after the main launcher in DOM order; it is not a live region, takes no focus on its own, and is never rendered while the panel is open, while a card awaits a decision, or on Overview.
- Idea limits from the spec: wait 20 seconds of a closed, untouched page; at most one bubble per page visit; a dismissed idea never returns in that browser session (`sessionStorage`); text in the user's language; role- and package-filtered by `ideasFor`.
- Ledger: `.superpowers/sdd/2026-11-16-overview-assistant-3b/progress.md` (git-ignored).

## Review Focus

1. **Reduced motion or paused.** No wake, no hop, no looping avatar; the bubble still appears and works. (Task 2)
2. **Activity resets the wait.** Typing in the page, moving the pointer or pressing a key within 20 s postpones the bubble; a bubble never appears over an open panel and never reappears after Dismiss in the same session. (Task 1)
3. **Hidden tab or offscreen launcher.** The wait does not run down while the tab is hidden (a user returning is not greeted at once). (Task 1)
4. **Language and role.** A Malay turn history shows the Malay idea; a user with no eligible idea (read-only, no packages) gets no bubble and no empty box. (Task 1)
5. **Keyboard and screen reader.** The bubble button has an accessible name equal to the idea text, Dismiss has its own label, Escape closes the bubble, and nothing is announced without user action. (Task 3)

---

### Task 1: When a bubble may show (pure logic)

**Files:**
- Create: `W/lib/assistant-bubble.ts`
- Test: `W/tests/assistant-bubble.test.mjs`
- Modify: `W/scripts/check-isolated.mjs`

**Interfaces:**
- Consumes: `Idea`, `ideasFor` from `W/lib/assistant-ideas.ts` (plan 3a); `Language`.
- Produces (no React, no imports except types, loadable by `node --test` as `overview-home.ts` is):
  ```ts
  export const BUBBLE_WAIT_MS = 20_000;
  export const DISMISSED_KEY = "fs-assistant-dismissed-ideas";
  export function parseDismissed(raw: string | null): string[];            // tolerant: bad JSON or non-array gives []
  export function serializeDismissed(ids: readonly string[]): string;
  export function pickBubbleIdea(
    candidates: readonly { id: string; text: string }[],   // output of ideasFor
    dismissed: readonly string[],
    shownThisVisit: boolean,
  ): { id: string; text: string } | null;                  // first candidate not dismissed; null when shownThisVisit
  export function bubbleMayShow(state: {
    panelOpen: boolean; cardOpen: boolean; onOverview: boolean; busy: boolean;
    shownThisVisit: boolean; idleMs: number; hidden: boolean;
  }): boolean;                                             // true only when closed, quiet, visible and idleMs >= BUBBLE_WAIT_MS
  ```

- [ ] **Step 1: Write failing tests** for each function: `parseDismissed(null)`, `"not json"`, `"{}"`, `'["a","b"]'`; round trip; `pickBubbleIdea` skips dismissed ids, returns `null` for an empty list, for all dismissed and for `shownThisVisit`; `bubbleMayShow` is false for each blocker in turn (panel open, card open, on Overview, busy, already shown, hidden tab, idle 19 999 ms) and true at exactly 20 000 ms with every blocker clear.
- [ ] **Step 2: Run** `node --test W/tests/assistant-bubble.test.mjs` through the wrapper; expect failure (module missing).
- [ ] **Step 3: Implement** the module; add both files to `check-isolated.mjs`.
- [ ] **Step 4: Run** the node test and `npm run typecheck`; expect pass.
- [ ] **Step 5: Commit** `feat(console): rules for when the dock offers an idea`.

### Task 2: The living launcher button

**Files:**
- Create: `W/components/console/assistant/AssistantLauncher.tsx`
- Modify: `W/app/globals.css` (one `@keyframes fs-dock-hop` of about 400 ms and a class that uses it, inside the same reduced-motion handling the avatar classes use), `W/components/console/AssistantDock.tsx` (render the launcher in place of the plain button), `W/tests/theme-literals.test.mjs`, `W/scripts/check-isolated.mjs`
- Test: `tests/test_workspace_browser.py`

**Interfaces:**
- Consumes: `AgentAvatar` (`state: "idle" | "working" | "wake"`), `useMotionAllowed`, the dock's current open button (`data-testid="assistant-open"`, `aria-expanded`, `aria-controls`, label `text.dock_title`, the `corner` placement class).
- Produces: `AssistantLauncher({ label, busy, panelId, onOpen, buttonRef, idea, onIdea, onDismissIdea }: { label: string; busy: boolean; panelId: string; onOpen(): void; buttonRef: Ref<HTMLButtonElement>; idea: { id: string; text: string } | null; onIdea(idea: { id: string; text: string }): void; onDismissIdea(id: string): void })`. The main button keeps `data-testid="assistant-open"` and its accessible name; it shows the avatar (`size={48}`) beside the label: state `working` while `busy`, `wake` for 1.5 s after hover or keyboard focus begins, else `idle`; a click applies the hop class for 400 ms and calls `onOpen`. With `useMotionAllowed` false it never sets `wake` and never applies the hop class (the avatar draws its rest frame itself). The bubble (Task 3) is rendered by this component.

- [ ] **Step 1: Write failing browser tests** in `test_workspace_browser.py`: the launcher shows the avatar (`svg` inside `assistant-open`); hovering the button gives the avatar element the wake class and it returns to the idle class after 1.5 s (wait on the class, not on a sleep); clicking opens the panel and the hop class is present at click time; with the browser context emulating `reduced_motion="reduce"` none of the wake or hop classes ever appear and the avatar shows its rest frame; with `localStorage["fs-motion-paused"]="1"` set before load, same result (Review Focus 1); `busy` shows the working class (open the panel, send a message with the stub delayed, close the panel, assert the launcher's avatar has the working class).
- [ ] **Step 2: Run** `bash <wrapper> uv run pytest tests/test_workspace_browser.py -q`; expect the new tests to fail.
- [ ] **Step 3: Implement** the launcher and the CSS; the dock passes `busy` from the conversation. Keep the existing focus return to `buttonRef` when the panel closes.
- [ ] **Step 4: Run** the browser file again plus `npm run typecheck`, `npm run lint`, the theme-literals and isolated checks. Expected: pass, and every previously passing dock test still passes.
- [ ] **Step 5: Commit** `feat(console): the dock button is the living assistant avatar`.

### Task 3: The idea bubble

**Files:**
- Modify: `W/components/console/assistant/AssistantLauncher.tsx`, `W/components/console/AssistantDock.tsx`, `W/lib/assistant-copy.ts` (labels `dock_idea_dismiss`, English and Malay, plain words per `docs/CONSOLE_VOICE.md`)
- Create: `W/components/console/assistant/useIdleTimer.ts`
- Test: `tests/test_workspace_browser.py`, `W/tests/assistant-copy.test.mjs`

**Interfaces:**
- Consumes: Task 1 (`bubbleMayShow`, `pickBubbleIdea`, `parseDismissed`, `serializeDismissed`, `BUBBLE_WAIT_MS`, `DISMISSED_KEY`); plan 3a's `ideasFor`, `assistant-ideas.json`, `useAssistantConversation()` (`turns`, `busy`, `outcomes`, `language`, `body`, `setBody`), `openProposals` from `assistant-view.ts`, `usePathname`/`tabForPath` for the Overview check.
- Produces: `useIdleTimer(options?: { waitMs?: number }): number` returns the milliseconds since the last pointer move, key press, scroll or touch on the document, updated about once a second and frozen while `document.visibilityState === "hidden"` (the clock restarts when the tab is visible again; Review Focus 3). In the dock: the candidate ideas are `ideasFor(ideas, { tab: tabForPath(...), role, packages, language })`; when `bubbleMayShow(...)` is true the dock passes `pickBubbleIdea(...)` to the launcher and records `shownThisVisit` (a `useRef` reset on each path change). `onIdea` opens the panel, puts the idea text in the composer with `setBody` (nothing is sent) and focuses it; `onDismissIdea` adds the id to `sessionStorage[DISMISSED_KEY]` and hides the bubble; Escape while the bubble button has focus dismisses it.

- [ ] **Step 1: Write failing tests.** Browser (set the wait low through a test-only query or by advancing time with Playwright's clock API, without changing production constants): the bubble appears after the idle wait on a page with eligible ideas, in the language of the latest turn (Review Focus 4); clicking it opens the panel with the idea text in the composer and sends no turn request; Dismiss hides it and it does not return after a reload in the same session (`sessionStorage`), but a new session shows the next idea; typing in the page within the wait postpones it (Review Focus 2); it never appears with the panel open, while a card awaits a decision, or on Overview; a read-only member and a user with no eligible idea get no bubble and no empty element; the bubble is a `button` with the idea as its accessible name, Dismiss has its label, Escape dismisses, and the container has no `aria-live` (Review Focus 5). Node: `assistant-copy.test.mjs` asserts `dock_idea_dismiss` exists in both languages.
- [ ] **Step 2: Run** the browser file and the copy test; expect the new ones to fail.
- [ ] **Step 3: Implement** the hook, the bubble in the launcher, the wiring in the dock, the copy. Position the bubble above the button inside the same fixed corner, never overlapping the open panel, and make it wrap at narrow widths (`max-w-[min(18rem,calc(100vw-2.5rem))]`).
- [ ] **Step 4: Run** `tests/test_workspace_browser.py`, `tests/test_overview_browser.py`, every other browser file that renders the dock and stubs the console API (grep `workspace_assistant`), one at a time; node tests; typecheck; lint; isolated check. Expected: pass.
- [ ] **Step 5: Commit** `feat(console): the dock offers one idea after a quiet moment`.

### Task 4: Documentation and whole-branch checks

**Files:**
- Modify: `docs/ASSISTANT_RETEST.md` (rows), `docs/PROGRESS.md`

- [ ] **Step 1: Retest rows** (manual): the launcher shows the avatar and reacts to hover and click; with the operating system's reduced-motion setting on, or the product pause on, nothing animates and the bubble still works; after a quiet moment on Inbox the bubble offers an idea, clicking it fills the composer and sends nothing, Dismiss keeps it away for the session; no bubble on Overview or with the panel open; Malay session shows a Malay idea.
- [ ] **Step 2: PROGRESS entry**; confirm `git diff origin/main --stat -- db csa docs/console_openapi.json` is empty.
- [ ] **Step 3: Checks:** all changed browser files one at a time, `node --test` for changed web tests, `npm run lint`, `npm run typecheck`, `npm run check:isolated`.
- [ ] **Step 4: Commit** `docs: alive dock retest rows and progress`.
- [ ] **Step 5: Whole-branch review** on the most capable model, one fix wave, one scoped re-review; merge main; push; open the PR with `gh api -X POST repos/amsyrzmi/CSAV2/pulls --input <json>` (body notes the 48 px avatar deviation and ends with the "Generated with Claude Code" line and the session URL); watch CI to green; tell the owner the PR number. Front end only: no smoke needed; no rollout by this session.
