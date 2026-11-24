"""Assize on studionet: a filing, a contest, a ruling, and an appeal.

The court has no owner and no reset, so every assertion is a delta — a count, a
ledger total compared before and after — and the suite tolerates cases and
credits left behind by an earlier run.

The two sides are played by two different wallets, because the contract refuses a
filer's own contest. Every claim reference is suffixed with the run's start time,
because a reference is caller-scoped and permanent.

A live committee cannot be ordered to rule, so each case asserts the law for
whichever ruling appears and reports it: a ruling is settled after its appeal
window, an appeal is settled when the second instance answers, and a case nobody
could decide is voided with both stakes back.

Both grounds are exercised against the deployed court: one appeal is argued on the
record alone and one on the page, so the second instance runs both ways. A case
filed against a page no quote can be grounded in is ruled three times without ever
being decided, then voided by the other account, so the void path is live as well.

    pytest tests/integration -v -s
"""

import json
import re
import sys
import time
from pathlib import Path

import pytest
from genlayer_py import create_client, studionet

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from wallet import load_account  # noqa: E402

DEPLOYMENT_PATH = ROOT / "deployment.json"
ADDRESS_VALUE = re.compile(r"0x[0-9a-fA-F]{40}")

POLL_INTERVAL = 10_000
POLL_RETRIES = 60
RETRY_PAUSE = 20

BOND = 200
APPEAL_STAKE = 2 * BOND
WINDOW = 60
# The ruling's timestamp is the rule transaction's own clock, and Studio can take
# longer than the minimum window to execute the appeal that follows it. The case
# that is meant to be appealed therefore gets an hour; the settle test keeps the
# minimum, because that one needs the window to close.
APPEAL_WINDOW = 3600
SOURCE_URL = "https://raw.githubusercontent.com/weels007/genlayer-vow/main/README.md"
# A page whose whole text folds to nineteen letters, one short of MIN_QUOTE_CHARS:
# no quote taken from it can ground, so a case filed against it can never be ruled.
UNGROUNDABLE_URL = (
    "https://raw.githubusercontent.com/weels007/genlayer-assize/main/"
    "testdata/ungroundable.txt"
)
CLAIM_TEXT = (
    "The project is an Intelligent Contract for GenLayer that reads evidence from "
    "the web and settles a promise with a quote."
)

RUN = str(time.time_ns())


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _deployment() -> dict:
    if not DEPLOYMENT_PATH.exists():
        pytest.skip("no deployment.json yet; run the Deploy Contract workflow first")
    payload = json.loads(DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    address = str(payload.get("address", "")).strip()
    if not ADDRESS_VALUE.fullmatch(address):
        pytest.skip(f"deployment.json has no usable address: {address!r}")
    return payload


@pytest.fixture(scope="module")
def deployment() -> dict:
    return _deployment()


@pytest.fixture(scope="module")
def contract(deployment) -> str:
    print(f"\n[assize] contract {deployment['address']}")
    return deployment["address"]


@pytest.fixture(scope="module")
def account():
    return load_account("tester1")


@pytest.fixture(scope="module")
def opponent(account):
    """A second funded wallet. Without one there is nothing to contest with."""
    try:
        other = load_account("tester2")
    except SystemExit:
        pytest.skip("no second wallet is configured; a contest would be a self-contest")
    if _hex(other.address) == _hex(account.address):
        pytest.skip("the second wallet is the deployer; a contest would be a self-contest")
    return other


@pytest.fixture(scope="module")
def client(account):
    return create_client(studionet, account=account)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _hex(value) -> str:
    return str(value).lower().replace("0x", "")


def _succeeded(result) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("status_name") not in ("ACCEPTED", "FINALIZED"):
        return False
    leader = (result.get("consensus_data") or {}).get("leader_receipt")
    if isinstance(leader, list):
        leader = leader[0] if leader else None
    if isinstance(leader, dict):
        return leader.get("execution_result") == "SUCCESS"
    return str(result.get("tx_execution_result_name", "")) == "FINISHED_WITH_RETURN"


def _definite_rollback(result) -> bool:
    if not isinstance(result, dict):
        return False
    leader = (result.get("consensus_data") or {}).get("leader_receipt")
    if isinstance(leader, list):
        leader = leader[0] if leader else None
    return isinstance(leader, dict) and leader.get("execution_result") == "ERROR"


# Studio has been asking more than the fee its own client quotes: a send carrying
# only the quote comes back reverted at the EVM step, before any contract code
# runs, and the same send with a larger deposit goes through. The deposit is the
# one number this side can get wrong, so a reverted send is tried again with more.
SEND_ATTEMPTS = 3


def _fees(client, factor: int):
    """The quoted fee distribution with the deposit multiplied, or None."""
    try:
        estimate = client.estimate_transaction_fees()
    except Exception:
        return None
    distribution = estimate.get("distribution")
    fee_value = estimate.get("feeValue")
    if not distribution or not fee_value:
        return None
    return {"distribution": distribution, "feeValue": int(fee_value) * factor}


def _attempt(client, account, contract, method, args=None, value=0):
    last = None
    for attempt in range(SEND_ATTEMPTS):
        fees = None if attempt == 0 else _fees(client, 2**attempt)
        try:
            tx_hash = client.write_contract(
                contract,
                method,
                args=args or [],
                value=value,
                account=account,
                fees=fees,
            )
            return client.wait_for_transaction_receipt(
                tx_hash, retries=POLL_RETRIES, interval=POLL_INTERVAL
            )
        except Exception as error:
            last = error
            if "reverted" not in str(error).lower() or attempt == SEND_ATTEMPTS - 1:
                return error
            print(
                f"  {method}: the network reverted the send; trying again with a "
                f"deposit of {2 ** (attempt + 1)}x the quoted fee"
            )
            time.sleep(RETRY_PAUSE)
    return last


def _expect_ok(client, account, contract, method, args=None, value=0, verify=None):
    result = None
    for attempt in range(3):
        result = _attempt(client, account, contract, method, args, value)
        if _succeeded(result):
            return result
        if verify is not None and verify():
            return result
        if _definite_rollback(result):
            raise AssertionError(f"{method} was refused: {result}")
        if attempt < 2:
            print(f"  {method} did not reach consensus, retrying")
            time.sleep(RETRY_PAUSE)
    raise AssertionError(f"{method} did not reach consensus: {result}")


def _read(client, contract, method, args=None):
    """Read a view, retrying a provider hiccup (a 502 is not an answer)."""
    last = None
    for attempt in range(3):
        try:
            return client.read_contract(contract, method, args=args or [])
        except Exception as error:
            last = error
            if attempt < 2:
                print(f"  {method} read failed, retrying: {error}")
                time.sleep(RETRY_PAUSE)
    raise last


def _case(client, contract, case_id):
    return _read(client, contract, "get_case", [case_id])


def _case_ids(client, contract):
    return list(_read(client, contract, "get_case_ids").get("ids", []))


def _ledger(client, contract):
    return _read(client, contract, "get_ledger")


def _balance(client, contract):
    return int(_read(client, contract, "get_balance"))


def _credit(client, contract, who):
    return int(_read(client, contract, "get_credit", [who.address]))


def _invariant_holds(client, contract) -> bool:
    ledger = _ledger(client, contract)
    return int(ledger["total_in"]) == int(ledger["total_out"]) + int(ledger["locked_total"])


def _file(client, account, contract, ref, claim=CLAIM_TEXT, url=SOURCE_URL, window=WINDOW):
    before = len(_case_ids(client, contract))
    _expect_ok(
        client,
        account,
        contract,
        "file_case",
        [claim, url, window, f"{ref}-{RUN}"],
        value=BOND,
        verify=lambda: len(_case_ids(client, contract)) > before,
    )
    ids = _case_ids(client, contract)
    assert len(ids) > before, "the case was not filed"
    return ids[-1]


def _contest(client, opponent, contract, case_id):
    _expect_ok(client, opponent, contract, "contest", [case_id], value=BOND)
    record = _case(client, contract, case_id)
    assert record["status"] == "CONTESTED", (
        "the contest did not open a case — a refused payable call answers 'credit' "
        f"instead of reverting: {record}"
    )
    assert _read(client, contract, "is_contested", [case_id]), (
        "a contested case must say so to anyone who asks"
    )
    return record


def _rule(client, account, contract, case_id):
    """Rule until the committee answers, or the attempts are spent."""
    for _ in range(4):
        record = _case(client, contract, case_id)
        if record["status"] != "CONTESTED" or int(record["attempts"]) >= 3:
            return record
        _expect_ok(client, account, contract, "rule", [case_id])
    return _case(client, contract, case_id)


def _winner_cannot_appeal(client, winner, contract, case_id, ground):
    """The side that won cannot buy a second instance, even inside the window."""
    assert _read(client, contract, "can_appeal", [case_id]), (
        "the appeal window has to be open for this to prove anything"
    )

    credit_before = _credit(client, contract, winner)
    _expect_ok(
        client, winner, contract, "appeal", [case_id, ground], value=APPEAL_STAKE
    )

    assert _case(client, contract, case_id)["status"] == "RULED", (
        f"the winner's appeal was accepted: {_case(client, contract, case_id)}"
    )
    assert _credit(client, contract, winner) == credit_before + APPEAL_STAKE, (
        "the case is ruled, the window is open, the ground and the stake are right, "
        "so the only refusal left is not_the_loser — and a refused appeal must "
        "leave the stake as a credit"
    )

    _expect_ok(
        client,
        winner,
        contract,
        "withdraw_credit",
        verify=lambda: _credit(client, contract, winner) == credit_before,
    )


def _appeal(client, loser, contract, case_id, ground="RECORD"):
    credit_before = _credit(client, contract, loser)
    _expect_ok(
        client, loser, contract, "appeal", [case_id, ground], value=APPEAL_STAKE
    )
    record = _case(client, contract, case_id)
    if record["status"] != "APPEALED":
        refunded = _credit(client, contract, loser) - credit_before
        raise AssertionError(
            "the appeal was refused — a payable refusal answers 'credit': "
            f"status {record['status']!r}, ruled at {record['ruled_ts']}, "
            f"appeal closed at {record['appeal_deadline']}, "
            f"the runner's clock {int(time.time())}, credit +{refunded}: {record}"
        )
    return record


def _judge_appeal(client, account, contract, case_id):
    for _ in range(4):
        record = _case(client, contract, case_id)
        if record["status"] != "APPEALED" or int(record["attempts"]) >= 3:
            return record
        _expect_ok(client, account, contract, "judge_appeal", [case_id])
    return _case(client, contract, case_id)


def _void_open_case(client, account, contract, case_id):
    """Give up on a case the committee could not decide: three attempts, then void."""
    record = _case(client, contract, case_id)
    for _ in range(4):
        if record["status"] not in ("CONTESTED", "APPEALED"):
            return record
        if int(record["attempts"]) >= 3:
            _expect_ok(client, account, contract, "void", [case_id])
            return _case(client, contract, case_id)
        if record["status"] == "CONTESTED":
            _expect_ok(client, account, contract, "rule", [case_id])
        else:
            _expect_ok(client, account, contract, "judge_appeal", [case_id])
        record = _case(client, contract, case_id)
    return record


def _settle_and_check(client, account, contract, case_id, pot):
    ledger_before = _ledger(client, contract)
    _expect_ok(
        client,
        account,
        contract,
        "settle",
        [case_id],
        verify=lambda: _case(client, contract, case_id)["status"] == "SETTLED",
    )

    assert _case(client, contract, case_id)["status"] == "SETTLED"
    ledger = _ledger(client, contract)
    assert int(ledger["total_out"]) == int(ledger_before["total_out"]) + pot
    assert _invariant_holds(client, contract)


def _appealed_case(client, account, opponent, contract, ref, ground):
    """Rule a contested case, appeal it on `ground`, hear it, and settle it."""
    case_id = _file(client, account, contract, ref, window=APPEAL_WINDOW)
    _contest(client, opponent, contract, case_id)

    record = _rule(client, account, contract, case_id)
    if record["status"] != "RULED":
        record = _void_open_case(client, account, contract, case_id)
        assert record["status"] == "VOID", record
        assert _invariant_holds(client, contract)
        pytest.skip(f"the committee could not rule the {ground} case; it was voided")

    stored = _read(client, contract, "get_record", [case_id])
    assert stored["record"], (
        f"the {ground} case was ruled but wrote no record: the appeal would have "
        "no agreed evidence to argue over"
    )
    assert stored["record_digest"] == record["record_digest"]

    loser = opponent if record["ruling"] == "SUSTAINED" else account
    winner = account if loser is opponent else opponent

    # The winner is refused, and the refusal leaves the stake as their credit.
    _winner_cannot_appeal(client, winner, contract, case_id, ground)

    _appeal(client, loser, contract, case_id, ground=ground)

    heard = _judge_appeal(client, account, contract, case_id)
    if heard["status"] != "APPEAL_RULED":
        heard = _void_open_case(client, account, contract, case_id)
        assert heard["status"] == "VOID", heard
        assert _invariant_holds(client, contract)
        pytest.skip(
            f"the second instance could not decide the {ground} appeal; it was voided"
        )

    print(
        f"  {ground}: the first instance ruled {record['ruling']!r} on a "
        f"{stored['record_chars']}-character record, the appeal answered "
        f"{heard['decision']!r}"
    )

    _settle_and_check(client, account, contract, case_id, 2 * BOND + APPEAL_STAKE)


# ---------------------------------------------------------------------------
# the court is live
# ---------------------------------------------------------------------------


def test_01_the_court_is_live(client, contract):
    rules = _read(client, contract, "get_rules")
    assert int(rules["min_bond"]) == BOND
    assert int(rules["max_attempts"]) == 3
    assert set(rules["grounds"]) == {"RECORD", "EVIDENCE"}
    assert _invariant_holds(client, contract)


def test_02_a_case_is_ruled_and_settled(client, account, opponent, contract):
    case_id = _file(client, account, contract, "live-1")
    _contest(client, opponent, contract, case_id)

    record = _rule(client, account, contract, case_id)
    if record["status"] != "RULED":
        record = _void_open_case(client, account, contract, case_id)
        assert record["status"] == "VOID", record
        assert _invariant_holds(client, contract)
        pytest.skip("the committee could not rule this case; it was voided and refunded")

    print(f"  the first instance ruled {record['ruling']!r}")
    assert record["quote"], "a ruling must keep the quote that decided it"

    # The record is the evidence a RECORD appeal is handed, and its digest is part
    # of the consensus that wrote the ruling.
    stored = _read(client, contract, "get_record", [case_id])
    assert stored["record"], (
        "a ruling has to write down the whole record it read: without it a RECORD "
        "appeal would have nothing but a sentence of the leader's choosing"
    )
    assert int(stored["record_chars"]) == len(stored["record"])
    assert stored["record_digest"] == record["record_digest"]

    # A ruling nobody appeals is settled once its appeal window has passed.
    time.sleep(WINDOW + 5)

    _settle_and_check(client, account, contract, case_id, 2 * BOND)


def test_03_a_record_appeal_is_heard_and_settled(client, account, opponent, contract):
    _appealed_case(client, account, opponent, contract, "live-record", "RECORD")


def test_04_an_evidence_appeal_is_heard_and_settled(
    client, account, opponent, contract
):
    _appealed_case(client, account, opponent, contract, "live-evidence", "EVIDENCE")


def test_05_an_undecidable_case_is_voided(client, account, opponent, contract):
    """A page no quote can be grounded in: three attempts, then both stakes back."""
    case_id = _file(
        client,
        account,
        contract,
        "live-void",
        url=UNGROUNDABLE_URL,
        window=APPEAL_WINDOW,
    )
    _contest(client, opponent, contract, case_id)

    for attempt in range(3):
        before = int(_case(client, contract, case_id)["attempts"])
        _expect_ok(client, account, contract, "rule", [case_id])

        record = _case(client, contract, case_id)
        assert record["status"] == "CONTESTED", (
            "the committee grounded a ruling in a page that cannot ground one: "
            f"{record}"
        )
        assert int(record["attempts"]) == before + 1, record
        assert record["ruling"] == "", record

    ledger_before = _ledger(client, contract)

    # Anyone may void it, so the account that did not file does it.
    _expect_ok(
        client,
        opponent,
        contract,
        "void",
        [case_id],
        verify=lambda: _case(client, contract, case_id)["status"] == "VOID",
    )

    assert _case(client, contract, case_id)["status"] == "VOID"

    ledger = _ledger(client, contract)
    assert int(ledger["total_out"]) == int(ledger_before["total_out"]) + 2 * BOND
    assert int(ledger["locked_total"]) == int(ledger_before["locked_total"]) - 2 * BOND
    assert _invariant_holds(client, contract)


def test_06_views_agree_with_the_ledger(client, contract):
    summary = _read(client, contract, "get_summary")
    cases = _read(client, contract, "get_cases")

    assert int(summary["cases"]) == len(cases)
    assert int(summary["cases"]) == len(_case_ids(client, contract))
    assert _balance(client, contract) >= 0
    assert _invariant_holds(client, contract)
