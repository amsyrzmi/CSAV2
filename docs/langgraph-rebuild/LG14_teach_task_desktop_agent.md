# LG14 — Teach-A-Task & Virtual Desktop Action Automation

## CONTEXT & OBJECTIVE
Provide an interactive "Teach a Task" capability inspired by **grokbot** / computer-use desktop agents.
Rather than requiring admins to write complex code or DAGs, the platform provides a **dedicated virtual desktop environment** (headless containerized browser/desktop session) where:
1. **Interactive Desktop**: The agent or admin has a dedicated, sandboxed virtual desktop session (via isolated browser context / containerized display).
2. **Action Recording**: The user performs actions in the desktop session (navigating portals, clicking buttons, filling forms, extracting tables) while the system records the trajectory into a deterministic, replayable action script.
3. **Task Compilation & Parameterization**: The trajectory is compiled into a typed playbook with typed inputs (e.g. date range, SKU, tenant id), validation checkpoints, and error-handling steps.
4. **Scheduled Execution**: Users can schedule the compiled task to run periodically (cron / interval via the LG10 job runner) or trigger it upon specific system conditions (e.g. when an order is flagged, or a low-stock alert occurs).

---

## 1. Architecture & Sandbox Isolation

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Admin / Web Console                             │
│       ┌──────────────────────────────────────────────────────┐         │
│       │  Virtual Desktop Stream (noVNC / Playwright Canvas)   │         │
│       └──────────────────────────┬───────────────────────────┘         │
│                                  │ User interacts & records            │
└──────────────────────────────────┼─────────────────────────────────────┘
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       Sandbox Agent Environment                        │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │ Action Recorder (DOM events, mouse, keystrokes, API requests)   │   │
│   └──────────────────────────────┬─────────────────────────────────┘   │
│                                  │ generates                           │
│                                  ▼                                     │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │ Replayable Playbook / AST (Declarative Actions & Assertions)   │   │
│   └──────────────────────────────┬─────────────────────────────────┘   │
└──────────────────────────────────┼─────────────────────────────────────┘
                                   │ publishes
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   CSA Task & Scheduled Runner                          │
│   - Cron / Condition trigger (LG10 JobRunner)                          │
│   - Headless execution with screenshot / trace recording               │
│   - Anomaly detection & HITL escalation on structural changes          │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Key Components

### 2.1 Virtual Desktop & Sandbox
- **Isolated Runner**: Containerized headless Chromium / Playwright runtime per execution.
- **No Direct Secret Exposure**: External credentials (portal logins) are injected securely via server-side vault session injection, never recorded in raw action payloads.
- **Network Boundaries**: Domain allowlist enforced per tenant; access to local CSA internal network or metadata services is blocked (SSRF guard).

### 2.2 Action Recording & Compilation
- **Event Capture**:
  - Semantic element selectors (data-testid, aria-label, text hierarchy, resilient XPath/CSS).
  - Explicit action primitives: `navigate(url)`, `click(target)`, `type_text(target, value)`, `select_option(target, val)`, `wait_for(selector)`, `extract_table(target)`.
- **Compiler**:
  - Analyzes the recorded trace.
  - Converts dynamic values into parameters (`{{order_id}}`, `{{target_date}}`).
  - Synthesizes assertions (e.g., verifying page title, confirmation badge).
- **Admin Review & Sandbox Test Run**:
  - The compiled playbook must be previewed and executed once in dry-run mode before it can be published.

### 2.3 Scheduled & Conditional Triggers
- **Triggers**:
  - **Time-based**: e.g., "Run every day at 08:00 AM" (persisted in `scheduled_tasks` and executed by LG10 `JobRunner`).
  - **Event/Condition-based**: e.g., "Trigger when an e-commerce order status transitions to 'dispute'".
- **Execution Log & Auditing**:
  - Every run records a record in `job_runs` / `task_runs` with execution duration, screenshot at completion, extracted payload, and success/failure status.
  - If a website changes layout and an action fails, the task aborts safely, logs the failure, and alerts staff via `staff_notifications`.

---

## 3. Phasing & Prerequisites
- **Phase A**: Isolated headless browser runner & action replay protocol (Playwright engine).
- **Phase B**: Action recording interface (console WebSocket stream + event recorder).
- **Phase C**: Parameterization engine & compiler.
- **Phase D**: Scheduled execution via LG10 runner and condition triggers.
