"""Views: what the court admits about itself, and when an appeal is still open."""

import json

BOND = 200
WINDOW = 3600


def _deployed(direct_deploy):
    return direct_deploy("contracts/Assize.py")


def _filed(direct_vm, contract, filer, ref="ref-1"):
    direct_vm.sender = filer
    direct_vm.value = BOND
    case_id = contract.file_case(
        "The project publishes a signed release.", "https://source.example.com/page", WINDOW, ref
    )
    direct_vm.value = 0
    return case_id


def test_a_fresh_contract_is_empty(direct_deploy):
    contract = _deployed(direct_deploy)

    assert contract.get_case_ids()["ids"] == []
    assert contract.get_cases() == {}
    assert contract.get_ledger() == {"total_in": 0, "total_out": 0, "locked_total": 0}

    summary = contract.get_summary()
    assert summary["cases"] == 0
    assert summary["appeals"] == 0
    assert summary["settled"] == 0


def test_cases_are_listed_in_order(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deployed(direct_deploy)

    _filed(direct_vm, contract, direct_alice, ref="a")
    _filed(direct_vm, contract, direct_bob, ref="b")

    assert contract.get_case_ids()["ids"] == ["az-1", "az-2"]

    cases = contract.get_cases()
    assert len(cases) == 2
    assert cases["az-1"]["status"] == "FILED"


def test_an_appeal_is_open_only_inside_the_window(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)
    case_id = _filed(direct_vm, contract, direct_alice)

    assert contract.can_appeal(case_id) is False

    direct_vm.sender = direct_bob
    direct_vm.value = BOND
    contract.contest(case_id)
    direct_vm.value = 0

    direct_vm.mock_web(
        r".*source\.example\.com.*",
        {"status": 200, "body": "The project publishes a signed release every month."},
    )
    direct_vm.mock_llm(
        r".*\[\[ASSIZE_RULING\]\].*",
        json.dumps(
            {
                "ruling": "SUSTAINED",
                "quote": "The project publishes a signed release every month.",
                "reason": "the page says so",
            }
        ),
    )
    contract.rule(case_id)

    assert contract.can_appeal(case_id) is True

    direct_vm.warp("2025-01-01T02:00:00Z")
    assert contract.can_appeal(case_id) is False


def test_unknown_cases_are_never_contested_or_appealable(direct_deploy):
    contract = _deployed(direct_deploy)

    assert contract.is_contested("az-404") is False
    assert contract.can_appeal("az-404") is False


def test_the_rules_are_public(direct_deploy):
    contract = _deployed(direct_deploy)
    rules = contract.get_rules()

    assert rules["min_bond"] == 200
    assert rules["max_attempts"] == 3
    assert rules["min_appeal_window_seconds"] == 60
    assert set(rules["grounds"]) == {"RECORD", "EVIDENCE"}
