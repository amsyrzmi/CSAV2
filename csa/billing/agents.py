"""The AI agents a client's usage is shown under, and which agent each metered job belongs to.

The titles mirror ``AGENT_TITLES`` in ``CSA_WebConsole/csa-console/web/lib/agent-avatars.ts``
for the four agents that exist there; ``training`` has no console title yet, so it gets its own.
The job-to-agent map is ``JOB_AGENT`` (``csa/metering/context.py``): it lives in code, not in the
ledger, so renaming an agent never rewrites history.
"""

from __future__ import annotations

from csa.metering.context import JOB_AGENT, Job

__all__ = ["AGENT_TITLES", "agent_for"]

AGENT_TITLES: dict[str, str] = {
    "support": "Customer support assistant",
    "assistant": "Workspace assistant",
    "hr": "HR assistant",
    "email": "Email assistant",
    "training": "Training summary",
}


def agent_for(job: Job) -> tuple[str, str]:
    """``(agent_id, title)`` for a ledger job. An unmapped job or agent raises ``KeyError``."""
    agent_id = JOB_AGENT[job]
    return agent_id, AGENT_TITLES[agent_id]
