# FourStacks dashboard implementation brief

Give this brief, the root `UI_GUIDE.md`, `AGENT_AVATARS.md`, token CSS and assets to the developer working in the actual dashboard repository.

## Objective

Apply the established FourStacks light blue flat-vector identity to the product dashboard while preserving its existing workflows, backend contracts and authorization. Deliver a professional operational interface with readable tables, clear approval controls and the original white-eyed agent family.

## Read before editing

1. `../UI_GUIDE.md` for the theme and product patterns.
2. `AGENT_AVATARS.md` for the current stable service-to-avatar mapping.
3. The product repository's own instructions, component system, API types and role rules.
4. The supplied `../Agents.md` and `../Overview.md` as system context; confirm current implementation details in the product repository.

The reference HTML contains illustrative data. Its navigation grouping, sample counts and visible states are design examples, not additional product requirements or evidence of a working endpoint.

## Design requirements

- Explicit light color scheme, `#F7F8FA` canvas, white surfaces, `#1C2B40` text and `#3264FF` primary actions.
- Space Grotesk headings, Inter UI/body, Manrope for the existing lowercase brand wordmark.
- Consistent 4 px spacing scale, restrained borders, 14 px panel radii, 6 px control radii and visible blue keyboard focus.
- Status labels use text and semantic colors; keep agent identity separate from job state, connection state, entitlement and implementation readiness.
- Use the six exported static avatars for dense views. Reserve animated working artwork for selected-agent details or a bounded overview illustration.
- Preserve every character's unique shape, white eye count, flexible tendrils and blue palette.
- Use quick product transitions and honor reduced motion, offscreen visibility and the user's pause preference.
- Adapt the shell and shared components before extending the theme to every screen.

## Functional boundaries

- Preserve the actual routes, API requests, authentication, tenant isolation and server-side permissions.
- Theme existing components rather than replacing working behavior with a visual mock.
- Preserve keyboard/focus behavior when replacing dialogs, tabs, menus or drawers.
- Render actions only when supported by the current item state, role and backend response.
- Keep approve/edit/reject and human takeover decisions explicit. Never automate them through animation.
- Keep `Planned` agents separate from enabled, connected services. Do not invent marketplace integrations or invoice submission features.
- Keep plan entitlement and client enable/disable configuration separate.
- Obtain numbers and progress from real responses. Use an indeterminate state when progress is unknown.
- Do not treat the marketing workspace's simulated concurrency or timers as product behavior.
- Keep provider credentials and internal service details out of ordinary product copy and browser responses.

## Suggested sequence

1. Inspect the actual product and list the shared components to theme.
2. Apply token CSS, fonts, shell, controls, badges, tables and overlays.
3. Add static avatars using stable service IDs.
4. Style the existing conversations, knowledge and approval flows.
5. Style implemented agent configuration and account connections.
6. Add optional selected-agent motion only after the working screens are stable.
7. Verify responsive, accessibility, permission and real-data states.

## Acceptance checks

- The product remains light under an OS dark preference.
- The layout works at 320, 390, 768, 1024 and 1440 px without accidental page overflow.
- Dense content remains readable and long customer names do not break rows.
- All six existing agents have the correct distinct avatar; planned agents use an icon until an approved avatar exists.
- Controls work by keyboard and touch; dialogs manage focus and close properly.
- Reduced motion and the pause setting stop decorative motion without changing server work.
- Loading, empty, unavailable, partial, failed and unauthorized states remain distinct.
- Approval and handoff actions obey actual backend permissions and state.
- No fake metrics, simulated progress, placeholder client results or invented availability is presented as live data.
- Existing product checks and the relevant UI interactions still pass.

Report the changed files, verified screens and any integration gaps. This brief calls for implementing the theme in the actual product; the reference dashboard is only a visual aid.
