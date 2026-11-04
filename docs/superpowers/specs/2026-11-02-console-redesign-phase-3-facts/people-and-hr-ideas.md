# People & HR: ideas to make every tab easier for non-technical people

Read-only review of the worktree `feat-console-redesign-phase-2` (web app: `CSA_WebConsole/csa-console/web`). All file paths below are relative to that folder. Line numbers are real lines in each file (checked per file).

Short names: `hr/` = `components/console/hr/`. `copy` = `lib/hr-access-copy.ts`. `people` = `lib/hr-people.ts`.

Legend for effort: **S** = under half a day, **M** = 1 to 2 days, **L** = more. **FE** = front-end copy/layout only (safe). **BE** = needs a backend or contract change (flagged).

## What a user can reach (from `lib/hr-paths.ts:12-21`)

Tab bar, each tab hidden if the person has nothing to do there (`visibleTabs`, hr-paths.ts:102-117):
Home, People (List / Org chart / Departments, plus a profile page with Overview / Access / History, plus Add person), Recruitment, Leave & attendance, Payroll, Training, Requests, Settings (which contains the whole Access page "Who can do what", company features, recent changes, work calendar, background maintenance, private access and an Advanced box).
Staff without HR duties see "My HR" on Home instead of the HR Home. A hidden floating "HR chat (assistant & voice)" sits at the bottom of Home. Onboarding has no tab yet (hr-paths.ts:11). Platform admins also get a "Tenant membership" picker above the tabs.

Overall finding: nothing is a pure placeholder. People, Org chart, Departments, Profile, Add person, Home, My HR and Access are new, polished and plain-worded (most copy already follows WRITING_GUIDE). Recruitment, Leave, Payroll, Training, Requests and Settings (below the access page) are the OLD tools "moved, not rebuilt" (design spec section "Tabs and who sees them"). They are real but still speak in engineer and auditor language. That is where most of the comprehension pain is.

---

## 0. Whole-area shell (HrShell, HrTabBar)

1. **Does today:** header "People & HR", a row of tab buttons, then the chosen tab. Real.
2. **Hurdles**
   - Subtitle "Training, recruitment, attendance, leave and payroll preparation." (hr/HrShell.tsx:48) leaves out People, Home and Settings, which are the parts most people use first.
   - Tab names are department jargon with no hint of what is inside: "Requests" (HrTabBar via hr-paths.ts:19), "Leave & attendance", "Recruitment". A new owner cannot tell "Requests" from "Training" requests.
   - Platform admin "Tenant membership" and "Choose a tenant" (HrShell.tsx:51-53) show company codes, not names (`m.client_id`, line 53).
   - "Loading your HR access…" (HrShell.tsx:74) and "You don't have access to this part of People & HR." (80) say nothing about who to ask.
3. **Ideas**
   - Subtitle: "Keep your team's details, time off, hiring, training and payroll preparation in one place."
   - Under the tab bar show one grey line per tab: what it is for and the one main action ("People: everyone in your company. Add a person to start."). One shared `TabIntro` component, copy kept in one file.
   - Rename tabs: `Requests` -> `Approvals` for people who can decide, `Requests` for others (see section 8); `Leave & attendance` -> `Time off & attendance`.
   - Platform-admin picker: show company name where the data has it; label "Company" instead of "Tenant membership" (platform admin only, low priority).
   - No-access notice: "You don't have access to this part. Ask your HR owner, or go to HR Home."
4. **Effort:** S, FE. Company names in the picker may need a field the memberships do not carry (BE, only if absent).

---

## 1. Home (HomeTab, HR view)

1. **Does today:** for HR people, a "Needs you today" list (training and HR requests waiting, interviews, starters, unread notifications), quick-action buttons, a "Local HR reminders" panel, and a collapsed HR chat. Real, built from existing data.
2. **Hurdles**
   - Empty day is the usual day: "Nothing needs you today." (people:73) with no next step. A new company sees a blank page.
   - "Local HR reminders" (hr/HomeTab.tsx:91) explains itself with: "Reminders are deduplicated daily; expired candidate content is erased. No external messages are sent." (line 91). "deduplicated", "candidate content" and "Maintenance runs every five minutes" are system words.
   - Reminder rows show raw kinds and ids: `{reminder.kind.replaceAll("_"," ")} · {reminder.resource_id}` (HomeTab.tsx:95) reads like "training request submitted · 3f9c…". Interview rows show `{appointment.status}` raw (93) and use browser-locale dates (`toLocaleString`), not the guide's "27 Oct 2026, 09:15".
   - Two buttons that look the same: "Refresh reminders" (92) and the hidden "Mark read" (95). "Refresh" runs a maintenance job, which a user does not expect.
   - "HR chat (assistant & voice)" (HomeTab.tsx:29) is a second assistant next to the dock. It is a duplicate that confuses (design spec says it goes away in 1b).
   - Error footer "Check saved records before retrying an uncertain write." (HomeTab.tsx:90) is shown for any failure.
3. **Ideas**
   - First view = a "Get started" checklist when the company is new, driven by data Home already reads (people count from the People list read, departments from the departments read): 1 "Add your first person", 2 "Add your departments", 3 "Choose who can do what", 4 "Set holidays and working days". Each is a link; ticks disappear as done. Replace the empty line with: "Nothing needs you today. Next step: add your team." plus the first undone checklist link.
   - Quick actions first, list second: put "Add person", "Request training", "Open the access page" above the list; rename the last to "Set who can do what" (people:455).
   - Rename the panel "Local HR reminders" -> "Reminders". Text: "Your reminders appear here. We never send them outside this page." Move "Maintenance runs…" and "deduplicated…" behind a "More details" arrow. Rename "Refresh reminders" -> "Check for new reminders".
   - Reminder rows: map `kind` to a sentence ("A training request is waiting for you") and link to the record instead of printing `resource_id`. Show interview time with the shared date formatter (`formatDateTime`, as the Access page does) and a plain status word ("Booked", "Cancelled").
   - Move "HR chat" out of the first view: keep collapsed, rename to "Ask the assistant" with one line "The assistant is also at the side of every page." until 1b removes it.
   - Replace the generic error line with the Access page style: "We could not do that. Reload, then try again. Nothing was saved twice."
4. **Effort:** M (checklist), S (copy). FE. The checklist needs no new endpoint if it reuses the People and Departments reads; a "has the owner set holidays" tick would need the calendars read (already used on Leave/Settings).

## 1b. My HR (Home for staff without HR duties)

1. **Does today:** "My profile", "My leave balances", "My training requests" (hr/MyHr.tsx:50, 56, 62). Read-only plus "Request training". Real, but personal details, documents and leave requests are deferred (comment at MyHr.tsx:213).
2. **Hurdles**
   - Page is titled "Home" in the tab bar but is "My profile" inside, so staff do not know they are on their own page.
   - Unlinked message (people:75): "Your login isn't linked to an employee record yet. The HR owner links it from your profile's Access section." "login", "employee record", "Access section" are three ideas in one sentence; the person cannot open a profile that does not exist.
   - Balances empty: "No leave balances yet. Your HR owner adds them for each year." (MyHr.tsx:59). No way to ask for leave from this page (it lives in Requests).
   - Overview labels "Employment type", "Region", "Legal entity", "Employment category" (people:427-445) appear on a staff member's own profile with no meaning.
3. **Ideas**
   - Heading above the panels: "Your page" with one line "Your details, your leave and your training requests."
   - Unlinked: "We cannot find your employee details yet. Ask your HR owner to link your account. Until then you can still use the People list."
   - Add a plain button "Ask for leave" that links to Requests with the Leave form chosen (needs a query/route tweak, FE) and rename the empty balance line to "No leave set up for you yet. Ask your HR owner."
   - For staff, hide payroll-only rows (Region, Legal entity, Employment category) behind "More details".
4. **Effort:** S-M, FE. Do not build leave self-service here; that is project 4.

---

## 2. People list

1. **Does today:** a searchable, filterable table (Name, Title, Department, Manager, Status) with "Add person" for editors. Real (hr/PeopleTab.tsx:72-170).
2. **Hurdles**
   - Four controls above an empty list: Search, Department, Manager, Status (PeopleTab.tsx:113-145) on day one, when there is nobody to filter.
   - Status choices "Active and starting" / "Active" / "Starting" / "Left" (138-140, people:14): "Starting" and "Left" are not explained; "Starting" is easily read as "in progress".
   - Empty text "No one has been added yet. Choose Add person to add the first one." (people:62) is good, but the button sits in a different panel below the filters (147), so the eye misses it.
   - Switcher "List | Org chart | Departments" (32-35) looks like another tab bar under the main tab bar: two rows of identical buttons.
   - "Showing 20 of 134" + "Load more" (163-166) asks the user to count.
3. **Ideas**
   - Empty company: hide the filters entirely, show one large card: "Your people will appear here. Add the first person." with the button. Show filters only from the first person on.
   - Rename statuses with a tooltip or `title`: Active -> "Working here"; Starting -> "Starts soon" (start date in the future); Left -> "Left the company". Chip colours already exist (`STATUS_TONES`, PeopleTab.tsx:26).
   - Move "Add person" to the top right of the page heading, always visible.
   - Make the view switcher visually smaller (text links or segmented control) and rename: `List` -> "Everyone", `Org chart` -> "Who reports to whom", `Departments` -> "Departments". Keep "Org chart" in a `title` so searchers find it.
   - Search placeholder (126) "Name, job title or work email" is good; keep. Collapse Department, Manager, Status into "Filters" arrow when fewer than 8 people.
4. **Effort:** S. FE.

## 3. Org chart

1. **Does today:** an indented tree by manager, each name a link to the profile. Real (hr/OrgChart.tsx).
2. **Hurdles**
   - It is a plain bulleted tree, not a "chart"; users expecting boxes do not know why it looks like a list (OrgChart.tsx:7-15).
   - Loop warning: "These people are each other's managers in a loop: A, B. Change a manager on their profile to fix it." (OrgChart.tsx:26). "loop" is not everyday.
   - Empty: "The org chart shows up once people are added." (people:70). Fine, but no explanation that it is built from the Manager field.
   - No hint how to fix a wrong chart: nothing says "to move someone, edit their manager".
3. **Ideas**
   - One-line intro: "This shows who reports to whom. It comes from each person's Manager. To change it, open a person and choose Edit."
   - Loop wording: "A and B are each other's manager, so the chart cannot place them. Open one of them and change their manager."
   - Show headcount on each node ("Finance, 4 people") and a "Show less / Show more" arrow per branch to cut long trees (FE only; counts computed from the same nodes).
   - Cosmetic box layout belongs to the redesign session; do not build it here.
4. **Effort:** S. FE.

## 4. Departments

1. **Does today:** a table of departments with people count, status, rename and archive/unarchive. Real (hr/Departments.tsx).
2. **Hurdles**
   - "Archive" and "Archived / In use" (Departments.tsx:65, 73) are record-keeping words; owners say "close" or "hide".
   - Buttons read "Rename Finance" / "Archive Finance" (69, 73): repeated per row, heavy.
   - Footnote "An archived department keeps its people until you move them." (78) is at the bottom, after the action, where it is read too late.
   - Column "Actions" (56) is empty jargon; "People" column is a number with no link to who.
3. **Ideas**
   - "Archive" -> "Hide from lists"; status "In use" -> "Active", "Archived" -> "Hidden". Put the footnote in a confirmation line shown when pressing the button: "Finance has 4 people. They stay in Finance until you move them. Hide it?"
   - Make the "People" number a link to the People list pre-filtered by that department (the filter exists: PeopleTab.tsx:129-134).
   - Shorten the row buttons to "Rename" and "Hide" (the row already shows the name); keep the full name in an `aria-label` for screen readers. Rename the "Actions" column header to nothing or "Change".
   - Empty state with example: "Add a department such as Sales, Finance or Operations." (people:66 plus examples).
4. **Effort:** S. FE (the list filter link may need the list to accept `?department=` in the URL: small FE).

## 5. Person profile (Overview / Access / History)

1. **Does today:** a person page with Overview (read, Edit), Access (their HR switches, owner-only account link) and History (who changed what). Real (hr/PersonProfile.tsx).
2. **Hurdles**
   - Three sub-tabs "Overview / Access / History" (people:339) plus the main tabs: third level of navigation.
   - Access tab shows pill tags like "Owner of People & HR", then each switch label ("See everyone's basic details"...) (PersonProfile.tsx:114-116, 124) with no explanation, and says "Console account: A member" (121) when the person has no name.
   - "No console account is linked." (119) and "Change on the access page" (127): "console account", "access page" are internal. A non-technical owner thinks "my staff do not have a console, they have emails".
   - Owner sees "Console account" picker with "Link account" and "Unlink account" (PersonProfile.tsx:158-160) but no sentence on what linking does (that the person can then log in and see My HR).
   - Overview rows "Employment type", "Region", "Legal entity", "Employment category" (people:427-445) mix with simple rows; empty ones show "—".
   - History shows only "A former member" and field names; fine for admins, long for owners ("Showing the latest 200 changes.", line 184).
3. **Ideas**
   - Rename sub-tabs: `Overview` -> "Details", `Access` -> "Login and permissions", `History` -> "Changes". Only show Access/History when the server lists them (already true).
   - Access text: "Login: not linked. This person cannot sign in yet. Choose a team member below to link them." / "Login: Siti Aminah." Rename `Console account` -> "Login" and `Link account` -> "Link login", `Unlink account` -> "Unlink login (they can no longer sign in)".
   - Show the person's permissions as a plain sentence list using the existing explanation strings (`SWITCH_COPY.*.explanation`, copy:11, 17, 23...) instead of only tags. Tags become tooltips.
   - Group Overview: top block (Name, Job title, Department, Manager, Work email, Work phone, Start date) then a "More details" arrow containing Employment type and the three payroll fields. Add a one-line label "Used for payroll" there.
   - "Edit" -> "Edit details"; after Save the message "Changes saved." (PersonProfile.tsx:87) -> "Saved. The change is in History."
4. **Effort:** S-M. FE.

## 6. Add person (and Edit form, same component)

1. **Does today:** one form: Name, Job title, Start date, Manager, Department (or "New department…"), Work email, Work phone, Employment type, and a group of three payroll fields (hr/PersonForm.tsx:86-148). Real; Add keeps one employee id so a retry cannot add twice (comment lines 19-26).
2. **Hurdles**
   - Twelve fields on one screen, three of them payroll fields (PersonForm.tsx:121-132) with the legend "Employment details for payroll (optional; fill in all three or none)" (122). The "all or none" rule is a trap: a partial entry only fails after Submit (people:44, 133-137).
   - "Legal entity" and "Employment category" are free text with no example; many owners will not know what to type (128-131).
   - "Region" choices "Peninsular Malaysia / Labuan / Sabah / Sarawak" (HrForm.tsx:7) appear without saying why (public holidays and payroll rules).
   - Manager default "No manager (top of company)" (people:26) is a good label, but a new owner adding themselves first sees this and a required "Job title" and "Start date" only (92-95), which they may not know for the owner.
   - Two near-identical selects: "Manager" and "Department" next to "New department…" which reveals a new field (109-110) with `autoFocus`; the order surprises.
   - Title "Add person" and button "Add person" both say the same; Cancel is a link, not a button (141-142).
3. **Ideas**
   - Two steps, same page, no new route: **Step 1 "Who is joining?"** Name, Job title, Start date (default: today), Manager, Department. **Step 2 "More details (optional)"**: Work email, Work phone, Employment type, and a nested arrow "Payroll details (only needed when you prepare their pay)" holding the three fields.
   - Replace the three payroll text boxes: pre-fill Region from the company's most used region; Legal entity -> "Company name on payslips" with an example placeholder "Maju Sdn Bhd"; Employment category -> a short select with an "Other" free-text if the backend contract allows (needs BE only if it must be an enum; as free text this is FE only, so keep text and add examples such as "Executive, Non-executive, Probation").
   - Show a live inline hint under the three fields: "Fill in all three, or leave all three empty." Turn the red error on blur instead of on Submit.
   - Pre-fill the owner row: if the company has no people yet, show "Start with yourself. Add your own name and job title first." Default Manager to "No manager (top of company)" automatically for the first person.
   - Title: "Add a person". Primary button: "Save person". Add "Add another person" as a secondary button on the success page (currently it jumps to the new profile, PersonForm.tsx:82).
4. **Effort:** M (two-step), S (hints and examples). FE only.

---

## 7. Leave & attendance

1. **Does today:** shows "Reviewed leave balances", "Work calendars" (read), and for the HR owner a "Leave allocation" form; for attendance people, "Attendance import" (CSV), "Cancel shift" and "Shift schedule". Real but only the setup half: staff cannot request leave here (that is under Requests; self-service is project 4). (hr/LeaveTab.tsx:38-55)
2. **Hurdles**
   - Panel names invent a process: "Reviewed leave balances" (LeaveTab.tsx:38), "Reviewed allocated days" and "Reviewed policy reference" (42). "Reviewed" appears nine times on the tab; users do not know what review is needed.
   - Balance lines print raw leave type: `2026 annual: 12 days remaining` (38) where My HR prints "2026 · Annual leave: 12 of 14 days left" (people:476). Same data, two voices. Employee ids show when a name is unknown.
   - Attendance import: "Columns: starts_at, ends_at, region, timezone, reason. Use aware ISO timestamps. Imports remain pending manager review; the batch saves atomically." (LeaveTab.tsx:51) is unreadable to an office clerk.
   - "Reviewed CSV (up to 20 records)" (45) and error "Choose a CSV smaller than 64 KB." (47) have no download of a template.
   - "Start (Malaysia time)" / "End (Malaysia time)" (54) use browser date-time boxes that default to blank; "Cancel shift" asks for "Cancellation reason" (52) before you can see which shift.
   - "Work calendars" says "The HR owner adds them in Settings." (39) as plain text, no link.
3. **Ideas**
   - Open the tab with a plain explainer and the three things people do: "1 Set up leave days for each person. 2 Check balances. 3 Plan shifts." and one button per job.
   - Rename panels: "Reviewed leave balances" -> "Leave balances"; "Leave allocation" -> "Give leave days"; "Reviewed allocated days" -> "Days for this year"; "Reviewed policy reference" -> "Policy note (optional example: Employee handbook, section 4)" (make it optional in the FE if the contract allows: it is required today, `ref("policy",…)` -> check BE before changing `required`).
   - Reuse `leaveLabel` and the "x of y days left" sentence from `people:459-485` so every balance reads the same, with names not ids.
   - Attendance import: replace jargon with "Upload a spreadsheet (CSV). Download the template. Each row is one working day: start, end, region, time zone, reason." Add a "Download template" link (static file, FE). Move "pending manager review; the batch saves atomically" behind "More details".
   - Shift schedule: use the same date-time picker but default Start to next working day 09:00 and End to 18:00; show the time-zone note once under the section, not on each field.
   - Make "Work calendars" read "Public holidays and working days" and link "Set them in Settings" (HR owner only).
4. **Effort:** M. FE for copy and layout; the policy-reference optional is a BE contract question (flag). Do not add a leave request form here: leave self-service replaces this tab in project 4.

---

## 8. Requests

1. **Does today:** one panel to "Prepare an HR request" (recruitment interview guide, attendance correction, leave request, payroll preparation) and a list of requests awaiting decision, where authorised people approve, reject or withdraw. Real (hr/RequestsPanel.tsx).
2. **Hurdles**
   - Tab name "Requests" and panel titles "Prepare an HR request" (88) and "HR requests awaiting or recording decisions" (109): long and mixes "awaiting" with "recording".
   - First control is "Workflow" (89) with options "Recruitment interview guide / Attendance or correction / Leave request / Payroll preparation (MYR)" (11-14). "Workflow" is a system word; three of the four options are not requests a staff member makes.
   - A tick box: "Use enabled staff agents for preparation. Human decisions still require review." (80-81). Nobody knows what a "staff agent" is, or that it means the AI.
   - Request cards: title `{domain} · {status}` printed raw and lowercase ("leave · pending") (111), then "Reference 3f9c… · version 2" (111) and a grid of raw keys with underscores replaced ("starts on", "leave type") (112-113).
   - Buttons are bare lowercase verbs: "approve", "reject", "withdraw" (118). The review step says "Action: approve. Current version: 2." and "Continue with approve" (83, 85).
   - "Workforce region" (91) and "Original approved attendance reference (for corrections)" (97) require knowledge staff do not have.
3. **Ideas**
   - Rename tab and panels: "Requests" (for staff) / "Approvals" (shown only to decision makers, same route); "Prepare an HR request" -> "New request"; list -> "Waiting for a decision" and "Done" (two tidy lists using `item.status`).
   - Replace "Workflow" with "What do you need?" and relabel options: "Ask for leave", "Correct my attendance", "Prepare payroll for approval", "Interview questions for a role".
   - Tick box: "Let the assistant draft this for me. You still check and send it." (keeps the safety message: a human decides).
   - Card header as a sentence: "Leave request from Siti, 12 to 14 Nov. Waiting for a decision." Hide "Reference" and "version" behind "More details"; show dates with the shared formatter.
   - Buttons: "Approve", "Not approve" (match the Training copy `Not approved`, people:481), "Withdraw request". Review panel: "Approve this request?" with an optional "Reason (required when you do not approve)" and buttons "Approve" / "Cancel".
   - Replace "Workforce region" with the person's region when known (read from their profile), else ask once with a hint "Where do they work?".
4. **Effort:** M. FE. The prefill-by-profile idea reads data that exists but check endpoints (small BE risk).

---

## 9. Training

1. **Does today:** request training (title + learning objective), submit/withdraw your own drafts, approve or reject as a decision maker. Real (hr/TrainingTab.tsx).
2. **Hurdles**
   - Status line prints raw `{item.status} · version {item.version}` (TrainingTab.tsx:100), e.g. "submitted · version 3", while My HR shows "Waiting for a decision" (people:481). Two vocabularies for one thing.
   - Action buttons are lowercase single words: "submit", "withdraw", "approve", "reject" (105). The review panel says "Action: submit. Current version: 1." and "Continue with submit" (88, 90).
   - "Learning objective" (94) has no example; people leave it blank or write one word.
   - Ticking "Use enabled staff agents for preparation. Human decisions still require review." (74-75) appears on screen to every writer.
   - Confirm panel prints "approve request 8d1f…, version 2." (77) and "Expires 3/11/2026, 4:15:00 PM." (79) in browser format.
   - "No requests loaded." (97) reads like a fault, not an empty state.
   - Two-step feel is hidden: "Save draft" (95) then "Training draft saved. Submit it when ready." (43), then a button called "submit" on the card.
3. **Ideas**
   - Use the My HR status words everywhere: Draft / Waiting for a decision / Approved / Not approved / Withdrawn, as coloured chips (the `StatusLabel` chip used in PeopleTab).
   - Capitalised verb buttons that say what happens: "Send for approval", "Withdraw request", "Approve", "Do not approve". Review title: "Approve 'Excel basics'?".
   - Form: title -> "What training do you want?" placeholder "Excel basics course"; objective -> "What will it help you do?" placeholder "Prepare monthly sales reports faster". Primary button "Save as draft" plus a second one, "Save and send for approval" (FE chains the two existing calls; check it does not break the proposal-confirm flow at TrainingTab.tsx:116-122 first).
   - Empty list: "No training requests yet. Fill in the form above to ask for one." Drop "version N" from the card unless inside "More details".
   - Rename "Use enabled staff agents…" -> "Let the assistant draft this" (as in Requests).
4. **Effort:** S-M. FE. Do not alter the confirm card and version rules (data safety).

---

## 10. Recruitment

1. **Does today:** private tools: "Consented CV intake" (paste a CV and consent record), a "Candidates" list with "Open private CV", private notes, erase, and "Local interview schedule" / "Cancel local interview". People without the private area see recruitment requests instead (hr/RecruitmentTab.tsx:21-22). Real but a data store, not a hiring pipeline: no job openings, no stages (project 2).
2. **Hurdles**
   - "Consented CV intake" (RecruitmentTab.tsx:45) and "Recorded consent evidence reference" (45): legal vocabulary with no explanation of what to type.
   - "Retention deadline (Malaysia time)" (46) is a blank date-time box; many users will not know what to choose (the law behind it is the PDPA).
   - "Open private CV" (49), "Private candidate:" (51), "Human interview note" (53), label "Job-related evidence and human observations" (53). "Human" confuses: the user is a human.
   - "Withdraw consent and erase this CV and notes" then "Confirm erasure and cancel local interviews" (55): serious wording, but a user clicking "Withdraw consent" may not realise it erases.
   - "Local interview schedule" / "Cancel local interview" and "Creates a local appointment and reminder. External invitations require a configured calendar connection." (59, 57, 64). "Local" means "inside this system only", not "in person".
   - Candidates list is only "Name · retain until 12/03/2027" (49).
3. **Ideas**
   - Intro line: "Keep candidates' CVs and interview notes private. Only people with recruitment access can open them."
   - Rename: "Consented CV intake" -> "Add a candidate"; "Candidate display name" -> "Candidate name"; "Recorded consent evidence reference" -> "Where is their consent recorded? (for example: signed form or email, 3 Nov 2026)"; "Retention deadline" -> "Delete this CV on" with a date picker (not date-time) defaulting to six months from today (editable). Keep "Why we ask" in an arrow: the CV is erased automatically after this date.
   - "Open private CV" -> "Open CV and notes"; "Human interview note" -> "Add an interview note" with helper "Write what you saw and heard that relates to the job. No personal opinions about age, race or religion."
   - Erase: "Withdraw consent and erase this CV and notes" -> "Delete this candidate's CV and notes" with confirm text "This cannot be undone. It also cancels their booked interviews." (rephrased, not removed).
   - Interviews: "Local interview schedule" -> "Book an interview"; "Cancel local interview" -> "Cancel an interview"; replace the help line with "This adds the interview to this system and sets a reminder. It does not email the candidate." Move the time-zone select behind an arrow (default Kuala Lumpur).
   - Candidates list: add status chip "Interview booked" if an interview exists (data is already loaded: `interviews`).
4. **Effort:** M. FE. Do not weaken consent or erase semantics. Project 2 will replace the whole tab (openings, CV files, booking), so keep this to copy and light layout.

---

## 11. Payroll

1. **Does today:** three forms and one result table: "Reviewed contribution rules" (EPF/SOCSO/EIS CSV), "Payroll snapshot" (CSV of reviewed lines), "Private payroll review" (open a snapshot, approve as a second person, reverse). It prepares, never pays (notice at hr/PayrollTab.tsx:41). Real but specialist; project 5 rebuilds it.
2. **Hurdles**
   - The first thing is a dense Notice: "Financial actions are manual. Contribution tables and employment categories need qualified review. PCB values come from a separately reviewed calculation. This workspace does not send payments or statutory filings." (PayrollTab.tsx:41). Four ideas, three unexplained acronyms.
   - Acronyms everywhere: EPF, SOCSO, EIS, PCB (table headers, line 67; CSV text 55, 63).
   - Form 1 asks: "Legal entity reference", "Employment category", "Official source / reviewed version reference", "Reviewer qualification evidence", "Reviewed contribution bands CSV" (42-44).
   - CSV instructions list column names in code style (55, 63): "scheme, above, through, employee, employer" and "employee_id, gross, epf_wage, socso_wage, eis_wage, reviewed_pcb, reviewed_other_deductions, tax_review_reference". An office clerk cannot make this file, and `employee_id` is a UUID they cannot see.
   - Result table shows `employee_id` UUIDs in the Employee column (68), "MYR net total" (66) instead of "RM", and panel title `Payroll prepared` (66).
   - The approve button: "Confirm reviewed amounts as a distinct reviewer" (72) and "Payroll reversal / Reason for reversing this local run" (73).
   - "Payroll request (create under Requests)" (56) sends the user to another tab to start.
3. **Ideas**
   - Replace the notice with three plain lines and an arrow: "This page helps you prepare payroll. It does not pay anyone and does not file anything with the government. A second person must check the amounts before they count." (Same safety, plain.) Details in "More details".
   - Show Malaysian short names with the local name: "EPF (KWSP)", "SOCSO (PERKESO)", "EIS (SIP)", "PCB (monthly tax deduction)". Copy only, console stays English per the spec.
   - Add a "Steps" strip at the top: "1 Set contribution rules, 2 Upload pay lines, 3 Second person checks, 4 Done (nothing is paid here)". Anchor links only (FE).
   - Employee column: show names (`usePeopleOptions` already loads them for Leave), amounts as "RM 2,500.00" using the guide format.
   - Button: "Confirm amounts (second reviewer)"; add the line "You can only do this if you did not prepare this payroll." (the rule exists, `prepares_and_reviews` warning in copy:121).
   - Offer downloadable CSV templates with example rows. Rename "Reviewer qualification evidence" -> "Who checked these rates, and their qualification?" and the file label -> "Upload contribution rates (CSV)".
4. **Effort:** M (copy and steps), L only if you also redesign. FE. Mostly leave alone: project 5 replaces it, and maker/checker is a safety control that must stay.

---

## 12. Settings (including the Access page)

### 12a. Settings page as a whole (hr/SettingsTab.tsx)

1. **Does today:** the Access page ("Who can do what"), then Work calendar, Background maintenance, Private access, and an "Advanced" box with "Access profile". Owner only. Real.
2. **Hurdles**
   - One very long page whose first screen is the Access page (SettingsTab.tsx:36-38), 300 lines of interface, so "Work calendar" and reminders are found by scrolling past it.
   - Three overlapping ways to give access: the switches, "Private access" (45-47) and "Access profile" (52). Users cannot tell which to use.
   - "Background maintenance" (39) with "Retention and reminders", "Enable for this tenant", "Remind after pending hours" (40-41) and "Requires the separately configured maintenance worker…" (43). "tenant" and "worker" are system words.
   - Work calendar: "Working weekdays" is a tall multi-select (HrForm.tsx:26 `h-36`) that needs Ctrl-click; "Reviewed holiday dates, separated by commas (YYYY-MM-DD)" (36) is typed text.
   - "Private access": "Private capability", "CV intake, notes and interviews", "Grant explicitly" / "Revoke" (46-47).
   - Advanced: "Replace action grants with these selected profiles" and options like "Recorded client-owner decisions", "Remove action grants" (53). Pure jargon, but it sits under an arrow (good).
3. **Ideas**
   - Add a mini menu at the top of Settings (page anchors, not new routes): "Who can do what | Holidays and working days | Reminders | Private areas | Advanced". Keep the Access page open and in place (it is the main job); the menu stops people scrolling past it to find the calendar.
   - Rename: "Work calendar" -> "Holidays and working days"; "Background maintenance" -> "Automatic reminders and clean-up"; "Retention and reminders" -> "Turn on"; options "On" / "Off"; "Remind after pending hours" -> "Remind when a request waits more than (hours)"; "Enable for this tenant" -> "On for my company". Replace the final sentence with: "Reminders stay inside this system. They never send messages outside, and the clean-up only deletes expired candidate data."
   - Holidays: replace the comma text with a small "Add holiday" date picker that builds a list of chips (FE builds the same comma string for the same operation, `calendar.save`). Working weekdays: replace multi-select with seven tick boxes (Mon to Sun), default Mon to Fri.
   - "Private access" -> "Who can open private areas", options "Candidate CVs and interviews", "Attendance records and shifts", "Payroll records", decision "Allow" / "Stop allowing". Add: "These areas hold sensitive data, so they are separate from the switches above." (Keeps the safety reason.)
   - Keep Advanced collapsed, rename to "Advanced (for support)" and add "Most companies never need this." Do not delete.
4. **Effort:** M. FE. No contract change; the calendar chip picker still builds the same string.

### 12b. Access page "Who can do what" (components/console/HrAccessPage.tsx + HrAccessPanel.tsx + HrAccessMore.tsx + lib/hr-access-copy.ts)

1. **Does today:** left: a member list with search, Team / Reports to / Has switch filters, tick boxes, bulk Preset and Turn on / Turn off. Right: the selected member's nine switches in three groups, presets, "Copy access from…", custom access, warnings. Sticky bar "N changes for M people" with a review dialog before saving. Then "Waiting for access chosen on the invitation", "What the HR assistant can help with" and "Recent changes". Real; this is the polished part and the safety design (review dialog, warnings, version conflict) is excellent.
2. **Hurdles**
   - The word "switch" is used for what people call "permissions" or "what they can do": "Has switch / Any switch" (HrAccessPage.tsx:224-226), "Switch" bulk (243), "Turn on/off" (249-251), "switches" in the preset help (HrAccessPanel.tsx:77). Many owners do not call permissions switches.
   - Nine switches in three groups named "Everyday / Responsibilities / Private areas" (copy:4-5): the first view of any member is a wall of checkboxes with long labels ("Approves training, leave and attendance for their team", copy:20; "Can request their own training, leave and attendance", copy:14).
   - Preset names "Staff / Manager / HR team / Payroll preparer / Payroll reviewer" (copy:65-69) are good but are hidden in a "Preset: Add a preset…" select (HrAccessPanel.tsx:58-63) instead of being the main choice.
   - Overlapping ideas on one panel: "Preset", "Copy access from…", ticks, bulk Preset, bulk Switch (HrAccessPage.tsx:231-254). Two different "presets" places confuse.
   - Labels with internal tags: list tags "Directory", "Own requests", "Approves", "HR team" (copy:10, 16, 22, 28) vs. long labels in the panel; and "Included with {label}" (HrAccessPanel.tsx:91) is unexplained.
   - Three more panels below the fold: "What the HR assistant can help with" with toggles "Directory / Training / Recruitment / Attendance and leave / Payroll / HR assistant chat" (copy:93-98), whose first line says they don't change what members can do (copy:90) but sit right under member permissions.
   - "Custom access" block: "The switches don't describe this access. It came from earlier settings, and saving here keeps it." and "Change custom access in Advanced" (copy:72-75, HrAccessPanel.tsx:96-98).
   - Warnings are long conditional sentences, e.g. "manager … can't approve their training, leave or attendance. Link the manager to a member in Advanced." (copy:110-125). "Link the manager to a member" is hard.
3. **Ideas**
   - Rename "switch" -> "permission" (copy-only constant sweep: "Has switch" -> "Has permission"; "Turn on"/"Turn off" -> "Give"/"Remove"). "Turning on" / "Turning off" (HrAccessPanel.tsx:90) -> "Will be given" / "Will be removed". Title "Who can do what" stays.
   - Role-first panel: show the presets as five radio-like cards ("Staff", "Manager", "HR team", "Payroll preparer", "Payroll reviewer" with their one-line description, copy:65-69) at the top. Below an arrow "Fine-tune individual permissions" holds the nine boxes. Preset cards call the same `applyPreset` so nothing changes in saved data (presets only add; keep the note "Presets only add permissions", rephrased: "Choosing a role adds permissions. It does not remove any").
   - Remove the "Copy access from…" select from the first view; keep under "Fine-tune" with the label "Copy permissions from another person".
   - Explain "Included with": "Already included because this person has 'HR team member'." (HrAccessPanel.tsx:91).
   - Merge "What the HR assistant can help with" under an arrow "Assistant settings" at the bottom. Keep its description (copy:90); it protects against a wrong assumption.
   - Warnings: add a button per warning that jumps to the fix when possible ("Turn on Approves for Razak"), and shorten: "Siti's manager Razak cannot approve her requests yet. Give Razak 'Approves training, leave and attendance for their team'." Keep "You can still save. These are warnings, not errors." (HrAccessPage.tsx:328).
   - Invitation block (copy:176-177) title -> "Invitations waiting for HR access" and each row "Apply access" -> "Give this access".
   - Bulk bar (HrAccessPage.tsx:231-254): keep, but rename "N members ticked" -> "N people selected".
4. **Effort:** M. FE mostly (string constants). The role-card arrangement is S-M FE. No safety wording removed; the review dialog and warnings stay. "Recent changes" in a collapse already exists.

---

## 13. Anything else found

- **Pickers fall back to typed references**: when the people or members read is denied or fails, a field shows "Employee reference" or "Console member reference" as a text box (hr/Pickers.tsx:46). A non-technical user cannot fill that. Idea: show "We could not load your team list. Reload the page." instead of the UUID text box for non-owner staff; keep the typed reference only for platform admins (S, FE; confirm the tools still work for platform admins first).
- **Shared Form button** always reads `Save {title.toLowerCase()}` (hr/HrForm.tsx:31), giving "Save reviewed contribution rules", "Save cancel shift", "Save consented cv intake" (lowercase CV). Fix with a `submitLabel` prop per form: "Give leave days", "Book interview", "Cancel shift", "Add candidate" (S, FE; the writing guide asks for verb-first, sentence-case buttons).
- **Dates**: some screens use the browser's locale (`toLocaleString`, `toLocaleDateString`): HomeTab.tsx:93, RecruitmentTab.tsx:49, TrainingTab.tsx:79. WRITING_GUIDE wants "27 Oct 2026, 09:15" and money as "RM 59.90". Use one shared formatter (exists as `formatDateTime`, HrAccessMore.tsx:88; `dateText`/`shortWhen` in lib).
- **Error footer** "Check saved records before retrying an uncertain write." is repeated on Home, Leave, Payroll, Recruitment, Settings (e.g. LeaveTab.tsx:37). It is a developer instruction. Replace with one component: "We could not save that. Reload the page to check, then try again. Nothing is saved twice." (keep the reassurance).
- **Language**: console is English by design (spec non-goal). Add Malay parenthetical hints only for payroll acronyms (see Payroll) and leave types ("Cuti tahunan" next to "Annual leave" is optional and out of scope for this pass).

---

# Cross-cutting

## A. Vocabulary table (current -> proposed)

| Current term (where) | Proposed plain term |
|---|---|
| Tenant / "Tenant membership" / "for this tenant" (HrShell:51, SettingsTab:40) | Company / "my company" |
| Console account / Link account (PersonProfile:158-160) | Login / Link login |
| Console member / Member (SettingsTab:45) | Team member |
| Switch / Has switch / Turn on / Turn off (HrAccessPage:224-251) | Permission / Has permission / Give / Remove |
| Preset (HrAccessPanel:58) | Role (Staff, Manager, HR team, Payroll preparer, Payroll reviewer) |
| Directory (tag, copy:10) | Company phone book |
| Access (profile sub-tab, PersonProfile) | Login and permissions |
| History (profile sub-tab) | Changes |
| Workflow (RequestsPanel:89) | What do you need? / Request type |
| Staff agents / "enabled staff agents" (TrainingTab:75, RequestsPanel:81) | The assistant |
| Draft -> submit (TrainingTab:105) | Save draft -> Send for approval |
| submitted / pending / approved / rejected (raw, TrainingTab:100, RequestsPanel:111) | Waiting for a decision / Approved / Not approved |
| approve / reject / withdraw (lowercase buttons) | Approve / Do not approve / Withdraw request |
| version N, Reference <id> | hidden under "More details" |
| Archive / Archived / In use (Departments:65-73) | Hide / Hidden / Active |
| Starting / Left (people:14) | Starts soon / Left the company |
| Legal entity (PersonForm:128) | Company name on payslips |
| Employment category | Job grade or type (for payroll) |
| Region (PersonForm:123) | Where they work (state) |
| Reviewed X (Leave, Payroll: nine times) | drop the word "Reviewed" or say "Checked by HR" once |
| Local (Local interview, Local HR reminders) | drop; say "inside this system, nothing is sent out" once |
| Private access / Private capability (SettingsTab:45) | Who can open private areas |
| Background maintenance (SettingsTab:39) | Automatic reminders and clean-up |
| Retention deadline (RecruitmentTab:46) | Delete this CV on |
| MYR (PayrollTab:66, RequestsPanel:13) | RM |
| EPF / SOCSO / EIS / PCB (PayrollTab:67) | EPF (KWSP) / SOCSO (PERKESO) / EIS (SIP) / PCB (monthly tax deduction) |
| Distinct reviewer (PayrollTab:72) | A second person (not the one who prepared it) |
| Work calendar (SettingsTab:35) | Holidays and working days |
| Request training / Training requests (two panels, TrainingTab:93,97) | Ask for training / Your training requests |

## B. "First 5 minutes" journey for a brand-new company owner (what to build toward)

Assumption: the HR owner is already recorded for the company (that happens outside the console, see docs/hr/SETUP.md step 2; if not, Home shows the unlinked sentence at people:75, which is a dead end for a brand-new owner, flag as BE/onboarding, not a UI fix).
1. Open People & HR. Home shows "Welcome. 4 steps to set up your team" (checklist), not "Nothing needs you today."
2. Step 1 "Add yourself": the form opens with Manager = "No manager (top of company)", Start date = today. Name and Job title only; everything else under "More details".
3. Step 2 "Add your team" ("Add another person" button keeps them on the form) and "Add departments" with example chips (Sales, Finance, Operations).
4. Step 3 "Choose who can do what": five role cards (Staff, Manager, HR team, Payroll preparer, Payroll reviewer). Owner picks "Manager" for the two team leads. Warnings appear as "Things to check" with one-click jumps.
5. Step 4 "Set holidays and working days": Mon to Fri ticked by default; add public holidays with a date picker.
6. Done: Home shows the empty "Nothing needs you today. Your team is set up." plus quick actions (Add person, Ask for training, Open Settings). Leave, Payroll and Recruitment tabs remain visible only if the owner turned on those areas.

## C. Five highest-value changes overall, ranked

1. **One plain-word pass over the old tools** (Training, Requests, Leave, Payroll, Recruitment, Settings forms): status words, capitalised verb buttons, no raw ids/versions, dates and RM in the guide format, "Reviewed" and "Local" removed. Highest reach, lowest risk: S-M, FE.
2. **Home "Get started" checklist and plain empty states** replacing "Nothing needs you today." for a new company; quick actions above the list; reminders moved down. M, FE.
3. **Access page: role-first** (five role cards on top, nine permissions under "Fine-tune", "switch" renamed "permission", assistant settings under an arrow). It is the area most likely to confuse owners and where wrong choices matter. M, FE, keeps all warnings and the review dialog.
4. **Add person in two steps** with "More details" for email/phone/type and a nested "Payroll details", defaults (start date today, first person has no manager), "all three or none" hint. M, FE.
5. **Settings reorganised into plain short sections** (Holidays and working days with date picker and weekday ticks; Automatic reminders; Who can open private areas; Advanced collapsed), plus a one-line "what this tab is for" on every tab. M, FE.

(Runners-up: replace typed-reference fallbacks with a plain message; per-form submit labels; unify status chips.)

## D. What I would NOT change (owned elsewhere or replaced soon)

- **Project 1b scope (personal details, documents, leaving, assistant actions, retiring the HR chat on Home)**: do not touch the HR chat panel's content (just rename its label), My HR "documents" (not built), "Mark as leaving" (not built), Home's "leaving" items (passed as `leaving: []`, HomeTab.tsx:112 via people:306).
- **Project 2 (Recruitment)**, **3 (Onboarding/offboarding)**, **4 (Leave self-service)**, **5 (Payroll runs, payslips, statutory items)**, **6 (Claims, reviews, announcements)** replace the corresponding tabs. Only copy and light layout there; no new forms, no new capability. Do not invent job openings, leave request forms on the Leave tab, or payroll runs.
- **Console redesign phase 2 (another session)**: visual style, shared `ui` components (`Panel`, `Notice`, `Tabs`, `Pill`, `StatusLabel`), tab bar styling. Ideas above only change copy, order and disclosure, not styling.
- **Safety and permission wording**: the Access review dialog ("Save N changes for M people?"), warnings, version-conflict "Reload access", "Custom access" notice, the payroll "second reviewer" rule and "No payment or filing was sent", consent and erase confirmations, "Reason (omit medical details)". Rephrase only; never remove.
- **People refusal sentences** (people:29-45) and the uncertain-save sentences (people:48-51): already follow WRITING_GUIDE and are tied to tests.
- **Idempotency, version checks and one-employee-id-per-form behaviour** (PersonForm.tsx:19-26, 41): invisible to users, essential to safety.
- **Route and tab structure** (`hr-paths.ts`): URLs and legacy `?section=` redirects are used by bookmarks and the assistant's navigate. Renaming labels is fine, changing segments is not.
- **Malay UI translation**: spec non-goal (console stays English; the assistant answers in the user's language).
