"""Staff HR spoke entry point.

The customer Agent protocol requires a contact, which is not employee identity.
Authenticated staff adapters use these typed agents and HrSession instead. They
are registered in the tenant's HR catalog, never in customer capabilities.
"""

from csa.hr.agents import (
    HR_AGENT_INPUT,
    DirectoryRead,
    HrAgentDispatcher,
    HrAgentInput,
    InterviewArrange,
    TrainingPrepare,
    TrainingRead,
    TrainingTransition,
    WorkflowPrepare,
    WorkflowRead,
)

__all__ = [
    "HR_AGENT_INPUT",
    "DirectoryRead",
    "InterviewArrange",
    "HrAgentDispatcher",
    "HrAgentInput",
    "TrainingPrepare",
    "TrainingRead",
    "TrainingTransition",
    "WorkflowPrepare",
    "WorkflowRead",
]
