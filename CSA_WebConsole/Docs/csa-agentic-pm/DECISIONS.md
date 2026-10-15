# Operator Architectural Decisions (Phase 00)

**Date**: 12 October 2026  
**Context**: Phase 00 Discovery  
**Requirement**: The following decisions require explicit operator input before their corresponding roadmap phases can be implemented and enabled in staging or production.

---

## 1. Application Database (`console_db`) Hosting Location
- **Options**:
  - **A. Keep in local Docker container (`127.0.0.1:5440`)**: Continues running locally during development. Fast, independent of remote network, but requires migration before team or production deployment.
  - **B. Move to homeserver (`nexusserver`)**: Runs as a Docker container or dedicated database on the homeserver. Requires port/tunnel reconfiguration and consistent backup procedures.
- **Operator Choice Needed**: Keep local for Phase 01 development or move to homeserver now?
- **Recommendation**: Keep local on port 5440 for Phase 01–03 development; execute database move during Phase 10 hardening or ahead of multi-user staging.

---

## 2. Execution Runner Host Environment (Phase 06, 07)
- **Options**:
  - **A. Dedicated container on homeserver (`nexusserver`)**: Uses homeserver compute resources. Requires isolated Docker daemon, non-root user, and restricted network egress.
  - **B. Dedicated runner on VPS**: Fully network-isolated from local network and homeserver internal secrets.
  - **C. Isolated microVM (e.g. Firecracker / fly.io / cloud container)**: Maximum security boundary against untrusted prompt execution.
- **Operator Choice Needed**: Where will the sandbox runner execute code?
- **Recommendation**: Dedicated Docker container on homeserver with explicit network restrictions (`--network=none` or strictly egress allowlisted) and read-only host mounts.

---

## 3. Hermes Agent Distribution & Version (Phase 07)
- **Context**:
  - The architecture assumes Nous Research Hermes Agent.
  - Is Hermes currently installed as a Python package, a CLI binary, or running in an existing container?
  - What is the exact commit, release version, and data directory location (e.g. `~/.hermes` or custom path)?
  - Does its CLI expose structured JSON output, non-interactive execution mode, and process cancellation?
- **Operator Action Needed**:
  - Run `checks/inspect-server.sh` on the host where Hermes is running and record output.
  - Confirm whether the personal profile must be strictly isolated from project-management worker execution.
- **Recommendation**: The PM worker must use fresh, isolated runtime homes (`HERMES_HOME=/var/lib/pm-runner/project-<id>`) with generic identity seeds, never mounting or cloning the operator's personal profile.

---

## 4. Project Terminal Scope & Semantics (Phase 06)
- **Options**:
  - **A. Hosted Project Runner (Server Container)**: The browser xterm.js connects via WebSocket to a PTY process running inside the project's sandbox container on the server.
  - **B. Developer Laptop Companion**: The browser attaches to the developer's local laptop shell via an authenticated local bridge/daemon.
- **Operator Choice Needed**: Does "Terminal" mean server-side sandbox or developer laptop?
- **Recommendation**: Default to **Option A (Hosted Project Runner)**. This keeps code changes isolated from the developer's laptop, avoids security hazards of web-to-laptop shell access, and matches standard cloud workspace patterns.

---

## 5. Model Provider, Model Selection & Per-Run Budget (Phase 04, 08)
- **Options**:
  - **A. Anthropic Claude (Claude 3.5 Sonnet)**: Strong planning, code analysis, and structured artifact generation.
  - **B. OpenAI (GPT-4o / o1-preview)**: High availability and structured output support.
  - **C. Local LLM via Ollama / vLLM on Homeserver**: Zero API cost, private, but limited by homeserver GPU/VRAM capacity.
- **Operator Choice Needed**:
  - Primary provider and model for the LangGraph planner.
  - Initial per-run token ceiling and financial budget cap (e.g. $0.50 per plan generation, max 2 repair cycles).
- **Recommendation**: Anthropic Claude 3.5 Sonnet for the LangGraph planner with a strict $0.50 / 50k token cap per proposal generation.

---

## 6. Git Repository Authentication Method (Phase 06)
- **Options**:
  - **A. GitHub Deploy Keys (Read-Only)**: Per-project SSH deploy keys configured in repository settings. No access to other repositories.
  - **B. GitHub App / Fine-Grained Personal Access Token (PAT)**: Scoped installation token generated dynamically.
  - **C. Local Bare Clones / Local Directory Mounts**: Cloned directly from an internal path without remote Git credentials.
- **Operator Choice Needed**: What credential strategy will provide runner workspaces access to project source code?
- **Recommendation**: Read-only GitHub Deploy Keys stored as encrypted references in `console_db`, resolved server-side by the workspace service.
