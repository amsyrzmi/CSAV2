# Phase 05 — Diagram studio and application/AWS architecture

Prerequisites: phase 03 for artifacts and phase 04 for AI generation. Manual source editing can ship before model generation. Start with a tested renderer catalog; do not claim all software-engineering notation works universally.

## IDE AI work

1. Add artifact/revision storage for Mermaid, PlantUML and a typed architecture JSON graph. Persist source, renderer/version, validation status, assumptions, evidence, generation run and exports.
2. Create a diagram-type registry describing available engines, supported installed-version syntax, example fixtures and enabled state. Begin with sequence, class, ERD, state and flowchart. Add PlantUML use case/object/component/deployment/timing types after its renderer is deployed. Provide an honest unsupported-type state.
3. Build source editor/preview/version picker and SVG/PNG exports. Mermaid configuration is controlled by the app (strict security and enforced secure settings), not untrusted frontmatter. Strip/reject inline init/config overrides and unsafe HTML/links. Sanitize exported SVG, including external references.
4. Render PlantUML and headless exports in a restricted local renderer with time/memory/output caps and no network. Disable arbitrary includes and file reads; resolve only approved vendored library assets. Render failure returns diagnostics and a bounded repair option, not a broken success artifact.
5. Add typed architecture nodes/edges/boundaries, layout metadata, editable canvas and inspector. Separate semantics from display layout. Prevent cross-project artifact references. Do not promise arbitrary text-diagram-to-canvas round-tripping.
6. Architecture generation outputs requirements/assumptions, component graph, deployment boundaries, data flows, trust boundaries, failure modes and ADR. Keep observed architecture (based on evidence/commit) separate from proposed architecture.
7. For AWS add local pinned icons/templates and account/region/VPC/AZ/subnet/security/IAM annotations. Ask for workload, availability, region, data requirements and budget when unknown. Cost figures require a cited dated source; otherwise show qualitative cost drivers or unknown values.
8. Label Terraform/CDK drafts as proposed code artifacts; no cloud credentials or automatic deployment in this phase. Read-only AWS inventory import is later and requires a separately scoped credential policy.

## Acceptance

Generate, edit and export a sequence diagram and ERD; preserve revisions and source. Validate a class diagram plus a PlantUML deployment fixture once enabled. Test invalid syntax, malicious configuration, external includes, oversized graphs, timeout, unsafe SVG and unauthorized exports. Generate an app and AWS design from a brief and inspect assumptions, evidence and cloud boundaries. Confirm no source is sent to a public rendering service and no infrastructure is provisioned.

## Operator work

Deploy the private renderer image with pinned assets and resource limits. Verify engine versions support the advertised types. Enable PlantUML only after renderer smoke checks; Mermaid-only mode remains useful.
