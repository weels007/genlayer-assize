"""The two instances: the ruling, the appeal, and the money that follows.

The first instance reads the page. The second one either reads the record only —
no web at all — or reads the page again. These tests pin both, and pin that the
loser is the only party who may appeal.
"""

import json

import pytest

CLAIM = "The project publishes a signed release."
URL = "https://source.example.com/page"
PAGE = (
    "The project publishes a signed release every month. The release notes list "
    "every change and the repository is public."
)
QUOTE = "The project publishes a signed release every month."

BOND = 200
APPEAL_STAKE = 2 * BOND
WINDOW = 3600


def _deployed(direct_deploy):
    return direct_deploy("contracts/Assize.py")


def _mock_page(direct_vm):
    direct_vm.mock_web(r".*source\.example\.com.*", {"status": 200, "body": PAGE})


def _mock_ruling(direct_vm, ruling="SUSTAINED", quote=QUOTE):
    direct_vm.mock_llm(
        r".*\[\[ASSIZE_RULING\]\].*",
        json.dumps({"ruling": ruling, "quote": quote, "reason": "the page says so"}),
    )


def _mock_appeal(direct_vm, decision="UPHELD", quote=QUOTE):
    direct_vm.mock_llm(
        r".*\[\[ASSIZE_APPEAL\]\].*",
        json.dumps({"decision": decision, "quote": quote, "reason": "on the record"}),
    )


def _contested(direct_vm, contract, filer, contestant, ref="ref-1"):
    direct_vm.sender = filer
    direct_vm.value = BOND
    case_id = contract.file_case(CLAIM, URL, WINDOW, ref)
    direct_vm.value = 0

    direct_vm.sender = contestant
    direct_vm.value = BOND
    contract.contest(case_id)
    direct_vm.value = 0

    return case_id


def _ruled(direct_vm, direct_deploy, filer, contestant, ruling="SUSTAINED", quote=QUOTE):
    contract = _deployed(direct_deploy)
    case_id = _contested(direct_vm, contract, filer, contestant)

    _mock_page(direct_vm)
    _mock_ruling(direct_vm, ruling, quote)
    contract.rule(case_id)
    return contract, case_id


def _appealed(
    direct_vm,
    direct_deploy,
    filer,
    contestant,
    ruling="SUSTAINED",
    ground="RECORD",
    decision="UPHELD",
):
    contract, case_id = _ruled(direct_vm, direct_deploy, filer, contestant, ruling)

    loser = contestant if ruling == "SUSTAINED" else filer
    direct_vm.sender = loser
    direct_vm.value = APPEAL_STAKE
    contract.appeal(case_id, ground)
    direct_vm.value = 0

    direct_vm.clear_mocks()
    _mock_appeal(direct_vm, decision)
    contract.judge_appeal(case_id)

    return contract, case_id, loser


def test_the_first_instance_must_wait_for_a_contest(
    direct_vm, direct_deploy, direct_alice
):
    contract = _deployed(direct_deploy)
    direct_vm.sender = direct_alice
    direct_vm.value = BOND
    case_id = contract.file_case(CLAIM, URL, WINDOW, "ref-1")
    direct_vm.value = 0

    with direct_vm.expect_revert("[EXPECTED]:case_not_ready"):
        contract.rule(case_id)


def test_a_sustained_ruling_keeps_the_record_it_read(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract, case_id = _ruled(direct_vm, direct_deploy, direct_alice, direct_bob)

    record = contract.get_case(case_id)
    assert record["status"] == "RULED"
    assert record["ruling"] == "SUSTAINED"
    assert record["quote"].lower() == QUOTE.lower()
    assert record["record_digest"] != ""
    assert record["record_chars"] == len(PAGE)
    assert record["attempts"] == 1
    assert record["appeal_deadline"] > record["ruled_ts"]
    assert contract.get_summary()["sustained"] == 1

    stored = contract.get_record(case_id)
    assert stored["record"] == PAGE, (
        "the record is the whole text the first instance read: a RECORD appeal is "
        "handed that text, so it cannot be a citation the leader picked out"
    )
    assert stored["record_digest"] == record["record_digest"]


def test_a_rejected_ruling_is_recorded(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id = _ruled(
        direct_vm, direct_deploy, direct_alice, direct_bob, ruling="REJECTED"
    )

    assert contract.get_case(case_id)["ruling"] == "REJECTED"
    assert contract.get_summary()["rejected"] == 1


def test_an_ungrounded_quote_is_not_a_ruling(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id = _ruled(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        quote="a sentence that is not on the page at all",
    )

    record = contract.get_case(case_id)
    assert record["status"] == "CONTESTED"
    assert record["ruling"] == ""
    assert record["attempts"] == 1
    assert contract.get_ledger()["total_out"] == 0


def test_an_empty_page_is_an_external_error(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract = _deployed(direct_deploy)
    case_id = _contested(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.mock_web(r".*source\.example\.com.*", {"status": 200, "body": ""})
    _mock_ruling(direct_vm)

    with direct_vm.expect_revert("[EXTERNAL]:empty_source_page"):
        contract.rule(case_id)

    assert contract.get_case(case_id)["attempts"] == 0


def test_an_undecidable_case_is_voided_with_both_stakes_back(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    case_id = _contested(direct_vm, contract, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_ruling(direct_vm, ruling="SILENT")

    for _ in range(3):
        assert contract.rule(case_id) == ""

    assert contract.get_case(case_id)["attempts"] == 3
    with direct_vm.expect_revert("[EXPECTED]:too_many_attempts"):
        contract.rule(case_id)

    direct_vm.sender = direct_owner
    contract.void(case_id)

    record = contract.get_case(case_id)
    assert record["status"] == "VOID"
    assert contract.get_summary()["void"] == 1

    ledger = contract.get_ledger()
    assert ledger["total_out"] == 2 * BOND
    assert ledger["locked_total"] == 0
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]


def test_void_is_refused_before_the_attempts_are_spent(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    case_id = _contested(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("[EXPECTED]:not_voidable"):
        contract.void(case_id)


def test_only_the_loser_may_appeal(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id = _ruled(direct_vm, direct_deploy, direct_alice, direct_bob)

    # Sustained: the filer won, so the filer is not the loser.
    direct_vm.sender = direct_alice
    direct_vm.value = APPEAL_STAKE
    contract.appeal(case_id, "RECORD")
    direct_vm.value = 0

    assert contract.get_credit(direct_alice) == APPEAL_STAKE
    assert contract.get_case(case_id)["status"] == "RULED"


def test_an_appeal_needs_the_right_stake_and_ground(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id = _ruled(direct_vm, direct_deploy, direct_alice, direct_bob)

    direct_vm.sender = direct_bob
    direct_vm.value = BOND
    contract.appeal(case_id, "RECORD")
    direct_vm.value = 0
    assert contract.get_credit(direct_bob) == BOND

    direct_vm.sender = direct_bob
    direct_vm.value = APPEAL_STAKE
    contract.appeal(case_id, "VIBES")
    direct_vm.value = 0
    assert contract.get_credit(direct_bob) == BOND + APPEAL_STAKE

    assert contract.get_case(case_id)["status"] == "RULED"


def test_an_appeal_after_the_window_is_a_credit(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract, case_id = _ruled(direct_vm, direct_deploy, direct_alice, direct_bob)

    direct_vm.warp("2025-01-01T02:00:00Z")

    direct_vm.sender = direct_bob
    direct_vm.value = APPEAL_STAKE
    contract.appeal(case_id, "RECORD")
    direct_vm.value = 0

    assert contract.get_credit(direct_bob) == APPEAL_STAKE
    assert contract.can_appeal(case_id) is False


def test_a_record_appeal_never_touches_the_web(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)
    case_id = _contested(direct_vm, contract, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_ruling(direct_vm, "SUSTAINED")
    contract.rule(case_id)

    direct_vm.sender = direct_bob
    direct_vm.value = APPEAL_STAKE
    contract.appeal(case_id, "RECORD")
    direct_vm.value = 0

    # No page mock at all: the record appeal must not need one. If the contract
    # fetched anything, this would fail for the lack of a handler.
    direct_vm.clear_mocks()
    _mock_appeal(direct_vm, "UPHELD")

    assert contract.judge_appeal(case_id) == "UPHELD"
    assert contract.get_case(case_id)["status"] == "APPEAL_RULED"


def test_a_record_appeal_is_handed_the_whole_record(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """The second instance sees the record, not the sentence the first one liked.

    The first instance cites the page's first sentence. The mock below answers only
    a prompt that also carries the page's second one, which exists nowhere except
    in the record itself — so the appeal is heard only because it is handed the
    record rather than the citation.
    """
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract, case_id = _ruled(direct_vm, direct_deploy, direct_alice, direct_bob)

    direct_vm.sender = direct_bob
    direct_vm.value = APPEAL_STAKE
    contract.appeal(case_id, "RECORD")
    direct_vm.value = 0

    direct_vm.clear_mocks()
    direct_vm.mock_llm(
        r"(?s).*\[\[ASSIZE_APPEAL\]\].*release notes list every change.*",
        json.dumps({"decision": "UPHELD", "reason": "the record supports it"}),
    )

    assert contract.judge_appeal(case_id) == "UPHELD"
    assert contract.get_case(case_id)["status"] == "APPEAL_RULED"


def _ready_to_hear(
    direct_vm, direct_deploy, filer, contestant, ground="RECORD", ruling="SUSTAINED"
):
    """Rule a case, appeal it, and leave the second instance waiting."""
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract, case_id = _ruled(
        direct_vm, direct_deploy, filer, contestant, ruling=ruling
    )

    loser = contestant if ruling == "SUSTAINED" else filer
    direct_vm.sender = loser
    direct_vm.value = APPEAL_STAKE
    contract.appeal(case_id, ground)
    direct_vm.value = 0

    direct_vm.clear_mocks()
    return contract, case_id


def test_the_appeal_understands_the_rulings_own_words(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """A model that answers in the first instance's vocabulary is understood.

    The second instance is asked about a ruling the first one entered, and models
    answer with that instance's word: a ruling that stands is UPHELD, and one that
    does not is OVERTURNED. Neither answer is an error.
    """
    contract, case_id = _ready_to_hear(
        direct_vm, direct_deploy, direct_alice, direct_bob
    )

    _mock_appeal(direct_vm, "SUSTAINED")
    assert contract.judge_appeal(case_id) == "UPHELD"
    assert contract.get_case(case_id)["status"] == "APPEAL_RULED"


def test_the_appeal_reads_a_prose_answer(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id = _ready_to_hear(
        direct_vm, direct_deploy, direct_alice, direct_bob
    )

    _mock_appeal(direct_vm, "the record supports the ruling")
    assert contract.judge_appeal(case_id) == "UPHELD"


def test_a_negated_or_ambiguous_answer_is_undecided(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """A negated answer guesses nothing: the case books an attempt instead.

    A guess here would pay one side on the strength of a misread word, so the
    contract refuses to decide and the attempt counter moves.
    """
    contract, case_id = _ready_to_hear(
        direct_vm, direct_deploy, direct_alice, direct_bob
    )

    _mock_appeal(direct_vm, "not upheld")
    assert contract.judge_appeal(case_id) == ""

    record = contract.get_case(case_id)
    assert record["status"] == "APPEALED"
    assert record["decision"] == ""
    assert record["attempts"] == 1


def test_the_ruling_understands_the_appeals_words(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id = _ruled(
        direct_vm, direct_deploy, direct_alice, direct_bob, ruling="upheld"
    )

    record = contract.get_case(case_id)
    assert record["ruling"] == "SUSTAINED"
    assert record["status"] == "RULED"


def test_an_evidence_appeal_does_read_the_page(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)
    case_id = _contested(direct_vm, contract, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_ruling(direct_vm, "SUSTAINED")
    contract.rule(case_id)

    direct_vm.sender = direct_bob
    direct_vm.value = APPEAL_STAKE
    contract.appeal(case_id, "EVIDENCE")
    direct_vm.value = 0

    # Only the appeal prompt is mocked: without a page the evidence appeal cannot
    # run, which is exactly what distinguishes it from the record appeal.
    direct_vm.clear_mocks()
    _mock_appeal(direct_vm, "OVERTURNED")

    with direct_vm.expect_revert("[TRANSIENT]:page_unreachable"):
        contract.judge_appeal(case_id)


def test_an_overturned_appeal_pays_the_appellant(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id, loser = _appealed(
        direct_vm, direct_deploy, direct_alice, direct_bob, decision="OVERTURNED"
    )

    record = contract.get_case(case_id)
    assert record["status"] == "APPEAL_RULED"
    assert record["decision"] == "OVERTURNED"
    assert contract.get_summary()["overturned"] == 1

    direct_vm.sender = direct_alice
    contract.settle(case_id)

    assert contract.get_case(case_id)["status"] == "SETTLED"
    ledger = contract.get_ledger()
    assert ledger["total_out"] == BOND + BOND + APPEAL_STAKE
    assert ledger["locked_total"] == 0
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]


def test_an_upheld_appeal_pays_the_first_winner(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id, loser = _appealed(
        direct_vm, direct_deploy, direct_alice, direct_bob, decision="UPHELD"
    )

    direct_vm.sender = direct_alice
    contract.settle(case_id)

    assert contract.get_case(case_id)["status"] == "SETTLED"
    assert contract.get_case(case_id)["decision"] == "UPHELD"
    assert contract.get_ledger()["locked_total"] == 0


def test_settle_waits_for_the_appeal_window(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract, case_id = _ruled(direct_vm, direct_deploy, direct_alice, direct_bob)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED]:case_not_closed"):
        contract.settle(case_id)

    direct_vm.warp("2025-01-01T02:00:00Z")
    contract.settle(case_id)

    record = contract.get_case(case_id)
    assert record["status"] == "SETTLED"
    assert contract.get_summary()["settled"] == 1

    ledger = contract.get_ledger()
    assert ledger["total_out"] == 2 * BOND
    assert ledger["locked_total"] == 0


def test_a_rejected_ruling_makes_the_filer_the_loser(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id = _ruled(
        direct_vm, direct_deploy, direct_alice, direct_bob, ruling="REJECTED"
    )

    # Rejected: the contestant won, so the filer may appeal.
    direct_vm.sender = direct_alice
    direct_vm.value = APPEAL_STAKE
    contract.appeal(case_id, "RECORD")
    direct_vm.value = 0

    assert contract.get_credit(direct_alice) == 0
    assert contract.get_case(case_id)["status"] == "APPEALED"


def test_an_undecided_appeal_can_be_voided(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)
    case_id = _contested(direct_vm, contract, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_ruling(direct_vm, "SUSTAINED")
    contract.rule(case_id)

    direct_vm.sender = direct_bob
    direct_vm.value = APPEAL_STAKE
    contract.appeal(case_id, "RECORD")
    direct_vm.value = 0

    direct_vm.clear_mocks()
    direct_vm.mock_llm(
        r".*\[\[ASSIZE_APPEAL\]\].*",
        json.dumps({"decision": "SILENT", "reason": "the record is thin"}),
    )

    for _ in range(3):
        assert contract.judge_appeal(case_id) == ""

    with direct_vm.expect_revert("[EXPECTED]:too_many_attempts"):
        contract.judge_appeal(case_id)

    direct_vm.sender = direct_owner
    contract.void(case_id)

    assert contract.get_case(case_id)["status"] == "VOID"

    ledger = contract.get_ledger()
    assert ledger["total_out"] == BOND + BOND + APPEAL_STAKE
    assert ledger["locked_total"] == 0
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]


def test_the_ledger_stays_balanced_through_a_full_appeal(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id, loser = _appealed(
        direct_vm, direct_deploy, direct_alice, direct_bob, decision="UPHELD"
    )

    ledger = contract.get_ledger()
    assert ledger["total_in"] == BOND + BOND + APPEAL_STAKE
    assert ledger["locked_total"] == BOND + BOND + APPEAL_STAKE
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]
