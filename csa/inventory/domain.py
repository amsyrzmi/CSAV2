"""Pure, immutable inventory rules; not wired to persistence or provider APIs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum


class InventoryRuleViolation(ValueError):
    """A requested transition violates the inventory domain rules."""


class StaleInventoryVersion(InventoryRuleViolation):
    """A command was based on an obsolete inventory version."""


class AuthorityMode(StrEnum):
    CSA_MANAGED = "csa_managed"
    EXTERNAL_AUTHORITATIVE = "external_authoritative"
    UNMANAGED = "unmanaged"


class ReservationState(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    FULFILLED = "fulfilled"


class ReturnState(StrEnum):
    REQUESTED = "requested"
    RECEIVED = "received"
    INSPECTED = "inspected"


class StockChangeKind(StrEnum):
    PHYSICAL_COUNT = "physical_count"
    MANUAL_ADJUSTMENT = "manual_adjustment"


class StockChangeState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RECORDED = "recorded"


class ActorRole(StrEnum):
    TENANT_ADMIN = "tenant_admin"
    OTHER = "other"


class AllocationOperationState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    OBSERVED = "observed"


class AllocationOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class ObservationFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"


class MappingState(StrEnum):
    VERIFIED = "verified"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"


class ReconciliationStatus(StrEnum):
    MATCH = "match"
    DRIFT = "drift"
    MISSING_OBSERVATION = "missing_observation"
    STALE_OBSERVATION = "stale_observation"
    MISSING_MAPPING = "missing_mapping"
    AMBIGUOUS_MAPPING = "ambiguous_mapping"
    INCOMPLETE_ORDER_FEED = "incomplete_order_feed"
    UNKNOWN_PROVIDER_STATUS = "unknown_provider_status"
    INCOMPARABLE_QUANTITY = "incomparable_quantity"


@dataclass(frozen=True, slots=True)
class InventoryScope:
    tenant_id: str
    variant_id: str
    warehouse_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("tenant_id", self.tenant_id),
            ("variant_id", self.variant_id),
            ("warehouse_id", self.warehouse_id),
        ):
            _require_text(value, name)


@dataclass(frozen=True, slots=True)
class Reservation:
    order_line_id: str
    quantity: int
    state: ReservationState = ReservationState.ACTIVE


@dataclass(frozen=True, slots=True)
class ReturnCase:
    return_id: str
    order_line_id: str
    quantity: int
    state: ReturnState = ReturnState.REQUESTED
    receiver_id: str | None = None
    receipt_reason: str | None = None
    inspector_id: str | None = None
    inspection_reason: str | None = None
    sellable_quantity: int | None = None
    resulting_version: int | None = None


@dataclass(frozen=True, slots=True)
class InventoryObservation:
    """Provider-neutral evidence prepared for a report-only comparison."""

    store_id: str
    freshness: ObservationFreshness
    mapping_state: MappingState
    observed_sellable_quantity: int | None
    order_feed_complete: bool
    provider_status_known: bool
    quantity_comparable: bool

    def __post_init__(self) -> None:
        _require_text(self.store_id, "store_id")
        if not isinstance(self.freshness, ObservationFreshness):
            raise InventoryRuleViolation("observation freshness is invalid")
        if not isinstance(self.mapping_state, MappingState):
            raise InventoryRuleViolation("observation mapping state is invalid")
        if self.observed_sellable_quantity is not None:
            _require_nonnegative(self.observed_sellable_quantity, "observed_sellable_quantity")
        if not isinstance(self.order_feed_complete, bool):
            raise InventoryRuleViolation("order_feed_complete must be a boolean")
        if not isinstance(self.provider_status_known, bool):
            raise InventoryRuleViolation("provider_status_known must be a boolean")
        if not isinstance(self.quantity_comparable, bool):
            raise InventoryRuleViolation("quantity_comparable must be a boolean")
        if self.freshness is ObservationFreshness.FRESH:
            if self.observed_sellable_quantity is None:
                raise InventoryRuleViolation("fresh observation requires a quantity")
        elif self.observed_sellable_quantity is not None:
            raise InventoryRuleViolation("stale or missing observation cannot assert quantity")


@dataclass(frozen=True, slots=True)
class ReconciliationRow:
    store_id: str
    expected_allocation: int
    observed_sellable_quantity: int | None
    status: ReconciliationStatus
    delta: int | None
    writes_blocked: bool


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    inventory_version: int
    rows: tuple[ReconciliationRow, ...]


@dataclass(frozen=True, slots=True)
class StockChange:
    command_id: str
    kind: StockChangeKind
    submitter_id: str
    reason: str
    expected_version: int
    authority_revision: int
    before_on_hand: int
    change_value: int
    state: StockChangeState
    approver_id: str | None = None
    review_reason: str | None = None
    resulting_version: int | None = None

    @property
    def proposed_on_hand(self) -> int:
        if self.kind is StockChangeKind.PHYSICAL_COUNT:
            return self.change_value
        return self.before_on_hand + self.change_value


@dataclass(frozen=True, slots=True)
class StoreAllocation:
    store_id: str
    quantity: int


@dataclass(frozen=True, slots=True)
class AllocationOperation:
    operation_id: str
    store_id: str
    expected_version: int
    authority_revision: int
    desired_quantity: int
    state: AllocationOperationState = AllocationOperationState.PENDING
    last_observed_quantity: int | None = None


@dataclass(frozen=True, slots=True)
class InventoryLedger:
    scope: InventoryScope
    authority_mode: AuthorityMode
    authority_revision: int
    version: int
    on_hand: int
    safety_stock: int = 0
    reservations: tuple[Reservation, ...] = ()
    returns: tuple[ReturnCase, ...] = ()
    stock_changes: tuple[StockChange, ...] = ()
    allocations: tuple[StoreAllocation, ...] = ()
    allocation_operations: tuple[AllocationOperation, ...] = ()

    def __post_init__(self) -> None:
        _require_nonnegative(self.authority_revision, "authority_revision")
        if self.authority_revision == 0:
            raise InventoryRuleViolation("authority_revision must be positive")
        _require_nonnegative(self.version, "version")
        _require_nonnegative(self.on_hand, "on_hand")
        _require_nonnegative(self.safety_stock, "safety_stock")
        _require_unique(
            (reservation.order_line_id for reservation in self.reservations),
            "order_line_id",
        )
        _require_unique((return_case.return_id for return_case in self.returns), "return_id")
        _require_unique((change.command_id for change in self.stock_changes), "command_id")
        _require_unique((allocation.store_id for allocation in self.allocations), "store_id")
        _require_unique(
            (operation.operation_id for operation in self.allocation_operations),
            "operation_id",
        )
        for reservation in self.reservations:
            _require_text(reservation.order_line_id, "order_line_id")
            _require_positive(reservation.quantity, "reservation quantity")
            if not isinstance(reservation.state, ReservationState):
                raise InventoryRuleViolation("reservation state is invalid")
        for return_case in self.returns:
            _require_text(return_case.return_id, "return_id")
            _require_text(return_case.order_line_id, "order_line_id")
            _require_positive(return_case.quantity, "return quantity")
            if not isinstance(return_case.state, ReturnState):
                raise InventoryRuleViolation("return state is invalid")
        for allocation in self.allocations:
            _require_text(allocation.store_id, "store_id")
            _require_nonnegative(allocation.quantity, "allocation quantity")
        for change in self.stock_changes:
            _require_text(change.command_id, "command_id")
            if not isinstance(change.kind, StockChangeKind):
                raise InventoryRuleViolation("stock change kind is invalid")
            if not isinstance(change.state, StockChangeState):
                raise InventoryRuleViolation("stock change state is invalid")

    @property
    def active_reserved(self) -> int:
        return sum(
            reservation.quantity
            for reservation in self.reservations
            if reservation.state is ReservationState.ACTIVE
        )

    @property
    def reservation_shortfall(self) -> int:
        return max(0, self.active_reserved + self.safety_stock - self.on_hand)

    @property
    def allocation_shortfall(self) -> int:
        return max(0, self.allocated_quantity - self.available_to_allocate)

    @property
    def allocated_quantity(self) -> int:
        return sum(allocation.quantity for allocation in self.allocations)

    @property
    def has_pending_stock_change(self) -> bool:
        return any(change.state is StockChangeState.PENDING for change in self.stock_changes)

    @property
    def available_to_allocate(self) -> int:
        if self.has_pending_stock_change or self.reservation_shortfall:
            return 0
        return max(0, self.on_hand - self.active_reserved - self.safety_stock)

    @property
    def allocation_writes_frozen(self) -> bool:
        return (
            self.has_pending_stock_change
            or self.reservation_shortfall > 0
            or self.allocation_shortfall > 0
            or any(
                operation.state is AllocationOperationState.UNKNOWN
                for operation in self.allocation_operations
            )
        )

    def reserve(self, scope: InventoryScope, order_line_id: str, quantity: int) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        _require_text(order_line_id, "order_line_id")
        _require_positive(quantity, "quantity")
        existing = self._reservation(order_line_id)
        if existing is not None:
            if existing.quantity != quantity:
                raise InventoryRuleViolation("order line already has a different reservation")
            return self
        if self.has_pending_stock_change:
            raise InventoryRuleViolation("stock changes are pending approval")
        if self.reservation_shortfall:
            raise InventoryRuleViolation("inventory has a reservation shortfall")
        if quantity > self.available_to_allocate:
            raise InventoryRuleViolation("insufficient available inventory")
        return replace(
            self,
            version=self.version + 1,
            reservations=tuple(
                sorted(
                    (*self.reservations, Reservation(order_line_id, quantity)),
                    key=lambda reservation: reservation.order_line_id,
                )
            ),
        )

    def cancel_reservation(self, scope: InventoryScope, order_line_id: str) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        reservation = self._require_reservation(order_line_id)
        if reservation.state is ReservationState.CANCELLED:
            return self
        if reservation.state is ReservationState.FULFILLED:
            raise InventoryRuleViolation("fulfilled reservation cannot be cancelled")
        return self._replace_reservation(replace(reservation, state=ReservationState.CANCELLED))

    def fulfil_reservation(self, scope: InventoryScope, order_line_id: str) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        reservation = self._require_reservation(order_line_id)
        if reservation.state is ReservationState.FULFILLED:
            return self
        if reservation.state is ReservationState.CANCELLED:
            raise InventoryRuleViolation("cancelled reservation cannot be fulfilled")
        if self.on_hand < reservation.quantity:
            raise InventoryRuleViolation("on-hand stock is below the reserved quantity")
        updated = self._replace_reservation(
            replace(reservation, state=ReservationState.FULFILLED),
            on_hand=self.on_hand - reservation.quantity,
        )
        return updated

    def request_return(
        self,
        scope: InventoryScope,
        *,
        return_id: str,
        order_line_id: str,
        quantity: int,
    ) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        _require_text(return_id, "return_id")
        _require_text(order_line_id, "order_line_id")
        _require_positive(quantity, "return quantity")

        existing = self._return_case(return_id)
        if existing is not None:
            if existing.order_line_id != order_line_id or existing.quantity != quantity:
                raise InventoryRuleViolation("return id was reused with different content")
            return self

        reservation = self._require_reservation(order_line_id)
        if reservation.state is not ReservationState.FULFILLED:
            raise InventoryRuleViolation("only fulfilled order lines can be returned")
        already_requested = sum(
            item.quantity for item in self.returns if item.order_line_id == order_line_id
        )
        if already_requested + quantity > reservation.quantity:
            raise InventoryRuleViolation("returns exceed fulfilled quantity")
        return replace(
            self,
            version=self.version + 1,
            returns=tuple(
                sorted(
                    (*self.returns, ReturnCase(return_id, order_line_id, quantity)),
                    key=lambda item: item.return_id,
                )
            ),
        )

    def receive_return(
        self, scope: InventoryScope, return_id: str, receiver_id: str, reason: str
    ) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        _require_text(receiver_id, "receiver_id")
        _require_text(reason, "reason")
        return_case = self._require_return_case(return_id)
        if return_case.state is ReturnState.RECEIVED:
            if (
                return_case.receiver_id == receiver_id
                and return_case.receipt_reason == reason.strip()
            ):
                return self
            raise InventoryRuleViolation("return receipt was already recorded differently")
        if return_case.state is ReturnState.INSPECTED:
            raise InventoryRuleViolation("inspected return cannot be received again")
        received = replace(
            return_case,
            state=ReturnState.RECEIVED,
            receiver_id=receiver_id,
            receipt_reason=reason.strip(),
        )
        return replace(
            self,
            version=self.version + 1,
            returns=self._replace_return(received),
        )

    def inspect_return(
        self,
        scope: InventoryScope,
        return_id: str,
        inspector_id: str,
        *,
        sellable_quantity: int,
        reason: str,
    ) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        _require_text(inspector_id, "inspector_id")
        _require_text(reason, "reason")
        _require_nonnegative(sellable_quantity, "sellable_quantity")
        return_case = self._require_return_case(return_id)
        if return_case.state is ReturnState.INSPECTED:
            if (
                return_case.inspector_id == inspector_id
                and return_case.inspection_reason == reason.strip()
                and return_case.sellable_quantity == sellable_quantity
            ):
                return self
            raise InventoryRuleViolation("return inspection was already recorded differently")
        if return_case.state is not ReturnState.RECEIVED:
            raise InventoryRuleViolation("return must be received before inspection")
        if sellable_quantity > return_case.quantity:
            raise InventoryRuleViolation("sellable quantity exceeds received return")
        next_version = self.version + 1
        inspected = replace(
            return_case,
            state=ReturnState.INSPECTED,
            inspector_id=inspector_id,
            inspection_reason=reason.strip(),
            sellable_quantity=sellable_quantity,
            resulting_version=next_version,
        )
        return replace(
            self,
            version=next_version,
            on_hand=self.on_hand + sellable_quantity,
            returns=self._replace_return(inspected),
        )

    def reconcile(self, observations: tuple[InventoryObservation, ...]) -> ReconciliationReport:
        _require_unique((observation.store_id for observation in observations), "store_id")
        rows: list[ReconciliationRow] = []
        for observation in sorted(observations, key=lambda item: item.store_id):
            expected = self._allocation_quantity(observation.store_id)
            status = self._reconciliation_status(observation, expected)
            observed = observation.observed_sellable_quantity
            delta = (
                observed - expected
                if status in {ReconciliationStatus.MATCH, ReconciliationStatus.DRIFT}
                and observed is not None
                else None
            )
            rows.append(
                ReconciliationRow(
                    store_id=observation.store_id,
                    expected_allocation=expected,
                    observed_sellable_quantity=observed,
                    status=status,
                    delta=delta,
                    writes_blocked=status is not ReconciliationStatus.MATCH,
                )
            )
        return ReconciliationReport(inventory_version=self.version, rows=tuple(rows))

    @staticmethod
    def _reconciliation_status(
        observation: InventoryObservation, expected_allocation: int
    ) -> ReconciliationStatus:
        if observation.mapping_state is MappingState.MISSING:
            return ReconciliationStatus.MISSING_MAPPING
        if observation.mapping_state is MappingState.AMBIGUOUS:
            return ReconciliationStatus.AMBIGUOUS_MAPPING
        if observation.freshness is ObservationFreshness.MISSING:
            return ReconciliationStatus.MISSING_OBSERVATION
        if observation.freshness is ObservationFreshness.STALE:
            return ReconciliationStatus.STALE_OBSERVATION
        if not observation.order_feed_complete:
            return ReconciliationStatus.INCOMPLETE_ORDER_FEED
        if not observation.provider_status_known:
            return ReconciliationStatus.UNKNOWN_PROVIDER_STATUS
        if not observation.quantity_comparable:
            return ReconciliationStatus.INCOMPARABLE_QUANTITY
        if observation.observed_sellable_quantity is None:
            raise InventoryRuleViolation("fresh observation requires a quantity")
        return (
            ReconciliationStatus.MATCH
            if observation.observed_sellable_quantity == expected_allocation
            else ReconciliationStatus.DRIFT
        )

    def request_stock_change(
        self,
        scope: InventoryScope,
        *,
        command_id: str,
        kind: StockChangeKind,
        submitter_id: str,
        role: ActorRole,
        reason: str,
        value: int,
        expected_version: int,
        expected_authority_revision: int,
    ) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        _require_text(command_id, "command_id")
        _require_text(submitter_id, "submitter_id")
        _require_text(reason, "reason")
        if not isinstance(kind, StockChangeKind):
            raise InventoryRuleViolation("stock change kind is invalid")
        if role is not ActorRole.TENANT_ADMIN:
            raise InventoryRuleViolation("tenant admin role is required")
        existing = self._stock_change(command_id)
        if existing is not None:
            candidate_reason = reason.strip()
            if (
                existing.kind is not kind
                or existing.submitter_id != submitter_id
                or existing.reason != candidate_reason
                or existing.expected_version != expected_version
                or existing.authority_revision != expected_authority_revision
                or existing.change_value != value
            ):
                raise InventoryRuleViolation("command id was reused with different content")
            return self
        if any(
            operation.state
            in {
                AllocationOperationState.PENDING,
                AllocationOperationState.UNKNOWN,
            }
            for operation in self.allocation_operations
        ):
            raise InventoryRuleViolation("allocation operation must be resolved first")
        self._require_version(expected_version)
        self._require_authority_revision(expected_authority_revision)

        if kind is StockChangeKind.PHYSICAL_COUNT:
            _require_nonnegative(value, "counted_on_hand")
            change = value - self.on_hand
        else:
            _require_integer(value, "adjustment_delta")
            change = value

        if any(
            change_record.state is StockChangeState.PENDING for change_record in self.stock_changes
        ):
            raise InventoryRuleViolation("another stock change is pending approval")

        state = StockChangeState.PENDING if change else StockChangeState.RECORDED
        record = StockChange(
            command_id=command_id,
            kind=kind,
            submitter_id=submitter_id,
            reason=reason.strip(),
            expected_version=self.version,
            authority_revision=self.authority_revision,
            before_on_hand=self.on_hand,
            change_value=value,
            state=state,
            resulting_version=self.version if state is StockChangeState.RECORDED else None,
        )
        return replace(self, stock_changes=(*self.stock_changes, record))

    def approve_stock_change(
        self,
        scope: InventoryScope,
        command_id: str,
        approver_id: str,
        role: ActorRole,
        reason: str,
    ) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        _require_text(approver_id, "approver_id")
        _require_text(reason, "reason")
        if role is not ActorRole.TENANT_ADMIN:
            raise InventoryRuleViolation("tenant admin role is required")
        change = self._require_stock_change(command_id)
        if (
            change.state is StockChangeState.APPROVED
            and change.approver_id == approver_id
            and change.review_reason == reason.strip()
        ):
            return self
        if change.state is not StockChangeState.PENDING:
            raise InventoryRuleViolation("stock change is not pending approval")
        if approver_id == change.submitter_id:
            raise InventoryRuleViolation("submitter cannot approve their own stock change")
        self._require_version(change.expected_version)
        self._require_authority_revision(change.authority_revision)
        proposed_on_hand = change.proposed_on_hand
        _require_nonnegative(proposed_on_hand, "resulting_on_hand")
        next_version = self.version + 1
        approved = replace(
            change,
            state=StockChangeState.APPROVED,
            approver_id=approver_id,
            review_reason=reason.strip(),
            resulting_version=next_version,
        )
        return replace(
            self,
            on_hand=proposed_on_hand,
            version=next_version,
            stock_changes=self._replace_stock_change(approved),
        )

    def reject_stock_change(
        self,
        scope: InventoryScope,
        command_id: str,
        reviewer_id: str,
        role: ActorRole,
        reason: str,
    ) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        _require_text(reviewer_id, "reviewer_id")
        _require_text(reason, "reason")
        if role is not ActorRole.TENANT_ADMIN:
            raise InventoryRuleViolation("tenant admin role is required")
        change = self._require_stock_change(command_id)
        if (
            change.state is StockChangeState.REJECTED
            and change.approver_id == reviewer_id
            and change.review_reason == reason.strip()
        ):
            return self
        if change.state is not StockChangeState.PENDING:
            raise InventoryRuleViolation("stock change is not pending review")
        if reviewer_id == change.submitter_id:
            raise InventoryRuleViolation("submitter cannot review their own stock change")
        rejected = replace(
            change,
            state=StockChangeState.REJECTED,
            approver_id=reviewer_id,
            review_reason=reason.strip(),
        )
        return replace(self, stock_changes=self._replace_stock_change(rejected))

    def prepare_allocation_change(
        self,
        scope: InventoryScope,
        *,
        operation_id: str,
        store_id: str,
        desired_quantity: int,
        expected_version: int,
        expected_authority_revision: int,
    ) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        _require_text(operation_id, "operation_id")
        _require_text(store_id, "store_id")
        _require_nonnegative(desired_quantity, "desired_quantity")
        _require_nonnegative(expected_version, "expected_version")
        _require_nonnegative(expected_authority_revision, "expected_authority_revision")

        existing = self._allocation_operation(operation_id)
        if existing is not None:
            if (
                existing.store_id != store_id
                or existing.desired_quantity != desired_quantity
                or existing.expected_version != expected_version
                or existing.authority_revision != expected_authority_revision
            ):
                raise InventoryRuleViolation("operation id was reused with different content")
            return self
        self._require_version(expected_version)
        self._require_authority_revision(expected_authority_revision)
        if self.allocation_writes_frozen:
            raise InventoryRuleViolation("allocation writes are frozen")
        if any(
            operation.store_id == store_id and operation.state is AllocationOperationState.PENDING
            for operation in self.allocation_operations
        ):
            raise InventoryRuleViolation("store already has a pending allocation operation")

        current_quantity = self._allocation_quantity(store_id)
        if desired_quantity > current_quantity:
            reserved_increases = sum(
                max(
                    0,
                    operation.desired_quantity - self._allocation_quantity(operation.store_id),
                )
                for operation in self.allocation_operations
                if operation.state is AllocationOperationState.PENDING
            )
            projected_total = (
                self.allocated_quantity + reserved_increases - current_quantity + desired_quantity
            )
            if projected_total > self.available_to_allocate:
                raise InventoryRuleViolation("store allocations exceed available inventory")
        operation = AllocationOperation(
            operation_id,
            store_id,
            self.version,
            self.authority_revision,
            desired_quantity,
        )
        return replace(
            self,
            allocation_operations=tuple(
                sorted(
                    (*self.allocation_operations, operation),
                    key=lambda item: item.operation_id,
                )
            ),
        )

    def record_allocation_outcome(
        self,
        scope: InventoryScope,
        operation_id: str,
        outcome: AllocationOutcome,
        *,
        accepted_quantity: int | None = None,
    ) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        if not isinstance(outcome, AllocationOutcome):
            raise InventoryRuleViolation("allocation outcome is invalid")
        operation = self._require_allocation_operation(operation_id)
        if (
            outcome is AllocationOutcome.ACCEPTED
            and operation.state
            in {AllocationOperationState.ACCEPTED, AllocationOperationState.OBSERVED}
            and accepted_quantity == operation.desired_quantity
        ):
            return self
        if (
            outcome is AllocationOutcome.REJECTED
            and operation.state is AllocationOperationState.REJECTED
            and accepted_quantity is None
        ):
            return self
        if (
            outcome is AllocationOutcome.UNKNOWN
            and operation.state is AllocationOperationState.UNKNOWN
            and accepted_quantity is None
        ):
            return self
        if operation.state is not AllocationOperationState.PENDING:
            raise InventoryRuleViolation("allocation operation is not pending")

        if outcome is AllocationOutcome.UNKNOWN:
            if accepted_quantity is not None:
                raise InventoryRuleViolation("unknown outcome cannot assert accepted quantity")
            updated_operation = replace(operation, state=AllocationOperationState.UNKNOWN)
            return replace(
                self, allocation_operations=self._replace_allocation_operation(updated_operation)
            )
        if outcome is AllocationOutcome.REJECTED:
            if accepted_quantity is not None:
                raise InventoryRuleViolation("rejected outcome cannot include accepted quantity")
            updated_operation = replace(operation, state=AllocationOperationState.REJECTED)
            return replace(
                self, allocation_operations=self._replace_allocation_operation(updated_operation)
            )
        if accepted_quantity is None:
            raise InventoryRuleViolation("accepted outcome requires the accepted quantity")
        _require_nonnegative(accepted_quantity, "accepted_quantity")
        if accepted_quantity != operation.desired_quantity:
            updated_operation = replace(
                operation,
                state=AllocationOperationState.UNKNOWN,
                last_observed_quantity=accepted_quantity,
            )
            return replace(
                self, allocation_operations=self._replace_allocation_operation(updated_operation)
            )
        if operation.authority_revision != self.authority_revision:
            updated_operation = replace(
                operation,
                state=AllocationOperationState.UNKNOWN,
                last_observed_quantity=accepted_quantity,
            )
            return replace(
                self, allocation_operations=self._replace_allocation_operation(updated_operation)
            )

        updated_operation = replace(operation, state=AllocationOperationState.ACCEPTED)
        return self._apply_allocation_result(updated_operation, accepted_quantity)

    def observe_unknown_allocation(
        self,
        scope: InventoryScope,
        operation_id: str,
        observed_quantity: int,
    ) -> InventoryLedger:
        self._require_scope(scope)
        self._require_csa_authority()
        operation = self._require_allocation_operation(operation_id)
        _require_nonnegative(observed_quantity, "observed_quantity")
        if (
            operation.state is AllocationOperationState.OBSERVED
            and operation.last_observed_quantity == observed_quantity
        ):
            return self
        if operation.state is not AllocationOperationState.UNKNOWN:
            raise InventoryRuleViolation("allocation operation is not unresolved")
        if (
            observed_quantity != operation.desired_quantity
            or operation.authority_revision != self.authority_revision
        ):
            updated_operation = replace(operation, last_observed_quantity=observed_quantity)
            return replace(
                self, allocation_operations=self._replace_allocation_operation(updated_operation)
            )

        updated_operation = replace(
            operation,
            state=AllocationOperationState.OBSERVED,
            last_observed_quantity=observed_quantity,
        )
        return self._apply_allocation_result(updated_operation, observed_quantity)

    def _apply_allocation_result(
        self, operation: AllocationOperation, accepted_quantity: int
    ) -> InventoryLedger:
        allocations = tuple(
            allocation
            for allocation in self.allocations
            if allocation.store_id != operation.store_id
        )
        if accepted_quantity:
            allocations = (*allocations, StoreAllocation(operation.store_id, accepted_quantity))
        return replace(
            self,
            version=self.version + 1,
            allocations=tuple(sorted(allocations, key=lambda item: item.store_id)),
            allocation_operations=self._replace_allocation_operation(operation),
        )

    def _require_scope(self, scope: InventoryScope) -> None:
        if scope != self.scope:
            raise InventoryRuleViolation("inventory scope does not match ledger")

    def _require_csa_authority(self) -> None:
        if self.authority_mode is not AuthorityMode.CSA_MANAGED:
            raise InventoryRuleViolation("CSA does not have inventory write authority")

    def _require_version(self, expected_version: int) -> None:
        _require_nonnegative(expected_version, "expected_version")
        if expected_version != self.version:
            raise StaleInventoryVersion("inventory version changed")

    def _require_authority_revision(self, expected_revision: int) -> None:
        _require_nonnegative(expected_revision, "expected_authority_revision")
        if expected_revision != self.authority_revision:
            raise StaleInventoryVersion("inventory authority revision changed")

    def _reservation(self, order_line_id: str) -> Reservation | None:
        return next(
            (item for item in self.reservations if item.order_line_id == order_line_id), None
        )

    def _return_case(self, return_id: str) -> ReturnCase | None:
        return next((item for item in self.returns if item.return_id == return_id), None)

    def _require_return_case(self, return_id: str) -> ReturnCase:
        _require_text(return_id, "return_id")
        return_case = self._return_case(return_id)
        if return_case is None:
            raise InventoryRuleViolation("return does not exist")
        return return_case

    def _replace_return(self, updated: ReturnCase) -> tuple[ReturnCase, ...]:
        return tuple(
            updated if item.return_id == updated.return_id else item for item in self.returns
        )

    def _require_reservation(self, order_line_id: str) -> Reservation:
        _require_text(order_line_id, "order_line_id")
        reservation = self._reservation(order_line_id)
        if reservation is None:
            raise InventoryRuleViolation("reservation does not exist")
        return reservation

    def _replace_reservation(
        self, updated: Reservation, *, on_hand: int | None = None
    ) -> InventoryLedger:
        return replace(
            self,
            version=self.version + 1,
            on_hand=self.on_hand if on_hand is None else on_hand,
            reservations=tuple(
                updated if item.order_line_id == updated.order_line_id else item
                for item in self.reservations
            ),
        )

    def _stock_change(self, command_id: str) -> StockChange | None:
        return next((item for item in self.stock_changes if item.command_id == command_id), None)

    def _require_stock_change(self, command_id: str) -> StockChange:
        _require_text(command_id, "command_id")
        change = self._stock_change(command_id)
        if change is None:
            raise InventoryRuleViolation("stock change does not exist")
        return change

    def _replace_stock_change(self, updated: StockChange) -> tuple[StockChange, ...]:
        return tuple(
            updated if item.command_id == updated.command_id else item
            for item in self.stock_changes
        )

    def _allocation_quantity(self, store_id: str) -> int:
        allocation = next((item for item in self.allocations if item.store_id == store_id), None)
        return 0 if allocation is None else allocation.quantity

    def _allocation_operation(self, operation_id: str) -> AllocationOperation | None:
        return next(
            (item for item in self.allocation_operations if item.operation_id == operation_id),
            None,
        )

    def _require_allocation_operation(self, operation_id: str) -> AllocationOperation:
        _require_text(operation_id, "operation_id")
        operation = self._allocation_operation(operation_id)
        if operation is None:
            raise InventoryRuleViolation("allocation operation does not exist")
        return operation

    def _replace_allocation_operation(
        self, updated: AllocationOperation
    ) -> tuple[AllocationOperation, ...]:
        return tuple(
            updated if item.operation_id == updated.operation_id else item
            for item in self.allocation_operations
        )


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InventoryRuleViolation(f"{name} must be non-empty")


def _require_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InventoryRuleViolation(f"{name} must be an integer")


def _require_nonnegative(value: int, name: str) -> None:
    _require_integer(value, name)
    if value < 0:
        raise InventoryRuleViolation(f"{name} must be non-negative")


def _require_positive(value: int, name: str) -> None:
    _require_integer(value, name)
    if value <= 0:
        raise InventoryRuleViolation(f"{name} must be positive")


def _require_unique(values: Iterable[str], name: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise InventoryRuleViolation(f"{name} values must be unique")
