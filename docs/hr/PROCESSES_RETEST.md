# HR processes: owner's staging checks

Run these rows on **staging** after the HR processes console (PR 3, migration `0636`) is rolled out.
They need a few minutes each, except rows 5, 8 and 9, which wait for the worker or for a day to
pass.
Tick each row, write what you saw in **Result**, and stop at the first row that does not match.

**Before you start.**

- The staging data is synthetic. Use only made-up names (the rows use "Test Person Alpha" and
  "Synthetic onboarding"). Do not type a real person's name, number or address, and do not run
  this on production.
- Sign in to the staging console as the HR owner of the staging test company. Rows 4 and 11 also
  need a second browser profile (or a private window) signed in as a staff member of the same
  company. Row 11 needs a read-only member, which the owner sets on the Team page.
- Reminders come from the background worker, which runs every 15 minutes. After the step that
  creates a task, wait up to 15 minutes before you look for its reminder (rows 5 and 8).
- Report anything that does not match to the engineer who rolled staging out. Give the row
  number, what you did, what you saw, and a screenshot with no real data in it. Row 14 is not
  part of this list.

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
on a staging test domain, a manager (a synthetic staff member), and a start date of today.

You should see: the person is added and opens on their profile.

Result: ____________________

### 3. The onboarding process appears

Do: open **Processes**. The list opens on **In progress**. Open the new process.

You should see: Test Person Alpha's onboarding is in the list with "0 of 3 steps". Its page shows
the template name, the status, the three steps in order with their owners, and due dates 2, 5 and
7 days after the start date (so, today plus 2, 5 and 7 days, written like "29 Nov 2026"). History
(collapsed) shows the process starting.

Result: ____________________

### 4. Complete, approve and send back

Do:

1. Open **My tasks** on Home as the HR owner. Press **Mark done** on the HR step (add a short
   note if you like).
2. Sign in as the manager (second browser profile) and mark the manager step done.
3. For the person step: if Test Person Alpha has a console account, sign in as them and press
   **Mark done**; if not, as HR press **Mark done for them**. The step now waits for approval.
4. As HR, press **Send back** on that step. A reason is required: type `Please attach the
   signed form`. Press the button.
5. Sign in as the person (or, without a console account, read the step on the process page as
   HR). Mark the step done again.
6. As HR, press **Approve**.

You should see: after step 1 the HR row disappears from My tasks and the process shows "1 of 3
steps"; after step 4 the step shows as sent back, and the person sees the line "Sent back:
Please attach the signed form" on their row; after step 6 the process shows "3 of 3 steps" and
its status becomes Completed. If you used **Mark done for them**, the step reads "Done by HR".

Result: ____________________

### 5. Reminders on Home, grouped and readable

Do: create a second synthetic process, or reuse a step that is due soon. After the next worker
round (up to 15 minutes), open Home as the person or the HR owner who owns an open step.

You should see: reminder lines with a readable subject (the step and the person) and a due date
in words, not a code. When there are more than three, they are grouped under headings such as
Overdue, Due today, Due soon and New tasks, each with a count. Click one: you land on My tasks.
Press **Mark all read**: a progress line shows ("Marking 3 of 12..."), the reminders become read,
and the unread count falls. A reminder for a step that is already done shows "Done" with no link.

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

Do: first create a minimal `Synthetic offboarding` template (kind **Offboarding**, one HR step,
days **0** counted from **Last day**) the same way as row 1. Then open Test Person Alpha's
profile and press **Mark as leaving**, choose a last day (today), a reason, and who their reports
move to. Open **Processes**. Then go back to the profile and press **Undo leaving**.

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
template, with **Keep my changes** and **Show their version**. **Show their version** loads the
name from window A. **Keep my changes** keeps `B` in the form with the sentence that saving now
replaces their version; saving then works. **Copy as new** opens a new template with "Copy of ..."
and your steps.

Result: ____________________

### 11. A read-only member sees tasks without buttons

Do: sign in as a staff member whose access on the Team page is **Read-only** and who has an open
step (or ask the owner to reassign one to them). Open Home or **My HR**.

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

### 14. Jev

Not part of this list: Jev is checked separately.
