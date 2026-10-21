"""Typed staff HR agents; deliberately independent of customer/contact AgentContext.

Reads and approval-free draft preparation can execute automatically. Human
decisions are prepared as bound confirmations and cannot be executed by a model.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, TypeAdapter

from csa.contracts.plan import RiskClass
from csa.hr.automation_models import InterviewSchedule, OperationReceipt
from csa.hr.cases import HrWorkflowService
from csa.hr.models import (
    DirectoryProjection,
    HrModel,
    TrainingAction,
    TrainingCommandInput,
    TrainingDraftInput,
    TrainingProjection,
)
from csa.hr.operations import HrOperations
from csa.hr.policy import HrPolicyError
from csa.hr.records import ConfirmationProjection, HrSession, TrainingReceipt
from csa.hr.service import HrService
from csa.hr.workflows import Domain, WorkflowInput, WorkflowRecord


class DirectoryRead(HrModel):
    action: Literal["directory.read"]
    employee_id: UUID


class TrainingRead(HrModel):
    action: Literal["training.read"]
    request_id: UUID


class TrainingPrepare(HrModel):
    action: Literal["training.prepare"]
    content: TrainingDraftInput


class TrainingTransition(HrModel):
    action: Literal["training.transition"]
    request_id: UUID
    command: TrainingCommandInput


class WorkflowPrepare(HrModel):
    action: Literal["workflow.prepare"]
    content: WorkflowInput


class WorkflowRead(HrModel):
    action: Literal["workflow.read"]
    domain: Domain
    case_id: UUID


class InterviewArrange(HrModel):
    action: Literal["interview.schedule"]
    content: InterviewSchedule


HrAgentInput = Annotated[
    DirectoryRead
    | TrainingRead
    | TrainingPrepare
    | TrainingTransition
    | WorkflowPrepare
    | WorkflowRead
    | InterviewArrange,
    Field(discriminator="action"),
]
HR_AGENT_INPUT = TypeAdapter(HrAgentInput)


class HrAgentDescription(HrModel):
    name: Literal[
        "hr_directory",
        "hr_training",
        "hr_recruitment",
        "hr_attendance",
        "hr_payroll",
        "hr_assistant",
    ]
    description: str
    enabled: bool
    actions: tuple[str, ...]
    timeout_ms: int = 10000
    max_retries: Literal[0] = 0
    side_effects: Literal["reversible"] = "reversible"
    input_schema: dict[str, JsonValue]


def describe_agent(name: str, enabled: bool) -> HrAgentDescription:
    descriptions = {
        "hr_directory": ("Read explicitly granted employee fields.", ("directory.read",)),
        "hr_training": (
            "Prepare training requests. Approval/rejection creates a human confirmation.",
            ("training.read", "training.prepare", "training.transition"),
        ),
        "hr_recruitment": (
            "Prepare job-based interview guides without candidate ranking or hiring.",
            ("workflow.read", "workflow.prepare", "interview.schedule"),
        ),
        "hr_attendance": (
            "Prepare attendance, corrections and leave for a human decision.",
            ("workflow.read", "workflow.prepare"),
        ),
        "hr_payroll": ("Read authorized payroll review workflow status only.", ("workflow.read",)),
    }
    descriptions["hr_assistant"] = (
        "Plan permitted HR requests from submitted messages using the configured model provider.",
        ("assistant.chat",),
    )
    input_schema = HR_AGENT_INPUT.json_schema()
    if name == "hr_assistant":
        from csa.hr.assistant import HrChatInput

        input_schema = HrChatInput.model_json_schema()
    description, actions = descriptions[name]
    return HrAgentDescription.model_validate(
        {
            "name": name,
            "description": description,
            "enabled": enabled,
            "actions": actions,
            "input_schema": input_schema,
        }
    )


class HrAgentDispatcher:
    """Deterministic spoke dispatcher over the same authorized domain service as UI.

    The authenticated HTTP adapter supplies session and request key. There is no
    model-visible actor, tenant, role, contact or grant argument.
    """

    def __init__(self, service: HrService) -> None:
        self.service = service

    @staticmethod
    def risk(inp: HrAgentInput) -> RiskClass:
        if isinstance(inp, InterviewArrange):
            return "commit"
        if isinstance(inp, (DirectoryRead, TrainingRead, WorkflowRead)):
            return "read"
        if isinstance(inp, TrainingTransition) and inp.command.action in {
            TrainingAction.SUBMIT,
            TrainingAction.WITHDRAW,
        }:
            return "commit"
        return "draft"

    @staticmethod
    def name(
        inp: HrAgentInput,
    ) -> Literal[
        "hr_directory",
        "hr_training",
        "hr_recruitment",
        "hr_attendance",
        "hr_payroll",
        "hr_assistant",
    ]:
        if isinstance(inp, InterviewArrange):
            return "hr_recruitment"
        if isinstance(inp, (WorkflowRead, WorkflowPrepare)):
            domain = inp.content.domain if isinstance(inp, WorkflowPrepare) else inp.domain
            if domain == "recruitment":
                return "hr_recruitment"
            if domain == "payroll":
                return "hr_payroll"
            return "hr_attendance"
        return "hr_directory" if isinstance(inp, DirectoryRead) else "hr_training"

    async def execute(
        self,
        session: HrSession,
        inp: HrAgentInput,
        request_key: UUID,
        *,
        observe_only: bool = False,
    ) -> (
        DirectoryProjection
        | TrainingProjection
        | TrainingReceipt
        | ConfirmationProjection
        | WorkflowRecord
        | OperationReceipt
    ):
        if observe_only and self.risk(inp) != "read":
            raise HrPolicyError("observe_loop_read_only")
        async with asyncio.timeout(10):
            return await self._execute(session, inp, request_key)

    async def _execute(
        self, session: HrSession, inp: HrAgentInput, request_key: UUID
    ) -> (
        DirectoryProjection
        | TrainingProjection
        | TrainingReceipt
        | ConfirmationProjection
        | WorkflowRecord
        | OperationReceipt
    ):
        bound = session.model_copy(update={"capability": self.name(inp)})
        if isinstance(inp, InterviewArrange):
            return await HrOperations(self.service).apply(bound, inp.content, request_key)
        if isinstance(inp, WorkflowRead):
            return await HrWorkflowService(self.service).get(bound, inp.case_id)
        if isinstance(inp, WorkflowPrepare):
            if inp.content.domain == "payroll":
                raise HrPolicyError("manual_payroll_preparation_required")
            return await HrWorkflowService(self.service).prepare(bound, inp.content, request_key)
        if isinstance(inp, DirectoryRead):
            return await self.service.directory(bound, inp.employee_id)
        if isinstance(inp, TrainingRead):
            return await self.service.training(bound, inp.request_id)
        if isinstance(inp, TrainingPrepare):
            return await self.service.save_draft(bound, inp.content, request_key)
        if inp.command.action in {TrainingAction.SUBMIT, TrainingAction.WITHDRAW}:
            return await self.service.transition(bound, inp.request_id, inp.command, request_key)
        return await self.service.prepare_confirmation(
            bound, inp.request_id, inp.command, request_key
        )
