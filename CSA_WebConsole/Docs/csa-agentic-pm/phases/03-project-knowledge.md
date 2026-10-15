# Phase 03 — Markdown notes, links and project knowledge

Prerequisite: phase 01; task-linking works after phase 02. Deliver an Obsidian-inspired project notebook, not a full Obsidian plugin runtime or immediate collaborative editor.

## IDE AI work

1. Add notes, immutable revisions and project-local links from CONTRACTS.md. Default to admin_only. Store Markdown in the app DB initially; use private project storage for attachments.
2. Provide tree/list, read, create, versioned update, search and backlink endpoints. Folder paths are metadata and must not resolve as arbitrary filesystem paths.
3. Define unambiguous wiki links: persist stable target note IDs; `[[Title]]` resolves within the project and rejects ambiguous matches. Renaming updates display labels without breaking references. Unresolved links appear as unresolved, not as access to another project.
4. Add Markdown editor/preview, autosave with debounce and conflict handling, folder tree, tags, backlinks and a small neighborhood graph. Reuse the existing sanitized Markdown pipeline, explicitly disabling raw HTML/executable content.
5. Add starter note templates: project brief, requirement, bug report, meeting note and Architecture Decision Record (context, options, decision, consequences and evidence). These are optional seed templates, not facts generated automatically.
6. Add link references from tasks to notes and immutable artifact revisions. Define evidence references with note ID and revision ID so later AI citations remain reproducible.
7. Implement project-scoped PostgreSQL full-text search before adding embeddings. Set content/context size limits. Exclude secrets and unauthorized artifacts from AI retrieval.
8. For Markdown export, package stable links and an attachment manifest through authorized artifact routes. Import validates names, size, content and scope. Do not promise live synchronization with an existing desktop Obsidian vault.

## Acceptance

Create two linked notes, rename one and verify backlinks still resolve; move folders and confirm no path traversal is possible; compare revision history and recover an earlier body through a new revision; concurrently edit and receive a conflict; search project A and verify no project B snippets leak; insert malicious HTML/URLs and verify safe preview. Verify task-note links use the authorized project's records.

## Operator work

Choose persistent private artifact storage and include it in backups. DB-only Markdown notes need no new storage service. Collaborative CRDT editing and actual vault synchronization are deferred until usage requires them.
