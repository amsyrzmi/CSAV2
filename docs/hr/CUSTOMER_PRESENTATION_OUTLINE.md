# CSA Agentic AI Platform
## Customer presentation outline and speaker brief

**Purpose:** Give a customer-facing overview of CSA, with extra attention on the HR agent and voice experience, then explain the wider agent platform and web console.

**Recommended presentation length:** 20–30 minutes, including a short product walkthrough.

**Audience:** Business leaders, operations teams, HR stakeholders and technical decision-makers.

**Presentation framing:** CSA is a multi-tenant AI operations platform. Specialized agents assist with defined tasks, while the runtime and staff console provide access controls, workflow visibility and human review. Present availability for the customer's environment according to its actual configuration and acceptance status.

---

## 1. Executive summary

CSA brings AI assistance into business workflows through a set of specialized agents and a staff web console. It is designed to connect a request to the relevant business capability, apply tenant and user permissions, and return a structured result that can be reviewed and acted on by staff.

The two requested areas are:

1. **HR agent:** authenticated staff workflows for directory access, training and other people operations. Access is governed by explicit HR authority, and consequential decisions stay with authorized people.
2. **Voice experience:** local browser dictation and spoken status in the staff HR assistant. A separate customer-facing interactive voice agent is a planned extension and should not be described as currently live or generally available.

The wider platform includes customer-service, knowledge, order, ticket and escalation capabilities, plus staged booking, subscription and marketplace work. The web console gives staff and administrators a place to manage access, review activity and operate supported workflows.

### Suggested opening

> “CSA connects AI assistance to defined business workflows. Specialized agents help with bounded tasks, staff retain control of important decisions, and the web console makes access and activity visible.”

### Three takeaways for the audience

- **Useful assistance:** AI can help staff and customers complete information-heavy, repetitive workflows.
- **Controlled execution:** permissions, workflow rules and human review determine what happens next.
- **Operational visibility:** staff have a console for supported controls, review and activity.

---

## 2. Presentation agenda

| Section | Suggested time | Purpose |
|---|---:|---|
| Product and business problem | 2 minutes | Establish the value and audience context |
| Platform overview | 3 minutes | Explain agents, runtime and console |
| HR agent | 6 minutes | Highlight staff workflows and authority controls |
| Voice experience | 3 minutes | Demonstrate staff dictation and set the customer voice boundary |
| Other agents and console | 5 minutes | Show the wider product surface |
| Walkthrough and questions | 5–10 minutes | Demonstrate a representative workflow and discuss fit |

Adjust the timing to the customer's priorities. If HR is the principal buying interest, spend more time on HR workflows and the access model.

---

## 3. Product overview

### What CSA does

CSA provides a platform for applying AI to defined business operations. A request can be routed to an agent with a specific purpose, such as searching an approved knowledge base, looking up an order or preparing an HR request. The agent works within an application workflow rather than receiving unrestricted access to business systems.

At a high level, the product has four parts:

1. **Request channels and authenticated interfaces** receive customer or staff requests. The channels available to a tenant depend on the deployed configuration.
2. **Coordinator and decision layer** determine which supported task should handle a request and whether more information or human review is needed.
3. **Specialized agents** perform bounded reads, searches or workflow preparation using typed inputs and tenant-scoped data access.
4. **Staff web console** provides authenticated operational views, supported management controls and human workflow actions.

### What makes this an agent platform

The platform is organized around agents with declared capabilities and defined task boundaries. An agent is not simply a chat persona: it is connected to a business function, returns structured information and is governed by application policy.

Examples include:

- A knowledge agent retrieves relevant material from a tenant's configured knowledge base.
- An order agent can perform supported local product/order tasks.
- An HR assistant can prepare a permitted request, while the HR service checks the current staff authority before execution.
- An escalation agent records a handoff and prevents automated responses from continuing while a human owns the conversation.

### Suggested speaker explanation

> “The language model helps interpret a request, but the application decides what capabilities are available and what requires a person. Each agent has a defined job and runs under the tenant's configured access.”

---

## 4. Highlight: HR agent and people workflows

### HR product story

The HR capability brings staff-facing people workflows into the same broader agent platform while keeping HR identity and authority distinct from customer-service identity. The staff user's authenticated session and explicit HR grants determine which HR actions and records are available.

HR access is not inferred from a general console role alone. Employee records are separate from console accounts and customer contacts; an authorized HR relationship or grant is required for protected HR information.

### Core HR capabilities

#### Directory

- Staff can view employee information that their active HR grants permit.
- Directory results use field-level projections, so the response can contain only the fields authorized for that user and relationship.
- Self and manager access are governed by explicit rules. A broad tenant role does not automatically reveal employee records.
- HR owners can manage employee records, account links and action grants through the supported administration workflows.

**Customer value:** staff can find the people information they need without turning the whole directory into an unrestricted data source.

#### Training

- Staff can prepare and submit training requests.
- The request's current state and version are available for review.
- Decisions such as approval or rejection remain human decisions.
- Where an agent prepares a decision, the system binds it to the request and expected version and requires an authorized human confirmation before it is committed.
- Request keys and receipts support safe recovery when a browser loses the response to a saved action.

**Customer value:** routine request preparation can be streamlined while responsibility for the decision remains with the assigned human reviewer.

#### Recruitment and interviews

- Candidate information is handled through consent-aware, retention-bounded workflows.
- Authorized staff can view private candidate materials and notes under HR controls.
- Interview scheduling is available for authorized operators when the required consent and staff membership conditions are satisfied.
- The system does not autonomously rank candidates, hire applicants or reject them.

**Customer value:** administrative steps can be organized while recruitment judgment stays with people.

#### Attendance, shifts and leave

- Attendance imports are bounded and leave imported entries pending review.
- Corrections, shifts and leave requests use validation for dates, time zones, overlaps and applicable configured balances.
- Leave approval is a workflow decision and updates the relevant local records atomically.
- The described implementation does not collect biometric or location data and does not calculate wages automatically from attendance.

**Customer value:** staff can manage common people operations with explicit validation and review points.

#### Payroll review workflow

- Payroll support is built around reviewed configuration, effective-dated contribution rules, exact decimal calculations and private snapshots.
- A second reviewer confirms the specific payroll snapshot before the decision is final.
- Approved periods are protected against overlapping approvals; reversal preserves the earlier decision history.
- The implementation does not initiate bank payments or file statutory returns.
- Malaysian statutory treatment and client-specific rules require qualified review and approved configuration before operational use.

**Customer value:** calculation and review preparation can be structured and auditable; it should not be represented as autonomous payroll compliance or payment execution.

#### HR assistant

- The assistant can plan one permitted HR action from a submitted staff message.
- It can request missing information or direct a staff member to the relevant workflow when a request is unsupported or requires human judgment.
- The assistant cannot grant permissions, impersonate a user, approve/reject a request, read private CVs or operate payroll.
- Business actions are rechecked against current HR authorization when they execute.

### HR controls worth explaining

- **Explicit authority:** HR access uses HR-specific sessions, grants and resource checks.
- **Least data:** directory responses expose permitted fields, and private HR content is not fed wholesale to the assistant.
- **Human decisions:** the assistant may prepare a workflow step, but sensitive decisions require the authorized human path.
- **Audit and replay protection:** HR operations use durable receipts, audit events, version checks and idempotency protections.
- **Tenant boundaries:** records and actions are scoped to the correct tenant; cross-tenant access is denied.

### Suggested HR speaker notes

> “The HR agent is designed to assist with preparation and retrieval, not to take over HR authority. It checks the user's active HR permissions and sends decisions that need judgment through a human review step.”

> “For sensitive records, access is specific to the person, field and workflow. A staff member does not gain HR access simply because they have a general console account.”

---

## 5. Highlight: voice experience

The term “voice agent” can refer to two different things in this product context. Keep them separate in the presentation.

### Staff HR assistant: local dictation

The HR assistant includes an explicit browser dictation option:

1. A staff member chooses **Dictate locally**.
2. The browser attempts on-device speech recognition, subject to browser support and an installed language pack.
3. Recognized text appears in the request field.
4. The staff member can review or edit that text.
5. The request is submitted through the normal authenticated HR assistant workflow.

The HR assistant can also read a short status response aloud using an available local browser voice. If local speech support is missing, typed input remains available.

**How to describe it:**

> “The staff HR assistant supports local dictation in compatible browsers. The transcript is shown for review before submission, and staff can always type instead.”

The code explicitly requires local recognition processing and does not select a cloud speech fallback. Actual support depends on browser capabilities and the installed local language pack. The current UI selects `en-MY`; do not claim broad language coverage without separate validation.

### Customer-facing interactive voice agent

The codebase contains historical design work for voice-note transcription and plans for interactive voice. The current continuation notes state that customer-facing interactive voice still needs a reviewed design for session handling, consent, cancellation, replay and media activation.

Therefore:

- Present interactive customer voice as **planned or under design**, unless the specific customer deployment has separate evidence that it is implemented, enabled and accepted.
- Do not claim live phone answering, real-time voice conversation, multilingual speech support, transcription accuracy or production availability based only on the local HR dictation feature.
- Treat a future interactive voice release as a separate capability with its own consent, retention, language, failure and operational acceptance decisions.

### Suggested answer if asked “Does CSA have a voice agent?”

> “CSA has a local dictation experience in the staff HR assistant today in compatible browsers. A full customer-facing interactive voice agent is a separate planned capability; we would confirm its channels, languages, consent and deployment readiness for your environment before committing to availability.”

---

## 6. Other agents and functionalities

Use this section to show that HR is part of a wider platform. Be clear about the difference between locally implemented behavior and provider-dependent or prepared-only functionality.

| Agent or capability | Current product scope | Customer-facing qualification |
|---|---|---|
| **Knowledge base** | Tenant knowledge ingestion and retrieval, supported document lifecycle, source/version tracking and unanswered-question capture | Retrieval depends on tenant content and compatible embedding setup. A queued upload is not searchable until ingestion completes. |
| **Orders** | Local product lookup, order status and order draft actions supported by the runtime | Describe only the actions enabled and backed by the customer's configured data. A draft is not a fulfilled order. |
| **Support tickets** | Tenant-scoped ticket create, lookup, list and supported ticket updates | Ticket behavior depends on the enabled tenant capability and policy. |
| **Escalation and human handoff** | Records escalation, notifies staff through configured paths, and suppresses follow-up automation while staff own the conversation | Promise human follow-up only where an actual ticket or successful staff notification exists. |
| **Invoices** | Local contact-scoped invoice lookup/status | Invoice PDF delivery, invoice issuance and MyInvois execution are not implemented in the default runtime. |
| **Bookings** | Local booking history and preparation of booking/reschedule/cancellation requests | No live calendar availability check or booking execution is included in the prepared-only scope. |
| **Subscriptions** | Local plan/status reads and preparation of change requests | No payment-provider billing change, checkout or payment execution. |
| **E-commerce / marketplaces** | Adapter and authorization code exists for marketplace reads and related setup flows | Default runtime catalogue says provider setup is required and marketplace adapters are not wired by default. Confirm per tenant. |
| **LHDN MyInvois** | Appears as a catalogued future capability | Sandbox validation, signing and submission are not implemented. |
| **Staff workspace assistant** | Tenant-aware workspace guidance and a bounded local order read, plus HR grant-aware guidance | Do not imply a general-purpose unrestricted staff agent or broad tool access. |

### Platform workflow examples

#### Knowledge question

1. A customer asks a business or product question.
2. The coordinator can route it to the knowledge capability when enabled and ready.
3. The agent retrieves tenant-scoped material and returns grounded facts.
4. If the knowledge base cannot answer, the question can be captured for staff follow-up.

#### Support issue needing a person

1. A request is identified as needing staff attention.
2. The escalation workflow records the handoff and uses configured staff notification paths.
3. Human ownership suppresses automated follow-up until control is released.
4. Staff can review the conversation and reply from the inbox where supported.

#### Booking request

The agent can save a request for a booking action, but prepared-only status means it has not checked live availability or created a calendar event. Describe this as workflow preparation, not a confirmed booking.

---

## 7. Web console

### What the console is for

The web console is the authenticated staff and administration surface for the platform. It gives permitted users visibility into tenant capabilities and runtime activity and provides interfaces for supported customer-service, knowledge and HR operations.

### Main areas to show

#### Agent catalogue and runtime activity

- Lists agents and their implementation stage.
- Shows subscription state, tenant enablement, setup/readiness and circuit information as distinct indicators.
- Gives staff an activity view of recorded run metadata.
- Makes clear that a switch being on does not prove an external provider is connected or healthy.

#### Customer-service inbox

- Lets authorized staff review conversations and messages.
- Supports takeover, release and human replies in the supported workflow.
- Includes an approval queue for supported approval decisions.
- Actions use the same underlying approval and delivery mechanisms as other supported paths.

#### Knowledge base

- Uploads supported documents through authenticated console routes.
- Displays job and per-file ingestion progress.
- Supports source downloads, versions, duplicate handling and audited deletion under the applicable role.
- Exposes unanswered-question/gap workflows where enabled.

#### HR workspace

- Provides directory, training, HR requests, automation and related tools according to the user's HR permissions.
- Supports review and human confirmation paths.
- Includes the HR assistant and local dictation controls for compatible browsers.

#### Tenant and team administration

- Supports tenant/client and team workflows for authorized administrators.
- Uses authenticated membership and role checks; browser-supplied identity is not the authority source.

### Console access story

The browser calls the versioned console API through the adopted web application. The backend derives identity and role from verified sessions and scopes database work to the tenant. The console separates platform-admin controls from tenant-admin, staff and read-only use.

### Suggested console speaker notes

> “The console is the operational control plane. It shows what is implemented, what is enabled and what still needs setup. That distinction helps staff avoid treating a catalog entry as proof that an integration is ready.”

---

## 8. Trust, governance and operational controls

Use this section if the customer asks how the product controls agent actions or handles sensitive workflows.

### Access and tenant boundaries

- Each request is associated with a tenant and authenticated actor context.
- Backend authorization is enforced on the server and in tenant-scoped data access.
- HR permissions are separate from customer contacts and general console roles.
- Agent inputs do not get to choose their own tenant, user or authority.

### Human review and risk

- The product distinguishes reads, preparation and committing actions.
- Human approval/confirmation paths are available for supported sensitive operations.
- HR approvals, payroll review and other consequential decisions are governed by the relevant human workflow.
- The model's proposed action does not itself grant permission or constitute a committed business decision.

### Reliability and visibility

- Supported writes use idempotency and durable records so retries can be handled safely.
- Agent execution metadata and workflow audit records support staff review.
- Capability switches, implementation stage and setup status are surfaced independently.
- Provider status should be verified for the specific deployment; the catalogue is not a live provider-health guarantee.

### Data handling

- Knowledge and business data are tenant scoped.
- HR records use distinct authority and privacy controls.
- Staff HR dictation is designed for local browser processing; the submitted text follows the normal HR assistant request path.
- Customer voice/media retention and multilingual behavior need separate design and acceptance before making claims.

---

## 9. Recommended demo script

Use a synthetic tenant and synthetic employee/customer records. Do not display real HR, customer or credential data.

### Demo A: show agent readiness

1. Open the console's Agents page.
2. Point out one available, one setup-required and one prepared-only capability if present in the demo tenant.
3. Explain that subscription, tenant switch, implementation and setup are separate states.
4. Open runtime activity and show the kind of metadata staff can inspect.

**Say:** “This view helps an administrator understand both the capability and its current readiness before relying on it operationally.”

### Demo B: HR directory and grants

1. Open the HR workspace with a synthetic staff account.
2. Show a directory result that contains only the fields authorized for that account.
3. If practical, compare with a separate synthetic manager account to show grant-based field access.
4. Avoid displaying unauthorized or private records in the demo.

**Say:** “The HR service checks current HR grants for each request; a general console role is not enough by itself.”

### Demo C: training request and human decision

1. Open Training.
2. Prepare and submit a synthetic training request.
3. Show its saved status and the review action.
4. If demonstrating agent-prepared approval, show the explicit confirmation bound to the request/version and confirm it only with the authorized synthetic reviewer.

**Say:** “The agent can help prepare the work, but the assigned human owns the approval decision.”

### Demo D: HR assistant voice dictation

1. Check that the demo browser is secure-context capable and has local speech recognition plus the needed local language pack.
2. Choose **Dictate locally** and speak a non-sensitive synthetic request.
3. Stop dictation and review/edit the text before sending.
4. Submit and show the resulting clarification, request or workflow handoff.
5. Optionally use local read-aloud for the short status result.
6. If speech recognition is unavailable, continue with typed input and explain the browser dependency.

**Say:** “This is local staff dictation into a reviewable text field. It is not a demonstration of a customer phone agent.”

### Demo E: customer-service handoff and inbox

1. Open a synthetic conversation in the inbox.
2. Show takeover or the recorded handoff state.
3. Show a human reply or the approval queue if the demo fixture is prepared.
4. Explain that the automation respects human ownership until staff release it.

### Demo F: knowledge lifecycle

1. Open the knowledge base.
2. Show an already ingested synthetic source and its version/status.
3. Show a recorded gap/unanswered question if prepared.
4. Explain that an upload only becomes searchable after ingestion completes.

---

## 10. Questions customers may ask

### “Can the AI make HR decisions?”

The assistant can help prepare requests and retrieve information allowed by the current grants. Decisions such as approval/rejection and payroll review remain with authorized humans in the relevant workflow.

### “Does a tenant administrator automatically see employee data?”

No. HR access is separate from ordinary console roles and is governed by active HR permissions and the specific resource/field rules.

### “Is the voice agent live?”

The staff HR assistant includes local browser dictation and local read-aloud where supported. A customer-facing interactive voice agent is a separate planned capability and needs its own consent, session, language, retention and deployment acceptance.

### “Which languages does voice support?”

The current HR browser dictation implementation selects `en-MY`, and availability depends on browser support and an installed on-device language pack. Do not promise other languages or transcription quality without validation in the customer's target browsers and environment.

### “Can the booking agent confirm a calendar booking?”

The catalogued booking scope supports local history and preparation of requests. It does not check live calendar availability or execute calendar changes in the prepared-only scope.

### “Can the subscription agent change a plan or charge a card?”

The available scope includes local plan/status reads and saved request preparation. Payment-provider execution and billing changes are not included in that prepared-only capability.

### “Does an enabled marketplace agent mean our store is connected?”

No. Capability enablement and provider setup/readiness are separate. Confirm the tenant's marketplace authorization, connection and specific enabled actions before stating that a live store is connected.

### “Can the system issue and send invoices?”

The default runtime includes local invoice lookup/status scope. Invoice issuance, PDF delivery and MyInvois submission are not implemented in the described runtime.

### “Can it guarantee compliance?”

The HR implementation includes configurable and reviewable workflows, but it should not be represented as a legal or statutory compliance certification. Real payroll and employment configuration needs qualified review and customer-approved policy.

---

## 11. Customer-facing language guide

### Recommended phrases

- “The system supports…”
- “For a tenant where this capability is enabled and configured…”
- “The assistant prepares the request for authorized review.”
- “Access is checked against the staff member's current HR permissions.”
- “This is local browser dictation, subject to browser and language-pack support.”
- “The console reports implementation and readiness separately.”
- “We would verify this integration in your environment before confirming rollout.”

### Avoid without environment-specific evidence

- “The voice agent is live for customers.”
- “It supports all languages” or “it understands Malay/Manglish/Tamil/Chinese accurately.”
- “Payroll is fully compliant and automatic.”
- “Bookings are confirmed automatically.”
- “The marketplace is connected” based only on an agent catalogue entry.
- “The agent can take any action in your systems.”
- “Everything shown in the console is production enabled.”

---

## 12. Pre-presentation checklist

### Content and scope

- [ ] Confirm the presentation emphasis: HR and staff dictation are available for the demo; customer interactive voice is described as planned unless separately accepted.
- [ ] Confirm which agent capabilities are enabled and configured in the actual demo environment.
- [ ] Avoid implying that prepared-only booking/subscription requests execute external actions.
- [ ] Confirm any claims about marketplace, invoice or provider connections against that specific tenant.
- [ ] For payroll questions, distinguish workflow implementation from statutory/compliance review and payment execution.

### Demo setup

- [ ] Use a synthetic/demo tenant and records.
- [ ] Confirm the presenter account has the intended console role and HR grants.
- [ ] Prepare a synthetic training request and reviewer if showing human confirmation.
- [ ] Confirm the browser supports local dictation and has the intended local language pack; have typed input ready as a fallback.
- [ ] Prepare a known ingested knowledge source and an example agent activity record.
- [ ] Do not open `.env.local`, secrets, production records or unrelated tenant data during screen sharing.

### Suggested final close

> “CSA provides a governed foundation for AI-assisted customer service and internal operations. The HR workflows and console are designed to keep access and human decisions explicit, while additional voice and provider capabilities can be introduced when their integration and acceptance requirements are met.”

---

## 13. Source references

This brief reflects the repository's implementation and progress documentation, not a live readiness check of a particular customer deployment.

- [HR implementation progress and feature matrix](PROGRESS.md)
- [Current domain and release handoff](../handoffs/2026-10-19-domain-release-next-session.md)
- [Current product progress](../PROGRESS.md)
- [Runtime agent catalogue](../../csa/console/agents.py)
- [Customer runtime capability registration](../../csa/turn/deps.py)
- [HR agent dispatcher](../../csa/hr/agents.py)
- [HR assistant planner](../../csa/hr/assistant.py)
- [HR local voice implementation](../../CSA_WebConsole/csa-console/web/lib/hr-voice.ts)
- [HR assistant interface](../../CSA_WebConsole/csa-console/web/components/console/HrAssistant.tsx)
- [HR workspace interface](../../CSA_WebConsole/csa-console/web/components/console/HrWorkspace.tsx)
- [Agent catalogue and console roadmap](../langgraph-rebuild/LG13_agent_catalog_and_console.md)
- [Voice ingress and transcription design](../langgraph-rebuild/LG03_ingress_gateway_channels.md)

