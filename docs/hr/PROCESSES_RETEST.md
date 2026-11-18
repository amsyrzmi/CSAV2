# HR processes: owner's staging checks

Run rows 1 to 13 on **staging** after the HR processes console (PR 3, migration `0636`) is rolled
out. Rows 14 to 21 check the org chart and the default templates (PR 4, no migration) and are run
after that rollout. They need a few minutes each, except rows 5, 8 and 9, which wait for the worker
or for a day to pass.
Tick each row, write what you saw in **Result**, and stop at the first row that does not match.

**Before you start.**

- The staging data is synthetic. Use only made-up names (the rows use "Test Person Alpha" and
  "Synthetic onboarding"). Do not type a real person's name, number or address, and do not run
  this on production.
- Sign in to the staging console as the HR owner of the staging test company. Rows 4 and 11 also
  need a second browser profile (or a private window) signed in as a staff member of the same
  company. Row 4 needs a second person who can approve (see row 4). Row 11 needs a read-only
  member, which the owner sets on the Team page, linked to a person record.
- Rows 14 to 19 use the org chart (**People → Org chart**). Row 15 needs a plain colleague signed in
  with the directory switch only, and a person who is leaving and has a manager who also has the
  directory switch. Row 21 needs a company with no active onboarding or offboarding template: use
  a second synthetic test company, or deactivate the templates from rows 1 and 7 first.
- Reminders come from the background worker, which runs every 15 minutes. After the step that
  creates a task, wait up to 15 minutes before you look for its reminder (rows 5 and 8).
- Report issues to the person running this build session. Give the row number, what you did,
  what you saw, and a screenshot with no real data in it.

## The rows

### 1. Create the first onboarding template

Do: open **People & HR**, then **Processes**, then **Templates**, then **New template**. Name it
`Synthetic onboarding`, choose **Onboarding**, and leave **Applies to** empty. Add three steps:

- Step 1: owner **HR team**, days **2**, counted from **Start date**.
- Step 2: owner **The person's manager**, days **5**, counted from **Start date**.
- Step 3: owner **The person**, days **7**, counted from **Start date**, approval by **HR team**.

Give each step a short title (English; Malay if you like) and press **Save**.

You should see: each step shows its sentence under the days ("2 days after the start date" and so
on); the preview line says how many people the template applies to now ("This applies to N people
now."); after the save the template opens on its own page and appears in the Templates list as
Active, applies to "Everyone".

Result: ____________________

### 2. Add a synthetic person with a start date

Do: open **People**, then **Add person**. Use the name `Test Person Alpha`, a made-up work email
on a staging test domain, a manager, and a start date of today. The manager must be a synthetic
staff member **whose person record is linked to a console account** (Team page); otherwise the
manager step goes to the HR team instead, and step 2 of row 4 cannot be done.

You should see: the person is added and opens on their profile.

Result: ____________________

### 3. The onboarding process appears

Do: open **Processes**. The list opens on **In progress**. Open the new process.

You should see: Test Person Alpha's onboarding is in the list with "0 of 3 steps". Its page shows
the template name, the status and the start date (written like "17 Nov 2026") in the header, then
the three steps in order with their owners and their due dates in words: "Due in 2 days", "Due in
5 days" and "Due in 7 days" (a step shows its date, "Due on 19 Nov 2026", only once it is done or
skipped). History (collapsed) shows the process starting.

Result: ____________________

### 4. Complete, approve and send back

**Send back and Approve need a second person.** They are offered (in **My tasks**, not on the
process page) only to an approver who did not complete the step: the manager for a step approved
by the manager, and for a step approved by the HR team (the person step here) a member of the HR
team other than the one who completed it. Set it up one of two ways:

- Link a console account to Test Person Alpha (Team page), so the person completes their own step
  and you, the HR owner, approve it; or
- Complete it for them as HR (**Mark done for them**) and approve it as a different HR team member
  (a second person with Manage people).

If you are the only HR team member (no other enabled, not read-only member is an HR owner or holds
Manage people) and you use **Mark done for them**, you are the one who completed the step, and the
console still offers you **Approve** and **Send back**: the approval is recorded in History as
"Approved without a second person: no one else in HR could approve it". If someone else in the
company has Manage people, the HR member who used **Mark done for them** sees no **Approve** and no
**Send back** for that step; the other HR team member does.

Do:

1. Open **My tasks** on Home as the HR owner. Press **Mark done** on the HR step (add a short
   note if you like).
2. Sign in as the manager (second browser profile) and mark the manager step done.
3. For the person step: if Test Person Alpha has a console account, sign in as them and press
   **Mark done**; if not, as HR press **Mark done for them**. The step now waits for approval.
4. As the approver (see above), press **Send back** on that step in **My tasks**. A reason is
   required: type `Please attach the signed form`. Press the button.
5. Sign in as the person (or, without a console account, mark it done for them again as HR). Mark
   the step done again.
6. As the approver, press **Approve** in **My tasks**.

You should see: after step 1 the HR row disappears from My tasks and the process shows "1 of 3
steps"; after step 4 the step shows as sent back, and the person sees the line "Sent back:
Please attach the signed form" on their row; after step 6 the process shows "3 of 3 steps" and
its status becomes Completed. If you used **Mark done for them**, the step reads "Done by HR".

Result: ____________________

### 5. Reminders on Home, grouped and readable

Do: create a second synthetic process, or reuse a step that is due soon. After the next worker
round (up to 15 minutes), open Home as the person or the HR owner who owns an open step. The HR
team sees the reminders in the **Local HR reminders** panel; staff without HR duties see them on
**My HR**, in **My reminders** under **My tasks**. A reminder goes to the person who owns the step
when they have a console account that is enabled and not read-only; otherwise it goes to the HR
team.

You should see: reminder lines with a readable subject (the step and the person) and a due date in
words, not a code. Older reminders (requests or training to review, upcoming interviews) read as
words such as "Requests to review", never a code or an id. When there are more than three, they are
grouped under headings such as Overdue, Due today, Due soon and New tasks, each with a count. Click
one: you land on My tasks. Press **Mark all read**: a progress line shows ("Marking 3 of 12..."),
the reminders become read, and the unread count falls. A reminder for a step that is already done
shows "Done" with no link.

Result: ____________________

### 6. Probation end date on the profile

Do: open Test Person Alpha's profile. Under **Onboarding & offboarding**, find **Set probation
end date**, choose a date three months after the start date, and press **Save**. Then press
**Clear**.

You should see: the profile shows the process section with the next step and the dates; after
Save the date is shown; after Clear it is gone. A made-up wrong value (leave the date empty and
press Save) says "Choose a date." and sends nothing.

Result: ____________________

### 7. Mark leaving, see the offboarding, undo it

Do: first create a minimal `Synthetic offboarding` template (kind **Offboarding**, one HR step, days
**0** counted from **Last day**) the same way as row 1. Then open Test Person Alpha's profile and
press **Mark as leaving**, choose a last day (today) and a reason (if the person has reports, also
who their reports move to). Open **Processes**. Then go back to the profile and press **Undo
leaving**.

You should see: an offboarding process for the person in the Processes list while they are
leaving; after **Undo leaving** it shows as Cancelled (change the status filter to **Any status**
to see it) and the person is active again.

Result: ____________________

### 8. Leave again and wait for the last day to pass

Do: press **Mark as leaving** again with the last day set to today. Leave the offboarding step
undone. The worker finishes the person the day after the last day; do the next row on that day,
after the person shows as **Left**. Also check Home for the offboarding reminder after the next
worker round.

You should see: the offboarding process is still in progress, and a reminder for its step appears
on Home within 15 minutes of the worker round.

Result: ____________________

### 9. Rehire warning

Do: on the day after the last day, open the profile of Test Person Alpha (now **Left**) and press
**Rehire**, then choose a start date.

You should see: **Rehire** first reads "Checking open processes..." for a moment. Because the
offboarding is still open, a question appears: rehiring cancels the offboarding and skips its
remaining steps, with **Continue** and **Keep the offboarding**. **Keep the offboarding** sends
nothing. **Continue** rehires the person, and the offboarding shows as Cancelled.

Result: ____________________

### 10. Edit the template in two windows

Do: open `Synthetic onboarding` in two browser windows. In window A change the name to `Synthetic
onboarding A` and save. In window B change the name to `Synthetic onboarding B` and press
**Save**.

You should see: window B does not lose what you typed. It shows that someone else changed the
template, with **Keep my changes** and **Show their version**. **Show their version** first asks
"Discard your changes?"; **Discard my changes** then loads the name from window A. **Keep my
changes** keeps `B` in the form with the sentence that saving now replaces their version; saving
then works. **Copy as new** opens a new template with "Copy of ..." and your steps.

Result: ____________________

### 11. A read-only member sees tasks without buttons

Do: sign in as a staff member whose access on the Team page is **Read-only**, whose console
account is linked to a person record, and who has an open step (or, as the HR owner, reassign one
to them from the process page first: a step can only be reassigned to a person record). Open Home
or **My HR**.

You should see: the member's tasks are listed with their titles and due dates, and there are no
**Mark done**, **Approve**, **Send back** or **Add note** buttons. Any Processes or template page
they can open has no Save, Start, Cancel or Skip buttons either.

Result: ____________________

### 12. A pasted list with tabs in a step description

Do: open a template, then paste this into a step's **Description** (the tab characters are
important; copy them from a spreadsheet or a text editor if your browser drops them):

```
- Collect the signed form
	- Scan it
- File it in the HR folder
```

Press **Save**.

You should see: the save works with no error. After the save the description keeps its three lines
and the tabs show as spaces. Pasting a control character (not a tab or a line break) marks that
field with a sentence, and nothing is sent.

Result: ____________________

### 13. Phone width, 390 px

Do: in the browser's developer tools, switch to a phone view that is 390 px wide. Open My tasks
(Home), the Processes list, a process page, the Templates list, and the template editor with three
steps.

You should see: nothing scrolls sideways on any of them; lists change from tables to cards; the
step fields in the editor stack one under another; buttons are tappable and none is cut off.

Result: ____________________

### 14. Org chart: collapse and expand with the keyboard

Do: open **People**, then **Org chart**. With the keyboard only, press Tab to a team's fold button,
press Enter, then press Enter again; try Space as well. Then press **Collapse all** and **Expand
all**.

You should see: each fold button names the manager ("Name's team (3)") and is a real button that
folds and unfolds that team with Enter or Space, keeping focus on the button; the first three levels
are open when the page loads and deeper teams are folded; **Collapse all** leaves only the people at
the top, and **Expand all** shows everyone. The line "N people shown" counts everyone, also when
teams are folded.

Result: ____________________

### 15. Org chart: department colour, name and markers

Do: on the org chart, look at each person's row. Include a person who starts in the future (a start
date after today) and a person marked as leaving. Then sign in as a plain colleague (second browser
profile, directory switch only) and as the leaving person's manager, and look at the same two
people.

You should see: each row shows the department as a coloured chip that always carries the department
name (the colour is never the only sign); a person who starts in the future has **Starting**; a
person who is leaving has **Leaving** for the HR owner and for their manager; the plain colleague
sees the starting person as **Starting** but the leaving person as an ordinary person with no
**Leaving** marker. The legal entity filter appears only for HR and only when someone has a legal
entity.

Result: ____________________

### 16. Org chart: people at the top

Do: make three situations with synthetic people: someone with no manager; someone whose manager has
left; two people who are each other's manager (change a manager on a profile; if the form refuses
to make a loop, skip that part and say so).

You should see: at the top of the chart, the first reads **No manager**, the second **Manager not
in the chart**, and each person in a loop **In a reporting loop**, and nobody is missing from the
chart. The HR owner also sees a notice naming the loop and how to fix it; a colleague does not.

Result: ____________________

### 17. Org chart: search, filters and counts

Do: type part of a name in **Search people**, then clear it and type part of a job title. Choose a
department, then (as the HR owner, when a person has a legal entity) a legal entity. Fold a team,
then search for someone inside it. Press **Clear filters**.

You should see: the chart narrows to the matches and the managers above them; "N people shown"
updates as you type; while you type, focus stays in the search box; a branch above a match opens by
itself while the filter is on; a team you folded earlier is folded again after **Clear filters**;
with no match the chart says "No one matches the search or the filters." with a **Clear filters**
button, and after **Clear filters** focus is on the "Reporting lines" heading.

Result: ____________________

### 18. Org chart: print and download CSV

Do: on the org chart, fold a team, then press **Print** and look at the print preview (do not
print). Close it and open another page (People list) in print preview. Back on the org chart, press
**Download CSV**, open the file in a spreadsheet, and give one synthetic person a name that starts
with `=` (for example `=Test`) before downloading to see how it appears.

You should see: the print preview shows the whole tree, including the folded team, without the side
menu, top bar and filters, on a white background with dark text; the other page's print preview is
normal. The button reads "Download CSV (N people)" and the file has exactly those people (the
filtered view when a filter is on), opens in the spreadsheet with Malay names intact and one row per
person, and the `=` name appears as text (it starts with an apostrophe), never as a formula.

Result: ____________________

### 19. Org chart at phone width, 390 px

Do: switch the browser to a 390 px phone view and open the org chart. Use the search, a fold button,
**Expand all** and the buttons.

You should see: nothing scrolls sideways; names, job titles and department chips wrap; fold buttons
and the other buttons are tappable and none is cut off.

Result: ____________________

### 20. Default templates: no button while the switch is off

Do: as the HR owner, open **Processes**, then **Templates**, on staging as it is shipped (the
switch `CSA_STAGING_HR_DEFAULT_TEMPLATES` not set).

You should see: **New template** only: no **Create the default templates** button and no hint about
default templates.

Result: ____________________

### 21. Default templates: create them once (only after the review is signed and the switch is on)

Run this row only after the reviewer has signed `docs/hr/DEFAULT_TEMPLATES_REVIEW.md`, the owner
has recorded it, and a reviewed pull request has set `CSA_STAGING_HR_DEFAULT_TEMPLATES=true` in
`deploy/staging-rollout.env` and staging was rolled out. Use a company with no active onboarding or
offboarding template.

Do:

1. As the HR owner, open **Processes**, then **Templates**. Press **Create the default templates**.
   Read the dialog and press its confirm button once.
2. Open each of the two new templates and edit one step's title; save.
3. Reload the Templates list.
4. Sign in as a read-only member of the HR team of the same company (Manage people, set to read
   only on the Team page) and open the Templates list, if the Processes tab shows for them.

You should see: the button appears beside **New template**; the dialog says what the two templates
are, that they are reminders with references and not filings, that they become the company defaults
(the next person added starts the onboarding with real tasks and reminders) and that HR can edit
them; after confirming, a notice says "Created" with links to **Onboarding (Malaysia default)** and
**Offboarding (Malaysia default)**, and both appear in the list as Active, applying to Everyone,
with their step counts (14 and 15). Both open in the editor and the edit saves as a new version.
After the reload there is no **Create the default templates** button. A read-only member sees no
button. With the switch turned back off, the button never shows.

Result: ____________________
