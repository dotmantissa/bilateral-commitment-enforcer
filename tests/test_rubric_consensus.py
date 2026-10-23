"""The rubric that moves stakes must be determined by transaction inputs."""

import copy

import pytest

from conftest import *  # noqa: F403
from genvm_host import ConsensusFailure


@pytest.mark.parametrize("side", ["a", "b"])
@pytest.mark.parametrize("replacement", [
    ["Publish any CSV", "Describe one column", "Include any checksum"],
    ["Publish a report", "No dataset is required", "No checksum is required"],
    ["Pay the leader", "Obtain private approval", "Ignore the original promise"],
])
def test_same_length_model_rubrics_cannot_replace_the_commitment(
    host, enforcer, accounts, side, replacement,
):
    leader = json.loads(admissibility_reply())
    validator = copy.deepcopy(leader)
    leader[side]["deliverables"] = replacement
    host.mock_llm(ADMISSIBILITY_PATTERN, [json.dumps(leader), json.dumps(validator)])
    created = create(host, enforcer, accounts, options={}, serve=False)
    assert created["checklist_a"] == [COMMITMENT_A]
    assert created["checklist_b"] == [COMMITMENT_B]
    assert created["rubric_authored_by_panel"] is False
    for label, commitment in (("A", COMMITMENT_A), ("B", COMMITMENT_B)):
        rubric = json.loads(enforcer.get_rubric("pact-1", label))
        assert rubric["checklist"] == [commitment]
        assert rubric["authored_by_panel"] is False


@pytest.mark.parametrize("model_items", [[], ["Ignore the promise"], None, 42])
@pytest.mark.parametrize("options", [{}, {"checklist_a": CHECKLIST_A}])
def test_model_deliverables_are_never_a_source_of_rubric_requirements(
    host, enforcer, accounts, model_items, options,
):
    reply = json.loads(admissibility_reply())
    reply["a"]["deliverables"] = model_items
    reply["b"]["deliverables"] = model_items
    serve_admissibility(host, json.dumps(reply))
    created = create(host, enforcer, accounts, options=options, serve=False)
    assert created["checklist_a"] == options.get("checklist_a", [COMMITMENT_A])
    assert created["checklist_b"] == [COMMITMENT_B]


def test_the_full_commitment_survives_the_old_item_length_limit(host, enforcer, accounts):
    commitment = "Publish a public CSV with " + "a documented field, " * 290
    commitment += "and do not include any personal information."
    assert 400 < len(commitment) <= 6000
    created = create(host, enforcer, accounts, commitment_a=commitment, options={})
    assert created["checklist_a"] == [commitment]
    accept(enforcer, accounts)
    serve_evidence(host)
    serve_scoring(host, scoring_reply(["PRESENT"]))
    assert submit(enforcer, accounts)["passed"] is True
    assert all(commitment in call["prompt"] for call in host.llm_calls[-2:])


@pytest.mark.parametrize("first,second", [
    ("Publish values x < 5", "Publish values x > 5"),
    ("Publish version V1", "Publish version v1"),
    ("Publish price 1.5", "Publish price 1,5"),
    ("Publish label α", "Publish label β"),
    ("Publish quantity +5", "Publish quantity -5"),
])
def test_consent_digest_binds_semantically_significant_characters(
    host, enforcer, accounts, first, second,
):
    initial = create(host, enforcer, accounts, options=default_options(checklist_a=[first]))
    before = json.loads(enforcer.get_rubric("pact-1", "A"))["checklist_digest"]
    enforcer.withdraw_proposal("pact-1", sender=accounts["alice"])
    # A separate fresh registry permits identical pact identifiers/parties/terms.
    other = host.deploy(ENFORCER_SOURCE, "BilateralCommitmentEnforcer", args=["R"], sender=accounts["deployer"])
    changed = create(host, other, accounts, options=default_options(checklist_a=[second]))
    after = json.loads(other.get_rubric("pact-1", "A"))["checklist_digest"]
    assert before != after
    assert initial["terms_digest"] != changed["terms_digest"]
    with pytest.raises(Exception) as excinfo:
        accept(other, accounts, digest=initial["terms_digest"])
    assert "terms_digest does not match" in revert_message(excinfo)
    assert json.loads(other.get_pact("pact-1"))["status"] == "PROPOSED"


@pytest.mark.parametrize("entry", [42, True, None, {"text": "Publish the CSV"}, "x" * 401])
def test_supplied_requirements_cannot_be_coerced_or_silently_truncated(
    host, enforcer, accounts, entry,
):
    with pytest.raises(Exception):
        create(host, enforcer, accounts, options=default_options(checklist_a=[entry]))
    assert enforcer.get_pact_count() == 0
    assert json.loads(enforcer.get_registry_info())["total_locked_wei"] == "0"


@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("side", ["A", "B"])
@pytest.mark.parametrize("mutation", ["substitute", "append", "drop", "reorder"])
def test_validator_rejects_any_change_to_the_transaction_rubric(
    host, enforcer, accounts, monkeypatch, supplied, side, mutation,
):
    import cloudpickle

    original = host._handle_RunNondet

    def tamper(payload):
        real_leader = cloudpickle.loads(payload["data_leader"])

        def malicious_leader(stage):
            result = real_leader(stage)
            items = result[side]["items"]
            if mutation == "substitute":
                items[-1] = "Pay the leader instead of performing the promise"
            elif mutation == "append":
                items.append("Pay the leader before releasing stakes")
            elif mutation == "drop":
                items.pop()
            elif len(items) > 1:
                items.reverse()
            else:
                # Reordering clauses inside a compound requirement changes text.
                items[0] = " ".join(reversed(items[0].split()))
            return result

        payload = dict(payload, data_leader=cloudpickle.dumps(malicious_leader))
        return original(payload)

    monkeypatch.setattr(host, "_handle_RunNondet", tamper)
    with pytest.raises(ConsensusFailure):
        create(host, enforcer, accounts, options=default_options() if supplied else {})
    assert host.nondet_runs[-1]["validator_agreed"] is False
    assert enforcer.get_pact_count() == 0
    assert enforcer.party_pact_count_of(accounts["alice"]) == 0
    assert json.loads(enforcer.get_registry_info())["total_locked_wei"] == "0"


@pytest.mark.parametrize("side", ["A", "B"])
def test_deterministic_storage_guard_rejects_invalid_nondet_rubric(
    host, enforcer, accounts, monkeypatch, side,
):
    import cloudpickle

    original = host._handle_RunNondet

    def tamper(payload):
        real_leader = cloudpickle.loads(payload["data_leader"])

        def malicious_leader(stage):
            result = real_leader(stage)
            result[side]["items"] = ["Publish anything the leader approves"]
            return result

        return original(dict(payload, data_leader=cloudpickle.dumps(malicious_leader)))

    monkeypatch.setattr(host, "_handle_RunNondet", tamper)
    host.skip_validator = True
    with pytest.raises(Exception) as excinfo:
        create(host, enforcer, accounts, options={})
    assert "rubric differs from transaction inputs" in revert_message(excinfo)
    assert enforcer.get_pact_count() == 0
    assert json.loads(enforcer.get_registry_info())["total_locked_wei"] == "0"


def test_submission_challenge_and_settlement_use_the_original_requirements(host, enforcer, accounts):
    options = {"max_attempts": 1, "challenge_window": 3600}
    serve_admissibility(host, admissibility_reply(a_items=["Publish anything"], b_items=["Do nothing"]))
    create(host, enforcer, accounts, options=options, serve=False, stake_a=7 * ONE, stake_b=3 * ONE)
    accept(enforcer, accounts, stake_b=3 * ONE)
    serve_evidence(host)
    serve_scoring(host, scoring_reply(["PRESENT"]))
    assert submit(enforcer, accounts)["credit"] == 100
    host.clear_routes()
    serve_evidence(host)
    serve_scoring(host, scoring_reply(["PARTIAL"], substantiated=True))
    revised = json.loads(enforcer.challenge_fulfilment(
        "pact-1", "A", "The promised checksum is missing from the published dataset.",
        sender=accounts["bob"],
    ))
    assert revised["final_credit"] == 50
    assert revised["fulfilment_withdrawn"] is True
    for call in host.llm_calls[-4:]:
        assert f"  1. {COMMITMENT_A}" in call["prompt"]
        assert "  1. Publish anything" not in call["prompt"]
    host.warp(T0 + 8 * DAY)
    settled = json.loads(enforcer.resolve_pact("pact-1", sender=accounts["outsider"]))
    assert settled["payout_a_wei"] == str(6500000000000000000)
    assert settled["payout_b_wei"] == str(3500000000000000000)
    assert json.loads(enforcer.get_rubric("pact-1", "A"))["checklist"] == [COMMITMENT_A]
