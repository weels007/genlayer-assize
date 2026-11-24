"""Assize end to end on the deployed court: the money paths around a ruling.

No model is called here on purpose — the Studio suite covers the judgements. This
one covers the refusals and the exits: a mismatched stake, a self-contest, a
filing withdrawn before anyone contests it, a contested case that cannot be
withdrawn, a void that has not been earned, and a stranger reaching for a bond
that is not theirs.

A claim reference is caller-scoped and permanent, so every reference carries the
run's start time.

    pytest tests/e2e -v -s
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
WINDOW = 60
SOURCE_URL = "https://raw.githubusercontent.com/weels007/genlayer-vow/main/README.md"
CLAIM_TEXT = "The project documents how a stranger can install and run it."

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
def contract() -> str:
    payload = _deployment()
    print(f"\n[assize-e2e] contract {payload['address']}")
    return payload["address"]


@pytest.fixture(scope="module")
def account():
    return load_account("tester1")


@pytest.fixture(scope="module")
def opponent(account):
    """A second funded wallet, or None when only one is configured."""
    try:
        other = load_account("tester2")
    except SystemExit:
        return None
    if _hex(other.address) == _hex(account.address):
        return None
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


# See the Studio suite: the network has been asking more than its client quotes,
# so a send reverted at the EVM step is tried again with a larger fee deposit.
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


def _expect_denied(client, account, contract, method, args=None, value=0):
    result = _attempt(client, account, contract, method, args, value)
    assert not _succeeded(result), f"{method} should have been refused: {result}"
    return result


def _read(client, contract, method, args=None):
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


def _file(client, account, contract, ref):
    before = len(_case_ids(client, contract))
    _expect_ok(
        client,
        account,
        contract,
        "file_case",
        [CLAIM_TEXT, SOURCE_URL, WINDOW, f"{ref}-{RUN}"],
        value=BOND,
        verify=lambda: len(_case_ids(client, contract)) > before,
    )
    ids = _case_ids(client, contract)
    assert len(ids) > before, "the case was not filed"
    return ids[-1]


# ---------------------------------------------------------------------------
# the credit path
# ---------------------------------------------------------------------------


def test_01_the_court_is_live(client, contract):
    rules = _read(client, contract, "get_rules")
    assert int(rules["min_bond"]) == BOND
    assert set(rules["grounds"]) == {"RECORD", "EVIDENCE"}
    assert _invariant_holds(client, contract)


def test_02_a_mismatched_stake_becomes_a_credit(client, account, contract):
    case_id = _file(client, account, contract, "e2e-credit")
    locked_before = int(_ledger(client, contract)["locked_total"])

    # A stake that does not match the bond is not a contest: it is a credit.
    _expect_ok(
        client,
        account,
        contract,
        "contest",
        [case_id],
        value=BOND + 1,
        verify=lambda: _credit(client, contract, account) >= BOND + 1,
    )

    assert _case(client, contract, case_id)["status"] == "FILED"
    assert _credit(client, contract, account) >= BOND + 1

    ledger = _ledger(client, contract)
    assert int(ledger["locked_total"]) == locked_before + BOND + 1
    assert _invariant_holds(client, contract)


def test_03_the_credit_comes_back(client, account, contract):
    locked_before = int(_ledger(client, contract)["locked_total"])
    credit_before = _credit(client, contract, account)
    assert credit_before > 0

    _expect_ok(
        client,
        account,
        contract,
        "withdraw_credit",
        verify=lambda: _credit(client, contract, account) == 0,
    )

    ledger = _ledger(client, contract)
    assert int(ledger["locked_total"]) == locked_before - credit_before
    assert _invariant_holds(client, contract)


def test_04_a_filer_cannot_contest_their_own_case(client, account, contract):
    case_id = _file(client, account, contract, "e2e-self")

    _expect_ok(
        client,
        account,
        contract,
        "contest",
        [case_id],
        value=BOND,
        verify=lambda: _credit(client, contract, account) >= BOND,
    )

    assert _case(client, contract, case_id)["status"] == "FILED"
    _expect_ok(client, account, contract, "withdraw_credit")


# ---------------------------------------------------------------------------
# the exits
# ---------------------------------------------------------------------------


def test_05_an_uncontested_filing_can_be_withdrawn(client, account, contract):
    case_id = _file(client, account, contract, "e2e-withdraw")

    out_before = int(_ledger(client, contract)["total_out"])
    _expect_ok(
        client,
        account,
        contract,
        "withdraw_case",
        [case_id],
        verify=lambda: _case(client, contract, case_id)["status"] == "VOID",
    )

    assert _case(client, contract, case_id)["status"] == "VOID"
    ledger = _ledger(client, contract)
    assert int(ledger["total_out"]) == out_before + BOND
    assert _invariant_holds(client, contract)


def test_06_a_stranger_cannot_withdraw_the_filing(client, account, contract, opponent):
    if opponent is None:
        pytest.skip("only one wallet is configured, so a stranger would be the filer")

    case_id = _file(client, account, contract, "e2e-stranger")

    touched = _attempt(client, opponent, contract, "withdraw_case", [case_id])
    assert not _succeeded(touched), "a stranger withdrew somebody else's filing"
    assert _case(client, contract, case_id)["status"] == "FILED"

    _expect_ok(client, account, contract, "withdraw_case", [case_id])


def test_07_a_contested_case_cannot_be_withdrawn(client, account, contract, opponent):
    if opponent is None:
        pytest.skip("only one wallet is configured, so there is nothing to contest with")

    case_id = _file(client, account, contract, "e2e-contested")
    _expect_ok(client, opponent, contract, "contest", [case_id], value=BOND)
    assert _case(client, contract, case_id)["status"] == "CONTESTED"

    _expect_denied(client, account, contract, "withdraw_case", [case_id])
    assert _case(client, contract, case_id)["status"] == "CONTESTED"


def test_08_void_is_refused_before_it_is_earned(client, account, contract, opponent):
    if opponent is None:
        pytest.skip("only one wallet is configured, so there is nothing to contest with")

    case_id = _file(client, account, contract, "e2e-void")
    _expect_ok(client, opponent, contract, "contest", [case_id], value=BOND)

    _expect_denied(client, account, contract, "void", [case_id])
    assert _case(client, contract, case_id)["status"] == "CONTESTED"
    assert _invariant_holds(client, contract)


def test_09_views_agree_with_the_ledger(client, contract):
    summary = _read(client, contract, "get_summary")
    cases = _read(client, contract, "get_cases")

    assert int(summary["cases"]) == len(cases)
    assert int(summary["cases"]) == len(_case_ids(client, contract))
    assert _balance(client, contract) >= 0
    assert _invariant_holds(client, contract)
