"""The deterministic area selector: which areas a turn's scoped prompt includes."""

from __future__ import annotations

import pytest
from csa.workspace.area_select import select_areas, word_rule_areas
from csa.workspace.planner import PriorTurn
from csa.workspace.prompt_areas import Area
from csa.workspace.routing import Needs

NONE = Needs(manager=False, mail=False, team=False, training=False, people=False)


def _needs(**on: bool) -> Needs:
    return Needs(
        manager=on.get("manager", False),
        mail=on.get("mail", False),
        team=on.get("team", False),
        training=on.get("training", False),
        people=on.get("people", False),
    )


def _prior(message: str, action: str) -> PriorTurn:
    return PriorTurn(message=message, action=action, result="proposal")


def _set(*areas: Area) -> frozenset[Area]:
    return frozenset(areas)


# (id, message, tab, record_kind, previous, needs, expected)
ROWS: list[
    tuple[str, str, str | None, str | None, PriorTurn | None, Needs, frozenset[Area] | None]
] = [
    # Each tab alone, on a message with no words of its own.
    ("tab-hr", "hello", "hr", None, None, NONE, _set("hr")),
    ("tab-email", "hello", "email", None, None, NONE, _set("email")),
    ("tab-inbox", "hello", "inbox", None, None, NONE, _set("inbox", "email")),
    ("tab-bookings", "hello", "bookings", None, None, NONE, _set("bookings")),
    ("tab-training", "hello", "training_library", None, None, NONE, _set("training")),
    ("tab-business", "hello", "business", None, None, NONE, _set("inbox", "products")),
    ("tab-overview-nothing", "hello", "overview", None, None, NONE, None),
    ("no-signal", "hello", None, None, None, NONE, None),
    # Words in English.
    (
        "en-bookings-book",
        "book a room for Friday at 3 pm",
        None,
        None,
        None,
        NONE,
        _set("bookings"),
    ),
    ("en-bookings-move", "move my 3 pm to 4 pm", None, None, None, NONE, _set("bookings")),
    ("en-bookings-free", "when is Aina free tomorrow?", None, None, None, NONE, _set("bookings")),
    (
        "en-bookings-busy",
        "is the boardroom busy on Monday",
        None,
        None,
        None,
        NONE,
        _set("bookings"),
    ),
    (
        "en-bookings-reschedule",
        "reschedule the haircut to Tuesday",
        None,
        None,
        None,
        NONE,
        _set("bookings"),
    ),
    ("en-products-stock", "how many are in stock", None, None, None, NONE, _set("products")),
    (
        "en-products-inventory",
        "inventory for the blue mug",
        None,
        None,
        None,
        NONE,
        _set("products"),
    ),
    ("en-inbox-approve", "approve the held reply", None, None, None, NONE, _set("inbox", "email")),
    ("en-inbox-reject", "reject that", None, None, None, NONE, _set("inbox")),
    (
        "en-hr-onboarding",
        "who is onboarding this month",
        None,
        None,
        None,
        NONE,
        _set("hr"),
    ),
    ("en-hr-tasks", "what are my tasks", None, None, None, NONE, _set("hr")),
    ("en-hr-profile", "open the profile of Aina", None, None, None, NONE, _set("hr")),
    ("en-hr-leave", "mark Aina as leaving on 30 June", None, None, None, NONE, _set("hr")),
    ("en-training", "request a first aid course", None, None, None, NONE, _set("training")),
    (
        "en-training-video",
        "what does the onboarding video say",
        None,
        None,
        None,
        NONE,
        _set("training", "hr"),
    ),
    ("en-email-word", "reply to the supplier", None, None, None, NONE, _set("email")),
    # Words in Malay.
    ("ms-bookings-tempah", "tempah bilik mesyuarat esok", None, None, None, NONE, _set("bookings")),
    (
        "ms-bookings-batal",
        "batalkan tempahan saya pukul 3",
        None,
        None,
        None,
        NONE,
        _set("bookings"),
    ),
    ("ms-bookings-kosong", "bila Aina kosong esok", None, None, None, NONE, _set("bookings")),
    ("ms-products-stok", "berapa stok mug biru", None, None, None, NONE, _set("products")),
    (
        "ms-inbox-lulus",
        "luluskan balasan yang ditahan",
        None,
        None,
        None,
        NONE,
        _set("inbox", "email"),
    ),
    ("ms-hr-tugasan", "apa tugasan saya", None, None, None, NONE, _set("hr")),
    (
        "ms-hr-berhenti",
        "siapa yang akan berhenti bulan depan",
        None,
        None,
        None,
        NONE,
        _set("hr"),
    ),
    ("ms-hr-profil", "buka profil Aina", None, None, None, NONE, _set("hr")),
    (
        "ms-training",
        "mohon kursus latihan pertolongan cemas",
        None,
        None,
        None,
        NONE,
        _set("training"),
    ),
    ("ms-email", "balas e-mel daripada pembekal", None, None, None, NONE, _set("email")),
    # Findings from the prompt review: the TikTok guard is products-only.
    ("tiktok-order", "where is my TikTok order", None, None, None, NONE, _set("products")),
    ("tiktok-refund", "refund this customer on TikTok", None, None, None, NONE, _set("products")),
    (
        "tiktok-cancel-order",
        "cancel the order",
        None,
        None,
        None,
        NONE,
        _set("products", "bookings"),
    ),
    ("tiktok-stock", "change the stock on TikTok", None, None, None, NONE, _set("products", "hr")),
    ("tiktok-pesanan", "mana pesanan TikTok saya", None, None, None, NONE, _set("products")),
    # A name lookup is email's directory_lookup, even on another area's turn.
    ("lookup-who-is", "who is Danial", None, None, None, NONE, _set("email", "hr")),
    ("lookup-siapa", "siapa Danial", None, None, None, NONE, _set("email", "hr")),
    ("lookup-find", "find Danial", None, None, None, NONE, _set("email")),
    ("lookup-look-up", "look up Danial please", None, None, None, NONE, _set("email")),
    ("lookup-on-hr-tab", "who is Danial", "hr", None, None, NONE, _set("hr", "email")),
    (
        "lookup-on-bookings-tab",
        "find Danial",
        "bookings",
        None,
        None,
        NONE,
        _set("bookings", "email"),
    ),
    # I1: plain stock questions in "available" words select products as well as bookings.
    (
        "avail-kettle",
        "is the blue kettle available",
        None,
        None,
        None,
        NONE,
        _set("products", "bookings"),
    ),
    ("avail-item", "is the item available", None, None, None, NONE, _set("products", "bookings")),
    ("avail-have-any", "do we have any mugs left", None, None, None, NONE, _set("products")),
    ("avail-sold-out", "is it sold out", None, None, None, NONE, _set("products")),
    ("avail-restock", "when will you restock", None, None, None, NONE, _set("products")),
    ("avail-habis", "mug biru dah habis ke", None, None, None, NONE, _set("products")),
    ("avail-ada-stok", "ada stok lagi", None, None, None, NONE, _set("products")),
    (
        "avail-slots-still-bookings",
        "what slots are available",
        None,
        None,
        None,
        NONE,
        _set("bookings"),
    ),
    # I2: who-is questions are also people questions (people_query, hr_process_read).
    ("who-joining", "who is joining next month", None, None, None, NONE, _set("email", "hr")),
    ("who-off", "who is off tomorrow", None, None, None, NONE, _set("email", "hr")),
    ("who-new", "who's new this week", None, None, None, NONE, _set("email", "hr")),
    (
        "siapa-masuk",
        "siapa yang akan masuk bulan depan",
        None,
        None,
        None,
        NONE,
        _set("email", "hr"),
    ),
    # Task 6: an HR request says "HR", a process "step" ("langkah") or that it "is running late".
    ("hr-word-en", "open the HR tab", None, None, None, NONE, _set("hr")),
    ("hr-word-ms", "tolong buka tab HR untuk saya", None, None, None, NONE, _set("hr")),
    (
        "hr-step-en",
        "I have finished the bank details step, please mark it done",
        None,
        None,
        None,
        NONE,
        _set("hr"),
    ),
    (
        "hr-step-ms",
        "saya sudah siap langkah butiran bank, tolong tandakan selesai",
        None,
        None,
        None,
        NONE,
        _set("hr"),
    ),
    ("hr-running-late", "is anything running late", None, None, None, NONE, _set("hr")),
    # ... and none of those words widens an unrelated message: "late" alone and "hrs" are not HR.
    ("late-alone", "I will be late tomorrow", None, None, None, NONE, None),
    ("hrs-not-hr", "we open 9 hrs a day", None, None, None, NONE, None),
    # Tuning: a weak inbox word (approve, reject, lulus, tolak) yields to an hr task word.
    ("weak-approve-task", "Please approve Aina's laptop task", None, None, None, NONE, _set("hr")),
    ("weak-reject-step", "reject the onboarding step", None, None, None, NONE, _set("hr")),
    ("weak-lulus-tugasan", "lulus tugasan Aina", None, None, None, NONE, _set("hr")),
    (
        "weak-approve-held",
        "approve the held reply to Razak",
        None,
        None,
        None,
        NONE,
        _set("inbox", "email"),
    ),
    ("weak-approve-alone", "approve", None, None, None, NONE, _set("inbox")),
    ("weak-tolak-ditahan", "tolak jawapan ditahan", None, None, None, NONE, _set("inbox")),
    ("weak-approve-reply", "approve Razak's reply", None, None, None, NONE, _set("inbox", "email")),
    (
        "weak-approve-task-but-held",
        "approve the held reply task",
        None,
        None,
        None,
        NONE,
        _set("inbox", "hr"),
    ),
    # Tuning: who-is words count only when no other word rule matched.
    (
        "who-weak-siapa-onboarding",
        "Siapa dalam onboarding sekarang?",
        None,
        None,
        None,
        NONE,
        _set("hr"),
    ),
    ("who-weak-onboarding", "who is onboarding", None, None, None, NONE, _set("hr")),
    # Tuning: the products prompt never travels alone (products alone 7-8/10 on product_stock-en-1,
    # 10/10 with any second area); a set that already has a second area is untouched.
    (
        "alone-stock-en-1",
        "How many of these do we have left?",
        "business",
        "product",
        None,
        NONE,
        _set("inbox", "products"),
    ),
    (
        "alone-stock-ms-1",
        "Berapa banyak lagi stok yang kita ada untuk produk ini?",
        "business",
        "product",
        None,
        NONE,
        _set("inbox", "products"),
    ),
    (
        "alone-kettle",
        "what is the stock for the blue kettle",
        None,
        None,
        None,
        NONE,
        _set("inbox", "products"),
    ),
    ("alone-tab-business", "hello", "business", None, None, NONE, _set("inbox", "products")),
    (
        "alone-cancel-tiktok-order",
        "cancel this TikTok order",
        None,
        None,
        None,
        NONE,
        _set("products", "bookings"),
    ),
    ("alone-hr-unchanged", "what are my tasks", None, None, None, NONE, _set("hr")),
    ("alone-bookings-unchanged", "tempah bilik esok", None, None, None, NONE, _set("bookings")),
    # Needs.
    ("needs-mail", "hello", None, None, None, _needs(mail=True), _set("email")),
    ("needs-team", "hello", None, None, None, _needs(team=True), _set("email")),
    ("needs-training", "hello", None, None, None, _needs(training=True), _set("training")),
    ("needs-people", "hello", None, None, None, _needs(people=True), _set("hr")),
    ("needs-manager", "hello", None, None, None, _needs(manager=True), _set("email", "hr")),
    # The open record's kind alone.
    ("record-conversation", "hello", None, "conversation", None, NONE, _set("inbox")),
    ("record-approval", "hello", None, "approval", None, NONE, _set("inbox")),
    ("record-order", "hello", None, "order", None, NONE, _set("inbox", "products")),
    ("record-product", "hello", None, "product", None, NONE, _set("inbox", "products")),
    ("record-employee", "hello", None, "employee", None, NONE, _set("hr")),
    ("record-training-request", "hello", None, "training_request", None, NONE, _set("training")),
    ("record-training-item", "hello", None, "training_item", None, NONE, _set("training")),
    ("record-draft", "hello", None, "draft", None, NONE, _set("email")),
    # The previous turn: its action keeps its area, general adds nothing.
    (
        "prev-action-email",
        "the rest",
        None,
        None,
        _prior("send the same email to the subscribers", "email_batch"),
        NONE,
        _set("email"),
    ),
    (
        "prev-action-booking",
        "yes",
        None,
        None,
        _prior("hello", "booking_action"),
        NONE,
        _set("bookings"),
    ),
    ("prev-action-hr", "next", None, None, _prior("hello", "hr_task_complete"), NONE, _set("hr")),
    ("prev-action-general", "ok", None, None, _prior("hello", "answer"), NONE, None),
    (
        "prev-clarification-keeps-earlier-words",
        "the second one",
        None,
        None,
        _prior("book a room for Friday", "clarification"),
        NONE,
        _set("bookings"),
    ),
    (
        "prev-clarification-hr-words",
        "Aina",
        None,
        None,
        _prior("mark my task done", "clarification"),
        NONE,
        _set("hr"),
    ),
    # A colleague pick keeps email.
    (
        "colleague-pick",
        "Use employee 7d9f2a10-0000-4000-8000-000000000001 for: email danial about Friday",
        None,
        None,
        None,
        NONE,
        _set("email"),
    ),
    (
        "colleague-pick-bare",
        "Use employee 7d9f2a10-0000-4000-8000-000000000001 for: lunch",
        None,
        None,
        None,
        NONE,
        _set("email"),
    ),
    # Cross-area.
    (
        "cross-email-leave",
        "email Aina about her leave",
        None,
        None,
        None,
        NONE,
        _set("email", "hr"),
    ),
    # The always-present bookings fact is not a parameter: it cannot select bookings.
    ("bookings-fact-not-a-signal", "hello", "overview", None, None, NONE, None),
    ("bookings-fact-with-hr-tab", "hello", "hr", None, None, NONE, _set("hr")),
]


@pytest.mark.parametrize(
    ("message", "tab", "record_kind", "previous", "needs", "expected"),
    [row[1:] for row in ROWS],
    ids=[row[0] for row in ROWS],
)
def test_select_areas(message, tab, record_kind, previous, needs, expected):
    chosen = select_areas(message, tab=tab, record_kind=record_kind, previous=previous, needs=needs)
    if expected is None:
        assert chosen is None
        return
    assert chosen is not None
    assert expected <= chosen, f"missing {expected - chosen}"


# Rows whose exact set matters (no stray area): the pure-signal rows.
EXACT = {
    "tab-hr",
    "tab-email",
    "tab-inbox",
    "tab-bookings",
    "tab-training",
    "tab-business",
    "needs-mail",
    "needs-team",
    "needs-training",
    "needs-people",
    "needs-manager",
    "record-conversation",
    "record-approval",
    "record-order",
    "record-product",
    "record-employee",
    "record-training-request",
    "record-training-item",
    "record-draft",
    "prev-action-email",
    "prev-action-booking",
    "prev-action-hr",
    "colleague-pick-bare",
    "lookup-who-is",
    "lookup-siapa",
    "alone-stock-en-1",
    "alone-stock-ms-1",
    "alone-kettle",
    "alone-tab-business",
    "alone-cancel-tiktok-order",
    "alone-hr-unchanged",
    "alone-bookings-unchanged",
    "weak-approve-task",
    "weak-reject-step",
    "weak-lulus-tugasan",
    "weak-approve-alone",
    "who-weak-siapa-onboarding",
    "who-weak-onboarding",
    "who-joining",
    "siapa-masuk",
}


@pytest.mark.parametrize("row", [r for r in ROWS if r[0] in EXACT], ids=lambda r: r[0])
def test_pure_signals_select_exactly_their_area(row):
    _, message, tab, record_kind, previous, needs, expected = row
    chosen = select_areas(message, tab=tab, record_kind=record_kind, previous=previous, needs=needs)
    assert chosen == expected


def test_word_rules_are_case_insensitive_with_word_boundaries():
    assert word_rule_areas("BOOK a room") == _set("bookings")
    assert word_rule_areas("a notebook") == frozenset()
    assert word_rule_areas("the stockroom") == frozenset()
    assert word_rule_areas("hello there") == frozenset()


def test_word_rules_reuse_the_service_words():
    assert "email" in word_rule_areas("my reports")  # team
    assert "training" in word_rule_areas("a workshop")
    assert word_rule_areas("who is my boss") >= _set("email", "hr")  # manager
    assert "hr" in word_rule_areas("hire someone")  # people


def test_routed_areas_join_the_union() -> None:
    kw = {"tab": None, "record_kind": None, "previous": None, "needs": _needs()}
    assert select_areas("hello", **kw) is None
    assert select_areas("hello", routed=frozenset({"hr"}), **kw) == {"hr"}
    assert select_areas("book a room", routed=frozenset({"hr"}), **kw) == {"bookings", "hr"}


def test_a_routed_products_alone_gets_the_same_companion() -> None:
    kw = {"tab": None, "record_kind": None, "previous": None, "needs": _needs()}
    assert select_areas("hello", routed=frozenset({"products"}), **kw) == {"products", "inbox"}
