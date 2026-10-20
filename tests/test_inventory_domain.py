from __future__ import annotations

from dataclasses import replace
from itertools import product

import pytest
from csa.inventory.domain import (
    ActorRole,
    AllocationOperation,
    AllocationOperationState,
    AllocationOutcome,
    AuthorityMode,
    InventoryLedger,
    InventoryObservation,
    InventoryRuleViolation,
    InventoryScope,
    MappingState,
    ObservationFreshness,
    ReconciliationStatus,
    ReturnState,
    StaleInventoryVersion,
    StockChangeKind,
    StockChangeState,
    StoreAllocation,
)

SCOPE = InventoryScope("tenant-synthetic-a", "variant-synthetic-a", "warehouse-default")


def test_pending_count_blocks_new_claims_but_terminal_work_can_stale_it() -> None:
    reserved = ledger().reserve(SCOPE, "existing", 2)
    pending = reserved.request_stock_change(
        SCOPE,
        command_id="pending-count",
        kind=StockChangeKind.PHYSICAL_COUNT,
        submitter_id="admin-a",
        role=ActorRole.TENANT_ADMIN,
        reason="Synthetic count",
        value=9,
        expected_version=reserved.version,
        expected_authority_revision=1,
    )
    assert pending.available_to_allocate == 0
    assert pending.allocation_writes_frozen
    assert pending.reserve(SCOPE, "existing", 2) is pending
    with pytest.raises(InventoryRuleViolation, match="different reservation"):
        pending.reserve(SCOPE, "existing", 3)
    with pytest.raises(InventoryRuleViolation, match="pending approval"):
        pending.reserve(SCOPE, "new", 1)
    fulfilled = pending.fulfil_reservation(SCOPE, "existing")
    with pytest.raises(StaleInventoryVersion):
        fulfilled.approve_stock_change(
            SCOPE, "pending-count", "admin-b", ActorRole.TENANT_ADMIN, "Review"
        )
    rejected = fulfilled.reject_stock_change(
        SCOPE, "pending-count", "admin-b", ActorRole.TENANT_ADMIN, "Stale count"
    )
    assert rejected.on_hand == fulfilled.on_hand
    assert not rejected.has_pending_stock_change
    assert rejected.available_to_allocate == 8


def ledger(
    *,
    authority_mode: AuthorityMode = AuthorityMode.CSA_MANAGED,
    on_hand: int = 10,
    safety_stock: int = 0,
    allocations: tuple[StoreAllocation, ...] = (),
) -> InventoryLedger:
    return InventoryLedger(
        scope=SCOPE,
        authority_mode=authority_mode,
        authority_revision=1,
        version=0,
        on_hand=on_hand,
        safety_stock=safety_stock,
        allocations=allocations,
    )


def allocation_operation(current: InventoryLedger, operation_id: str) -> AllocationOperation:
    return next(
        operation
        for operation in current.allocation_operations
        if operation.operation_id == operation_id
    )


def observation(
    store_id: str,
    quantity: int | None,
    *,
    freshness: ObservationFreshness = ObservationFreshness.FRESH,
    mapping: MappingState = MappingState.VERIFIED,
    feed_complete: bool = True,
    status_known: bool = True,
    comparable: bool = True,
) -> InventoryObservation:
    return InventoryObservation(
        store_id=store_id,
        freshness=freshness,
        mapping_state=mapping,
        observed_sellable_quantity=quantity,
        order_feed_complete=feed_complete,
        provider_status_known=status_known,
        quantity_comparable=comparable,
    )


def test_scope_and_authority_are_required_for_local_reservations() -> None:
    current = ledger()
    with pytest.raises(InventoryRuleViolation, match="scope"):
        current.reserve(
            InventoryScope("tenant-synthetic-b", SCOPE.variant_id, SCOPE.warehouse_id),
            "line-1",
            1,
        )
    with pytest.raises(InventoryRuleViolation, match="authority"):
        ledger(authority_mode=AuthorityMode.EXTERNAL_AUTHORITATIVE).reserve(SCOPE, "line-1", 1)
    assert current.on_hand == 10
    assert current.reservations == ()


def test_reservation_is_idempotent_and_cannot_exceed_available_stock() -> None:
    current = ledger(on_hand=5, safety_stock=1)
    reserved = current.reserve(SCOPE, "line-1", 3)

    assert reserved.reserve(SCOPE, "line-1", 3) is reserved
    assert reserved.active_reserved == 3
    assert reserved.available_to_allocate == 1
    with pytest.raises(InventoryRuleViolation, match="different reservation"):
        reserved.reserve(SCOPE, "line-1", 2)
    with pytest.raises(InventoryRuleViolation, match="insufficient"):
        reserved.reserve(SCOPE, "line-2", 2)
    assert current.active_reserved == 0


def test_fulfilment_and_cancellation_are_single_use_transitions() -> None:
    reserved = ledger(on_hand=5).reserve(SCOPE, "line-1", 2)
    fulfilled = reserved.fulfil_reservation(SCOPE, "line-1")

    assert fulfilled.on_hand == 3
    assert fulfilled.active_reserved == 0
    assert fulfilled.version == reserved.version + 1
    assert fulfilled.fulfil_reservation(SCOPE, "line-1") is fulfilled
    with pytest.raises(InventoryRuleViolation, match="fulfilled"):
        fulfilled.cancel_reservation(SCOPE, "line-1")

    cancelled = reserved.cancel_reservation(SCOPE, "line-1")
    assert cancelled.on_hand == 5
    assert cancelled.active_reserved == 0
    assert cancelled.cancel_reservation(SCOPE, "line-1") is cancelled
    with pytest.raises(InventoryRuleViolation, match="cancelled"):
        cancelled.fulfil_reservation(SCOPE, "line-1")


def test_count_adjustment_requires_second_admin_and_is_auditable() -> None:
    current = ledger()
    pending = current.request_stock_change(
        SCOPE,
        command_id="count-1",
        kind=StockChangeKind.PHYSICAL_COUNT,
        submitter_id="admin-a",
        role=ActorRole.TENANT_ADMIN,
        reason="cycle count",
        value=8,
        expected_version=current.version,
        expected_authority_revision=1,
    )

    assert pending.on_hand == 10
    assert pending.available_to_allocate == 0
    assert pending.stock_changes[0].state is StockChangeState.PENDING
    with pytest.raises(InventoryRuleViolation, match="own"):
        pending.approve_stock_change(
            SCOPE, "count-1", "admin-a", ActorRole.TENANT_ADMIN, "self review"
        )
    with pytest.raises(InventoryRuleViolation, match="tenant admin"):
        pending.approve_stock_change(SCOPE, "count-1", "staff", ActorRole.OTHER, "review")

    approved = pending.approve_stock_change(
        SCOPE, "count-1", "admin-b", ActorRole.TENANT_ADMIN, "count evidence reviewed"
    )
    record = approved.stock_changes[0]
    assert approved.on_hand == 8
    assert record.state is StockChangeState.APPROVED
    assert record.approver_id == "admin-b"
    assert record.review_reason == "count evidence reviewed"
    assert record.before_on_hand == 10
    assert record.proposed_on_hand == 8
    assert record.resulting_version == approved.version

    assert pending.on_hand == 10
    assert pending.stock_changes[0].state is StockChangeState.PENDING
    assert (
        pending.request_stock_change(
            SCOPE,
            command_id="count-1",
            kind=StockChangeKind.PHYSICAL_COUNT,
            submitter_id="admin-a",
            role=ActorRole.TENANT_ADMIN,
            reason="cycle count",
            value=8,
            expected_version=0,
            expected_authority_revision=1,
        )
        is pending
    )
    assert (
        approved.approve_stock_change(
            SCOPE,
            "count-1",
            "admin-b",
            ActorRole.TENANT_ADMIN,
            "count evidence reviewed",
        )
        is approved
    )


def test_zero_variance_count_needs_no_approval_and_reject_preserves_stock() -> None:
    current = ledger()
    recorded = current.request_stock_change(
        SCOPE,
        command_id="count-zero",
        kind=StockChangeKind.PHYSICAL_COUNT,
        submitter_id="admin-a",
        role=ActorRole.TENANT_ADMIN,
        reason="verified count",
        value=10,
        expected_version=0,
        expected_authority_revision=1,
    )
    assert recorded.on_hand == 10
    assert recorded.version == current.version
    assert recorded.stock_changes[0].state is StockChangeState.RECORDED
    assert recorded.available_to_allocate == 10

    pending = recorded.request_stock_change(
        SCOPE,
        command_id="adjust-1",
        kind=StockChangeKind.MANUAL_ADJUSTMENT,
        submitter_id="admin-a",
        role=ActorRole.TENANT_ADMIN,
        reason="damaged unit",
        value=-1,
        expected_version=recorded.version,
        expected_authority_revision=1,
    )
    rejected = pending.reject_stock_change(
        SCOPE, "adjust-1", "admin-b", ActorRole.TENANT_ADMIN, "evidence insufficient"
    )
    assert rejected.on_hand == 10
    assert rejected.stock_changes[-1].state is StockChangeState.REJECTED
    assert rejected.stock_changes[-1].review_reason == "evidence insufficient"
    assert (
        rejected.reject_stock_change(
            SCOPE,
            "adjust-1",
            "admin-b",
            ActorRole.TENANT_ADMIN,
            "evidence insufficient",
        )
        is rejected
    )
    assert rejected.available_to_allocate == 10


def test_stale_stock_change_cannot_be_approved() -> None:
    reserved = ledger(on_hand=5).reserve(SCOPE, "line-1", 1)
    pending = reserved.request_stock_change(
        SCOPE,
        command_id="adjust-stale",
        kind=StockChangeKind.MANUAL_ADJUSTMENT,
        submitter_id="admin-a",
        role=ActorRole.TENANT_ADMIN,
        reason="record shrinkage",
        value=-1,
        expected_version=reserved.version,
        expected_authority_revision=1,
    )
    changed = pending.fulfil_reservation(SCOPE, "line-1")

    with pytest.raises(StaleInventoryVersion):
        changed.approve_stock_change(
            SCOPE, "adjust-stale", "admin-b", ActorRole.TENANT_ADMIN, "review"
        )
    assert changed.on_hand == 4
    assert changed.stock_changes[0].state is StockChangeState.PENDING


def test_authority_revision_fences_approval_and_ambiguous_allocation() -> None:
    current = ledger()
    pending_change = current.request_stock_change(
        SCOPE,
        command_id="adjust-revision",
        kind=StockChangeKind.MANUAL_ADJUSTMENT,
        submitter_id="admin-a",
        role=ActorRole.TENANT_ADMIN,
        reason="cycle correction",
        value=1,
        expected_version=0,
        expected_authority_revision=1,
    )
    new_authority = replace(pending_change, authority_revision=2)
    with pytest.raises(StaleInventoryVersion, match="authority"):
        new_authority.approve_stock_change(
            SCOPE, "adjust-revision", "admin-b", ActorRole.TENANT_ADMIN, "review"
        )

    intent = current.prepare_allocation_change(
        SCOPE,
        operation_id="allocation-revision",
        store_id="store-a",
        desired_quantity=2,
        expected_version=0,
        expected_authority_revision=1,
    )
    changed_authority = replace(intent, authority_revision=2)
    uncertain = changed_authority.record_allocation_outcome(
        SCOPE,
        "allocation-revision",
        AllocationOutcome.ACCEPTED,
        accepted_quantity=2,
    )
    assert uncertain.allocation_writes_frozen
    assert (
        allocation_operation(uncertain, "allocation-revision").state
        is AllocationOperationState.UNKNOWN
    )


def test_fixed_allocations_require_confirmed_reduction_before_increase() -> None:
    current = ledger(
        on_hand=10,
        allocations=(
            StoreAllocation("store-a", 4),
            StoreAllocation("store-b", 2),
        ),
    )
    reduction = current.prepare_allocation_change(
        SCOPE,
        operation_id="reduce-a",
        store_id="store-a",
        desired_quantity=2,
        expected_version=0,
        expected_authority_revision=1,
    )
    with pytest.raises(InventoryRuleViolation, match="exceed"):
        reduction.prepare_allocation_change(
            SCOPE,
            operation_id="increase-b-too-soon",
            store_id="store-b",
            desired_quantity=7,
            expected_version=0,
            expected_authority_revision=1,
        )

    reduced = reduction.record_allocation_outcome(
        SCOPE,
        "reduce-a",
        AllocationOutcome.ACCEPTED,
        accepted_quantity=2,
    )
    increased = reduced.prepare_allocation_change(
        SCOPE,
        operation_id="increase-b",
        store_id="store-b",
        desired_quantity=7,
        expected_version=reduced.version,
        expected_authority_revision=1,
    )
    accepted = increased.record_allocation_outcome(
        SCOPE,
        "increase-b",
        AllocationOutcome.ACCEPTED,
        accepted_quantity=7,
    )
    assert [(item.store_id, item.quantity) for item in accepted.allocations] == [
        ("store-a", 2),
        ("store-b", 7),
    ]
    assert accepted.allocated_quantity == 9
    assert accepted.version == 2


def test_pending_allocation_increases_reserve_capacity() -> None:
    current = ledger(on_hand=10)
    pending = current.prepare_allocation_change(
        SCOPE,
        operation_id="increase-a",
        store_id="store-a",
        desired_quantity=6,
        expected_version=0,
        expected_authority_revision=1,
    )
    with pytest.raises(InventoryRuleViolation, match="exceed"):
        pending.prepare_allocation_change(
            SCOPE,
            operation_id="increase-b",
            store_id="store-b",
            desired_quantity=5,
            expected_version=0,
            expected_authority_revision=1,
        )


def test_unknown_allocation_freezes_writes_until_matching_observation() -> None:
    current = ledger()
    pending = current.prepare_allocation_change(
        SCOPE,
        operation_id="write-unknown",
        store_id="store-a",
        desired_quantity=4,
        expected_version=0,
        expected_authority_revision=1,
    )
    unknown = pending.record_allocation_outcome(SCOPE, "write-unknown", AllocationOutcome.UNKNOWN)

    assert unknown.allocation_writes_frozen
    assert allocation_operation(unknown, "write-unknown").state is AllocationOperationState.UNKNOWN
    with pytest.raises(InventoryRuleViolation, match="frozen"):
        unknown.prepare_allocation_change(
            SCOPE,
            operation_id="write-again",
            store_id="store-b",
            desired_quantity=1,
            expected_version=0,
            expected_authority_revision=1,
        )
    mismatch = unknown.observe_unknown_allocation(SCOPE, "write-unknown", 3)
    assert mismatch.allocation_writes_frozen
    assert allocation_operation(mismatch, "write-unknown").last_observed_quantity == 3

    observed = mismatch.observe_unknown_allocation(SCOPE, "write-unknown", 4)
    assert not observed.allocation_writes_frozen
    assert observed.allocations == (StoreAllocation("store-a", 4),)
    assert (
        allocation_operation(observed, "write-unknown").state is AllocationOperationState.OBSERVED
    )
    assert observed.observe_unknown_allocation(SCOPE, "write-unknown", 4) is observed


def test_allocation_and_stock_change_replays_reject_changed_payloads() -> None:
    current = ledger()
    count = current.request_stock_change(
        SCOPE,
        command_id="count-1",
        kind=StockChangeKind.MANUAL_ADJUSTMENT,
        submitter_id="admin-a",
        role=ActorRole.TENANT_ADMIN,
        reason="adjustment",
        value=1,
        expected_version=0,
        expected_authority_revision=1,
    )
    with pytest.raises(InventoryRuleViolation, match="reused"):
        count.request_stock_change(
            SCOPE,
            command_id="count-1",
            kind=StockChangeKind.MANUAL_ADJUSTMENT,
            submitter_id="admin-a",
            role=ActorRole.TENANT_ADMIN,
            reason="different adjustment",
            value=1,
            expected_version=0,
            expected_authority_revision=1,
        )

    operation = current.prepare_allocation_change(
        SCOPE,
        operation_id="allocation-1",
        store_id="store-a",
        desired_quantity=2,
        expected_version=0,
        expected_authority_revision=1,
    )
    with pytest.raises(InventoryRuleViolation, match="reused"):
        operation.prepare_allocation_change(
            SCOPE,
            operation_id="allocation-1",
            store_id="store-a",
            desired_quantity=3,
            expected_version=0,
            expected_authority_revision=1,
        )


def test_return_restocks_only_sellable_units_after_receipt_and_inspection() -> None:
    fulfilled = ledger(on_hand=4).reserve(SCOPE, "line-1", 3).fulfil_reservation(SCOPE, "line-1")
    requested = fulfilled.request_return(
        SCOPE, return_id="return-a", order_line_id="line-1", quantity=2
    )

    assert requested.on_hand == 1
    assert requested.returns[0].state is ReturnState.REQUESTED
    assert (
        requested.request_return(SCOPE, return_id="return-a", order_line_id="line-1", quantity=2)
        is requested
    )
    with pytest.raises(InventoryRuleViolation, match="exceed"):
        requested.request_return(SCOPE, return_id="return-b", order_line_id="line-1", quantity=2)
    with pytest.raises(InventoryRuleViolation, match="received"):
        requested.inspect_return(
            SCOPE, "return-a", "inspector-a", sellable_quantity=2, reason="looks good"
        )

    received = requested.receive_return(SCOPE, "return-a", "receiver-a", "parcel received")
    assert received.on_hand == 1
    assert received.receive_return(SCOPE, "return-a", "receiver-a", "parcel received") is received
    with pytest.raises(InventoryRuleViolation, match="differently"):
        received.receive_return(SCOPE, "return-a", "receiver-b", "different receipt")

    inspected = received.inspect_return(
        SCOPE, "return-a", "inspector-a", sellable_quantity=1, reason="one unit resellable"
    )
    assert inspected.on_hand == 2
    assert inspected.returns[0].state is ReturnState.INSPECTED
    assert inspected.returns[0].sellable_quantity == 1
    assert inspected.returns[0].resulting_version == inspected.version
    assert (
        inspected.inspect_return(
            SCOPE, "return-a", "inspector-a", sellable_quantity=1, reason="one unit resellable"
        )
        is inspected
    )
    with pytest.raises(InventoryRuleViolation, match="differently"):
        inspected.inspect_return(
            SCOPE, "return-a", "inspector-a", sellable_quantity=2, reason="different"
        )


def test_non_sellable_return_is_recorded_without_restock_and_requires_receipt() -> None:
    fulfilled = ledger(on_hand=2).reserve(SCOPE, "line-1", 2).fulfil_reservation(SCOPE, "line-1")
    requested = fulfilled.request_return(
        SCOPE, return_id="return-damaged", order_line_id="line-1", quantity=2
    )
    with pytest.raises(InventoryRuleViolation, match="received"):
        requested.inspect_return(
            SCOPE, "return-damaged", "inspector-a", sellable_quantity=0, reason="damaged"
        )

    returned = requested.receive_return(SCOPE, "return-damaged", "receiver-a", "parcel received")
    inspected = returned.inspect_return(
        SCOPE, "return-damaged", "inspector-a", sellable_quantity=0, reason="damaged"
    )
    assert inspected.on_hand == 0
    assert inspected.returns[0].sellable_quantity == 0
    assert inspected.version == returned.version + 1


def test_report_only_reconciliation_distinguishes_zero_missing_and_stale() -> None:
    current = ledger(
        on_hand=8,
        allocations=(StoreAllocation("store-zero", 0), StoreAllocation("store-stock", 5)),
    )
    report = current.reconcile(
        (
            observation("store-zero", 0),
            observation("store-stock", 0),
            observation(
                "store-missing",
                None,
                freshness=ObservationFreshness.MISSING,
            ),
            observation(
                "store-stale",
                None,
                freshness=ObservationFreshness.STALE,
            ),
        )
    )
    rows = {row.store_id: row for row in report.rows}

    assert rows["store-zero"].status is ReconciliationStatus.MATCH
    assert rows["store-zero"].delta == 0
    assert not rows["store-zero"].writes_blocked
    assert rows["store-stock"].status is ReconciliationStatus.DRIFT
    assert rows["store-stock"].delta == -5
    assert rows["store-stock"].writes_blocked
    assert rows["store-missing"].status is ReconciliationStatus.MISSING_OBSERVATION
    assert rows["store-missing"].observed_sellable_quantity is None
    assert rows["store-missing"].delta is None
    assert rows["store-stale"].status is ReconciliationStatus.STALE_OBSERVATION
    assert rows["store-stale"].delta is None
    assert report.inventory_version == current.version
    assert current.on_hand == 8
    assert current.allocations == (
        StoreAllocation("store-zero", 0),
        StoreAllocation("store-stock", 5),
    )


@pytest.mark.parametrize(
    ("observed", "expected_status"),
    [
        (
            observation("store-a", 5, mapping=MappingState.MISSING),
            ReconciliationStatus.MISSING_MAPPING,
        ),
        (
            observation("store-a", 5, mapping=MappingState.AMBIGUOUS),
            ReconciliationStatus.AMBIGUOUS_MAPPING,
        ),
        (
            observation("store-a", 5, feed_complete=False),
            ReconciliationStatus.INCOMPLETE_ORDER_FEED,
        ),
        (
            observation("store-a", 5, status_known=False),
            ReconciliationStatus.UNKNOWN_PROVIDER_STATUS,
        ),
        (
            observation("store-a", 5, comparable=False),
            ReconciliationStatus.INCOMPARABLE_QUANTITY,
        ),
    ],
)
def test_reconciliation_blocks_uncertain_or_uncomparable_observations(
    observed: InventoryObservation, expected_status: ReconciliationStatus
) -> None:
    current = ledger(allocations=(StoreAllocation("store-a", 5),))
    report = current.reconcile((observed,))

    assert report.rows[0].status is expected_status
    assert report.rows[0].writes_blocked
    assert report.rows[0].delta is None
    assert current.allocations == (StoreAllocation("store-a", 5),)
    assert current.on_hand == 10


def test_reconciliation_rejects_duplicate_observation_rows_without_mutation() -> None:
    current = ledger()
    rows = (observation("store-a", 0), observation("store-a", 0))

    with pytest.raises(InventoryRuleViolation, match="unique"):
        current.reconcile(rows)
    assert current.version == 0
    assert current.on_hand == 10


@pytest.mark.parametrize("actions", tuple(product(range(6), repeat=4)))
def test_generated_reservation_sequences_preserve_stock_conservation(
    actions: tuple[int, ...],
) -> None:
    initial_on_hand = 4
    initial = ledger(on_hand=initial_on_hand, safety_stock=1)
    current = initial
    transitions = (
        ("reserve", "line-a"),
        ("reserve", "line-b"),
        ("cancel", "line-a"),
        ("cancel", "line-b"),
        ("fulfil", "line-a"),
        ("fulfil", "line-b"),
    )

    for action_index in actions:
        operation, line_id = transitions[action_index]
        before = current
        try:
            if operation == "reserve":
                current = current.reserve(SCOPE, line_id, 1)
            elif operation == "cancel":
                current = current.cancel_reservation(SCOPE, line_id)
            else:
                current = current.fulfil_reservation(SCOPE, line_id)
        except InventoryRuleViolation:
            current = before

        fulfilled_quantity = sum(
            reservation.quantity
            for reservation in current.reservations
            if reservation.state.value == "fulfilled"
        )
        assert current.on_hand + fulfilled_quantity == initial_on_hand
        assert current.active_reserved + current.safety_stock <= current.on_hand
        assert current.available_to_allocate == (
            current.on_hand - current.active_reserved - current.safety_stock
        )
        assert before.on_hand >= 0


@pytest.mark.parametrize(("first", "second"), tuple(product(range(6), repeat=2)))
def test_generated_allocation_sequences_never_overallocate(first: int, second: int) -> None:
    current = ledger(on_hand=5)
    first_intent = current.prepare_allocation_change(
        SCOPE,
        operation_id="op-a",
        store_id="store-a",
        desired_quantity=first,
        expected_version=0,
        expected_authority_revision=1,
    )
    accepted_first = first_intent.record_allocation_outcome(
        SCOPE, "op-a", AllocationOutcome.ACCEPTED, accepted_quantity=first
    )
    try:
        second_intent = accepted_first.prepare_allocation_change(
            SCOPE,
            operation_id="op-b",
            store_id="store-b",
            desired_quantity=second,
            expected_version=accepted_first.version,
            expected_authority_revision=1,
        )
    except InventoryRuleViolation:
        assert first + second > accepted_first.available_to_allocate
        assert accepted_first.allocated_quantity == first
        return

    accepted_both = second_intent.record_allocation_outcome(
        SCOPE, "op-b", AllocationOutcome.ACCEPTED, accepted_quantity=second
    )
    assert accepted_both.allocated_quantity == first + second
    assert accepted_both.allocated_quantity <= accepted_both.available_to_allocate
