"""
State integrity: derivation, storage, access control and the clock.

Obligation state is never stored as a status field that a write has to remember to
update. The contract stores facts and derives the live state every time it is asked.
These tests check that there is no pair of values that can drift apart, that the
registry's steward has no authority over a live pact, and that the clock behaves the
way GenVM guarantees it does.
"""

import pytest

from conftest import *  # noqa: F403


# ---------------------------------------------------------------------------
# Derivation rather than stored status
# ---------------------------------------------------------------------------

def test_a_deadline_passing_changes_the_state_with_no_transaction_at_all(host, enforcer, accounts):
    """
    The consequence of deriving rather than storing. Nobody has to poke the contract
    for a missed deadline to become a breach.
    """
    active_pact(host, enforcer, accounts, window_a=3 * DAY, window_b=9 * DAY)
    assert enforcer.obligation_state("pact-1", "A") == "OPEN"

    host.warp(T0 + 3 * DAY + 1)
    assert enforcer.obligation_state("pact-1", "A") == "BREACHED"
    assert enforcer.obligation_state("pact-1", "B") == "OPEN"

    host.warp(T0 + 9 * DAY + 1)
    assert enforcer.obligation_state("pact-1", "B") == "BREACHED"


def test_every_view_reports_the_same_derived_state(host, enforcer, accounts):
    """
    `get_pact`, `get_obligation`, `obligation_state` and `is_resolvable` all route
    through one derivation, so there is no way for them to disagree.
    """
    active_pact(host, enforcer, accounts, window_a=3 * DAY, window_b=3 * DAY, dependency="b_after_a")
    host.warp(T0 + 3 * DAY + 1)

    pact = json.loads(enforcer.get_pact("pact-1"))
    resolvable = json.loads(enforcer.is_resolvable("pact-1"))
    assert pact["state_a"] == resolvable["state_a"] == enforcer.obligation_state("pact-1", "A") == "BREACHED"
    assert pact["state_b"] == resolvable["state_b"] == enforcer.obligation_state("pact-1", "B") == "EXCUSED"
    assert json.loads(enforcer.get_obligation("pact-1", "A"))["state"] == "BREACHED"
    assert json.loads(enforcer.get_obligation("pact-1", "B"))["state"] == "EXCUSED"


def test_remaining_seconds_counts_down_with_the_clock(host, enforcer, accounts):
    active_pact(host, enforcer, accounts, window_a=3 * DAY)
    assert json.loads(enforcer.get_obligation("pact-1", "A"))["seconds_remaining"] == 3 * DAY

    host.warp(T0 + DAY)
    assert json.loads(enforcer.get_obligation("pact-1", "A"))["seconds_remaining"] == 2 * DAY

    host.warp(T0 + 5 * DAY)
    assert json.loads(enforcer.get_obligation("pact-1", "A"))["seconds_remaining"] == 0


def test_a_waiting_side_reports_no_deadline_and_no_remaining_time(host, enforcer, accounts):
    active_pact(host, enforcer, accounts, dependency="b_after_a")
    ob = json.loads(enforcer.get_obligation("pact-1", "B"))
    assert ob["state"] == "WAITING"
    assert ob["deadline_at"] == 0
    assert ob["seconds_remaining"] == 0
    assert ob["clock_started"] is False


def test_the_derivation_does_not_recurse_on_a_chained_pact(host, enforcer, accounts):
    """
    A dependency only ever points one way, so the independent side is always computable
    first. This is the property that keeps the derivation a plain function.
    """
    for dependency in ("b_after_a", "a_after_b"):
        host.reset()
        host.warp(T0)
        local_accounts = {name: host.new_address() for name in ("deployer", "alice", "bob", "outsider")}
        local = host.deploy(
            ENFORCER_SOURCE, "BilateralCommitmentEnforcer",
            args=["R"], sender=local_accounts["deployer"],
        )
        active_pact(host, local, local_accounts, dependency=dependency)
        pact = json.loads(local.get_pact("pact-1"))
        assert {pact["state_a"], pact["state_b"]} == {"OPEN", "WAITING"}


# ---------------------------------------------------------------------------
# Storage isolation between pacts
# ---------------------------------------------------------------------------

def test_pacts_do_not_share_state(host, enforcer, accounts):
    create(host, enforcer, accounts, pact_id="pact-1", stake_a=3 * ONE, stake_b=3 * ONE)
    accept(enforcer, accounts, pact_id="pact-1", stake_b=3 * ONE)

    serve_admissibility(host)
    create(host, enforcer, accounts, pact_id="pact-2", stake_a=9 * ONE, stake_b=9 * ONE,
           dependency="b_after_a", serve=False)
    accept(enforcer, accounts, pact_id="pact-2", stake_b=9 * ONE)

    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, pact_id="pact-1", side="A")

    assert enforcer.obligation_state("pact-1", "A") == "FULFILLED"
    assert enforcer.obligation_state("pact-2", "A") == "OPEN"
    assert enforcer.obligation_state("pact-2", "B") == "WAITING"
    assert json.loads(enforcer.get_pact("pact-2"))["stake_a_wei"] == str(9 * ONE)
    assert json.loads(enforcer.get_registry_info())["total_locked_wei"] == str(24 * ONE)


def test_settling_one_pact_leaves_another_untouched(host, enforcer, accounts):
    active_pact(host, enforcer, accounts, pact_id="pact-1", stake_a=3 * ONE, stake_b=3 * ONE)
    serve_admissibility(host)
    create(host, enforcer, accounts, pact_id="pact-2", stake_a=9 * ONE, stake_b=9 * ONE, serve=False)
    accept(enforcer, accounts, pact_id="pact-2", stake_b=9 * ONE)

    enforcer.agree_release("pact-1", sender=accounts["alice"])
    enforcer.agree_release("pact-1", sender=accounts["bob"])

    assert json.loads(enforcer.get_pact("pact-1"))["status"] == "SETTLED"
    assert json.loads(enforcer.get_pact("pact-2"))["status"] == "ACTIVE"
    assert json.loads(enforcer.get_registry_info())["total_locked_wei"] == str(18 * ONE)


def test_a_failed_transaction_leaves_no_partial_state(host, enforcer, accounts):
    """
    GenVM applies no state change when a transaction fails. A creation that reverts on
    an inadmissible commitment must leave nothing behind, including the pact index and
    the locked total.
    """
    before = json.loads(enforcer.get_registry_info())
    host.mock_llm(ADMISSIBILITY_PATTERN, admissibility_reply(a_ok=False, a_defect="unspecific"))
    with pytest.raises(Exception):
        create(host, enforcer, accounts, serve=False)

    after = json.loads(enforcer.get_registry_info())
    assert after == before
    assert enforcer.pact_exists("pact-1") is False
    assert enforcer.party_pact_count_of(accounts["alice"]) == 0


# ---------------------------------------------------------------------------
# Rubric and commitment immutability
# ---------------------------------------------------------------------------

def test_the_rubric_cannot_change_after_creation(host, enforcer, accounts):
    """
    There is no method that writes a checklist after creation. The rubric a party
    consented to is the rubric every panel scores, for the life of the pact.
    """
    created = create(host, enforcer, accounts)
    before = json.loads(enforcer.get_rubric("pact-1", "A"))
    accept(enforcer, accounts)

    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")

    after = json.loads(enforcer.get_rubric("pact-1", "A"))
    assert after["checklist"] == before["checklist"] == CHECKLIST_A
    assert after["checklist_digest"] == before["checklist_digest"]
    assert enforcer.terms_digest("pact-1") == created["terms_digest"]

    writers = [name for name in dir(enforcer.contract_cls) if not name.startswith("_")]
    assert not any("checklist" in name or "rubric" in name for name in writers if name.startswith("set_"))


def test_the_rubric_is_handed_to_every_panel_verbatim(host, enforcer, accounts):
    """
    Every validator scores the identical list, which is what makes two point totals
    comparable in a way two independently authored rubrics never would be.
    """
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")

    scoring_prompts = [c["prompt"] for c in host.llm_calls if "performed what it promised" in c["prompt"]]
    assert len(scoring_prompts) == 2
    for prompt in scoring_prompts:
        for index, item in enumerate(CHECKLIST_A, start=1):
            assert f"{index}. {item}" in prompt
    # Both panels received byte identical rubric text.
    assert scoring_prompts[0] == scoring_prompts[1]


# ---------------------------------------------------------------------------
# Steward powers, and their limits
# ---------------------------------------------------------------------------

def test_the_steward_may_change_defaults_for_future_pacts(host, enforcer, accounts):
    out = json.loads(enforcer.set_defaults(json.dumps({"pass_threshold": 50, "max_attempts": 5}),
                                          sender=accounts["deployer"]))
    assert out["pass_threshold"] == 50
    assert out["max_attempts"] == 5
    assert out["applies_to"] == "pacts created after this transaction only"

    serve_admissibility(host)
    created = create(host, enforcer, accounts, serve=False,
                     options={"checklist_a": CHECKLIST_A, "checklist_b": CHECKLIST_B})
    assert created["pass_threshold"] == 50
    assert created["max_attempts"] == 5


def test_changing_a_default_cannot_move_a_pact_that_already_exists(host, enforcer, accounts):
    """
    Every term that decides money is copied onto the pact at creation. This is what
    lets an integrator treat a deployed registry as something it can rely on rather
    than something whose rules can move under it.
    """
    created = create(host, enforcer, accounts, options=default_options(pass_threshold=80))
    accept(enforcer, accounts)
    digest_before = created["terms_digest"]

    enforcer.set_defaults(json.dumps({"pass_threshold": 20, "max_attempts": 9}), sender=accounts["deployer"])

    pact = json.loads(enforcer.get_pact("pact-1"))
    assert pact["pass_threshold"] == 80
    assert pact["max_attempts"] == 3
    assert enforcer.terms_digest("pact-1") == digest_before

    # And the live pact still judges against the threshold it was created with.
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(TWO_OF_THREE))
    assert submit(enforcer, accounts, side="A")["passed"] is False


def test_only_the_steward_may_change_defaults_or_hand_over_stewardship(host, enforcer, accounts):
    for method, args in (
        ("set_defaults", (json.dumps({"pass_threshold": 10}),)),
        ("transfer_stewardship", (accounts["carol"],)),
    ):
        with pytest.raises(Exception) as excinfo:
            getattr(enforcer, method)(*args, sender=accounts["alice"])
        assert "Only the steward" in revert_message(excinfo)


def test_an_unknown_default_is_refused_rather_than_silently_ignored(host, enforcer, accounts):
    with pytest.raises(Exception) as excinfo:
        enforcer.set_defaults(json.dumps({"pass_threshhold": 50}), sender=accounts["deployer"])
    assert "Unknown default" in revert_message(excinfo)


def test_stewardship_transfers_and_the_old_steward_loses_access(host, enforcer, accounts):
    out = json.loads(enforcer.transfer_stewardship(accounts["carol"], sender=accounts["deployer"]))
    assert out["steward"] == accounts["carol"]
    assert json.loads(enforcer.get_registry_info())["steward"] == accounts["carol"]

    with pytest.raises(Exception):
        enforcer.set_defaults(json.dumps({"pass_threshold": 50}), sender=accounts["deployer"])
    assert json.loads(enforcer.set_defaults(json.dumps({"pass_threshold": 50}),
                                           sender=accounts["carol"]))["pass_threshold"] == 50


def test_the_steward_has_no_power_over_a_pact_itself(host, enforcer, accounts):
    """
    The steward is not a party, so every pact level method refuses it exactly as it
    refuses any other outsider. There is no admin override.
    """
    active_pact(host, enforcer, accounts)
    for method, args in (
        ("submit_evidence", ("pact-1", EVIDENCE_A)),
        ("agree_release", ("pact-1",)),
        ("challenge_fulfilment", ("pact-1", "A", "A long enough set of grounds for the panel to read.")),
    ):
        with pytest.raises(Exception) as excinfo:
            getattr(enforcer, method)(*args, sender=accounts["deployer"])
        assert "not a party to this pact" in revert_message(excinfo)


def test_the_steward_cannot_be_set_to_the_zero_address(host, enforcer, accounts):
    with pytest.raises(Exception) as excinfo:
        enforcer.transfer_stewardship(ZERO, sender=accounts["deployer"])
    assert "cannot be the zero address" in revert_message(excinfo)


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------

def test_the_clock_is_pinned_so_a_leader_and_a_validator_read_the_same_instant(host, enforcer, accounts):
    """
    GenVM pins `datetime.now()` to the transaction datetime, which is what lets a
    deadline be a deterministic fact rather than a race. Every timestamp written by
    one transaction is identical.
    """
    accepted = active_pact(host, enforcer, accounts, window_a=3 * DAY, window_b=3 * DAY)["accepted"]
    assert accepted["activated_at"] == T0
    assert accepted["a"]["deadline_at"] == accepted["b"]["deadline_at"] == T0 + 3 * DAY

    pact = json.loads(enforcer.get_pact("pact-1"))
    assert pact["created_at"] == pact["activated_at"] == T0


def test_a_broken_clock_falls_back_to_the_transaction_datetime(host, enforcer, accounts):
    """
    A deadline that silently read zero would make every obligation look breached, so
    the fallback is the transaction datetime carried on the message rather than a
    default of nothing.
    """
    active_pact(host, enforcer, accounts, window_a=3 * DAY)
    host.clock_mode = "broken_now"
    try:
        assert enforcer.obligation_state("pact-1", "A") == "OPEN"
        assert json.loads(enforcer.get_pact("pact-1"))["observed_at"] == T0
    finally:
        host.clock_mode = "pinned"


def test_a_fully_dead_clock_fails_closed_to_the_registry_creation_time(host, enforcer, accounts):
    """
    The last resort. Reading the pact's own floor keeps every obligation OPEN rather
    than sweeping every pact into breach, which is the safe direction to fail.
    """
    active_pact(host, enforcer, accounts, window_a=3 * DAY)
    host.clock_mode = "dead"
    try:
        assert enforcer.obligation_state("pact-1", "A") == "OPEN"
    finally:
        host.clock_mode = "pinned"


# ---------------------------------------------------------------------------
# View guards
# ---------------------------------------------------------------------------

def test_views_on_an_unknown_pact_revert_with_a_readable_reason(host, enforcer, accounts):
    for method, args in (
        ("get_pact", ("nope",)),
        ("terms_digest", ("nope",)),
        ("get_rubric", ("nope", "A")),
        ("is_resolvable", ("nope",)),
        ("settlement_preview", ("nope",)),
    ):
        with pytest.raises(Exception) as excinfo:
            getattr(enforcer, method)(*args)
        assert "No pact with id 'nope'" in revert_message(excinfo)

    assert enforcer.pact_exists("nope") is False


def test_an_invalid_side_is_refused_everywhere_it_is_accepted(host, enforcer, accounts):
    active_pact(host, enforcer, accounts)
    for method in ("get_obligation", "obligation_state", "is_fulfilled", "get_rubric"):
        with pytest.raises(Exception) as excinfo:
            getattr(enforcer, method)("pact-1", "X")
        assert "side must be 'A' or 'B'" in revert_message(excinfo)


def test_an_index_out_of_range_is_refused(host, enforcer, accounts):
    create(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        enforcer.get_pact_id_at(1)
    assert "index out of range" in revert_message(excinfo)
    with pytest.raises(Exception) as excinfo:
        enforcer.party_pact_id_at(accounts["alice"], 5)
    assert "index out of range for this party" in revert_message(excinfo)


def test_a_settlement_cannot_be_read_before_it_exists(host, enforcer, accounts):
    active_pact(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        enforcer.get_settlement("pact-1")
    assert "has not settled" in revert_message(excinfo)


def test_fulfilment_views_report_false_on_a_proposed_pact(host, enforcer, accounts):
    """
    A pact nobody has accepted has no obligations in any meaningful sense, so a gate
    reading it must get false rather than an error or an optimistic true.
    """
    create(host, enforcer, accounts)
    assert enforcer.is_fulfilled("pact-1", "A") is False
    assert enforcer.both_fulfilled("pact-1") is False
    with pytest.raises(Exception) as excinfo:
        enforcer.require_mutual_fulfilment("pact-1")
    assert "is PROPOSED and has no fulfilment to report" in revert_message(excinfo)
