"""Consensus: the ruling and the record it was taken from.

A RECORD appeal never fetches the page — it argues over the record the first
instance wrote down. That record is only worth something if the committee agreed on
it, so the first instance's decision is two fields, not one: the ruling, and the
keccak digest of the record. Two validators that rule the same way on two
different records have not agreed, and the case must stay undecided rather than be
settled on a record one of them never read.
"""

import json

CLAIM = "The project publishes a signed release."
URL = "https://source.example.com/page"
PAGE = (
    "The project publishes a signed release every month. The release notes list "
    "every change and the repository is public."
)
# The same ruling, the same citation, a page that no longer reads the same: the
# quote still grounds, so only the record tells the two readings apart.
LATER_PAGE = PAGE + " This paragraph was added after the ruling was entered."
QUOTE = "The project publishes a signed release every month."

BOND = 200
WINDOW = 3600


def _deployed(direct_deploy):
    return direct_deploy("contracts/Assize.py")


def _mock_page(direct_vm, body=PAGE):
    direct_vm.mock_web(r".*source\.example\.com.*", {"status": 200, "body": body})


def _mock_ruling(direct_vm, ruling="SUSTAINED", quote=QUOTE):
    direct_vm.mock_llm(
        r".*\[\[ASSIZE_RULING\]\].*",
        json.dumps({"ruling": ruling, "quote": quote, "reason": "the page says so"}),
    )


def _ruled(direct_vm, direct_deploy, filer, contestant):
    contract = _deployed(direct_deploy)

    direct_vm.sender = filer
    direct_vm.value = BOND
    case_id = contract.file_case(CLAIM, URL, WINDOW, "ref-1")
    direct_vm.value = 0

    direct_vm.sender = contestant
    direct_vm.value = BOND
    contract.contest(case_id)
    direct_vm.value = 0

    _mock_page(direct_vm)
    _mock_ruling(direct_vm)
    contract.rule(case_id)

    return contract, case_id


def test_a_validator_accepts_the_same_ruling_and_record(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id = _ruled(direct_vm, direct_deploy, direct_alice, direct_bob)

    assert contract.get_case(case_id)["status"] == "RULED"
    assert direct_vm.run_validator() is True


def test_a_validator_rejects_a_different_record_under_the_same_ruling(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, case_id = _ruled(direct_vm, direct_deploy, direct_alice, direct_bob)
    recorded = contract.get_case(case_id)["record_digest"]

    direct_vm.clear_mocks()
    _mock_page(direct_vm, LATER_PAGE)
    _mock_ruling(direct_vm)

    assert direct_vm.run_validator() is False, (
        "the same ruling on a different record is not the same judgement: a RECORD "
        "appeal would argue over text this validator never read, and the payout "
        "would follow a record that was never agreed on"
    )
    assert contract.get_case(case_id)["record_digest"] == recorded


def test_a_validator_rejects_a_different_ruling_on_the_same_record(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    _ruled(direct_vm, direct_deploy, direct_alice, direct_bob)

    direct_vm.clear_mocks()
    _mock_page(direct_vm)
    _mock_ruling(direct_vm, ruling="REJECTED")

    assert direct_vm.run_validator() is False


def test_a_validator_refuses_when_it_cannot_read_the_page(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    _ruled(direct_vm, direct_deploy, direct_alice, direct_bob)

    # No page and no model: the validator cannot say which record was read, so it
    # cannot bind the one the appeal will be handed.
    direct_vm.clear_mocks()

    assert direct_vm.run_validator() is False
