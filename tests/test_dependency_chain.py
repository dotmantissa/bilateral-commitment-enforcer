"""
Dependency chaining: the feature that makes this one instrument rather than two
escrows sitting next to each other.

A chained pact declares that one side's obligation does not begin until the other
side's fulfilment has been confirmed by consensus. Everything in this file is
about the consequences of that: a clock that has not started, a clock that starts
on confirmation, an obligation excused because its predecessor never delivered,
and the refusal that makes the declaration mean something.
"""

import pytest

from conftest import *  # noqa: F403


def chained(host, enforcer, accounts, dependency="b_after_a", **kwargs):
    """A chained, active pact with both evidence routes served."""
    active_pact(host, enforcer, accounts, dependency=dependency, **kwargs)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_evidence(host, EVIDENCE_B, BODY_B_COMPLETE)


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

def test_an_unchained_pact_starts_both_clocks_at_acceptance(host, enforcer, accounts):
    accepted = active_pact(host, enforcer, accounts, dependency="none", window_a=3 * DAY, window_b=5 * DAY)["accepted"]

    assert accepted["a"]["state"] == "OPEN"
    assert accepted["b"]["state"] == "OPEN"
    assert accepted["a"]["deadline_at"] == T0 + 3 * DAY
    assert accepted["b"]["deadline_at"] == T0 + 5 * DAY


def test_a_chained_pact_leaves_the_dependent_side_with_no_clock(host, enforcer, accounts):
    """
    The whole point. Party B holds no deadline at all, so it cannot be in breach of
    an obligation that has not begun.
    """
    accepted = active_pact(
        host, enforcer, accounts, dependency="b_after_a", window_a=3 * DAY, window_b=5 * DAY
    )["accepted"]

    assert accepted["a"]["state"] == "OPEN"
    assert accepted["a"]["deadline_at"] == T0 + 3 * DAY
    assert accepted["b"]["state"] == "WAITING"
    assert accepted["b"]["deadline_at"] == 0
    assert accepted["b"]["clock_started"] is False

    ob_b = json.loads(enforcer.get_obligation("pact-1", "B"))
    assert ob_b["depends_on"] == "A"
    assert ob_b["seconds_remaining"] == 0


def test_the_dependency_direction_reverses_cleanly(host, enforcer, accounts):
    accepted = active_pact(host, enforcer, accounts, dependency="a_after_b")["accepted"]
    assert accepted["a"]["state"] == "WAITING"
    assert accepted["b"]["state"] == "OPEN"
    assert json.loads(enforcer.get_obligation("pact-1", "A"))["depends_on"] == "B"
    assert json.loads(enforcer.get_obligation("pact-1", "B"))["depends_on"] == ""


# ---------------------------------------------------------------------------
# The refusal that makes the declaration mean something
# ---------------------------------------------------------------------------

def test_a_waiting_side_cannot_submit_evidence(host, enforcer, accounts):
    """
    Without this refusal, declaring a dependency would be a comment rather than a
    rule. Party B's work genuinely cannot start until party A delivers.
    """
    chained(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))

    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="B")
    message = revert_message(excinfo)
    assert "has not started" in message
    assert "depends on party A" in message

    # Nothing was adjudicated, so no attempt was spent.
    assert json.loads(enforcer.get_obligation("pact-1", "B"))["attempts_used"] == 0


def test_the_independent_side_may_submit_immediately(host, enforcer, accounts):
    chained(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    assert submit(enforcer, accounts, side="A")["passed"] is True


# ---------------------------------------------------------------------------
# The clock starts on confirmation
# ---------------------------------------------------------------------------

def test_confirming_the_predecessor_starts_the_dependent_clock_in_the_same_transaction(host, enforcer, accounts):
    """
    The activation is written by the transaction that confirms the fulfilment, so the
    moment party B's obligation becomes live is a consensus fact rather than anyone's
    assertion.
    """
    chained(host, enforcer, accounts, window_b=5 * DAY)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))

    host.warp(T0 + 2 * DAY)
    out = submit(enforcer, accounts, side="A")

    assert out["counterparty_clock_started"] is True
    assert out["state_b"] == "OPEN"

    ob_b = json.loads(enforcer.get_obligation("pact-1", "B"))
    assert ob_b["activated_at"] == T0 + 2 * DAY
    # Five days from the confirmation, not five days from acceptance. Party B gets
    # the window it was promised, measured from when its work could actually begin.
    assert ob_b["deadline_at"] == T0 + 2 * DAY + 5 * DAY


def test_a_failed_submission_does_not_start_the_dependent_clock(host, enforcer, accounts):
    """Only a confirmed fulfilment activates the next obligation."""
    chained(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(TWO_OF_THREE))

    out = submit(enforcer, accounts, side="A")
    assert out["passed"] is False
    assert out["counterparty_clock_started"] is False
    assert out["state_b"] == "WAITING"


def test_the_dependent_side_can_submit_once_its_clock_runs(host, enforcer, accounts):
    chained(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))

    submit(enforcer, accounts, side="A")
    out_b = submit(enforcer, accounts, side="B")
    assert out_b["passed"] is True
    assert out_b["state_a"] == "FULFILLED"
    assert out_b["state_b"] == "FULFILLED"


def test_the_dependent_clock_is_started_once_and_never_extended(host, enforcer, accounts):
    """
    A challenge can re-confirm a fulfilment that was already confirmed. Starting the
    clock again would silently extend the counterparty's deadline, so activation is
    idempotent.
    """
    chained(host, enforcer, accounts, options=default_options(challenge_window=2 * DAY))
    serve_scoring(host, scoring_reply(ALL_PRESENT_3, substantiated=False))

    submit(enforcer, accounts, side="A")
    first_deadline = json.loads(enforcer.get_obligation("pact-1", "B"))["deadline_at"]

    host.warp(T0 + DAY)
    enforcer.challenge_fulfilment(
        "pact-1", "A",
        "The schema document is not actually linked from the page, only mentioned.",
        sender=accounts["bob"],
    )
    assert json.loads(enforcer.get_obligation("pact-1", "B"))["deadline_at"] == first_deadline


# ---------------------------------------------------------------------------
# Excusal: the predecessor breached
# ---------------------------------------------------------------------------

def test_a_dependent_side_is_excused_when_its_predecessor_breaches_by_deadline(host, enforcer, accounts):
    """
    Party A let its deadline pass, so party B's obligation never became live. Party B
    is excused, not in breach, and that is derived from the clock without anyone
    sending a transaction to say so.
    """
    chained(host, enforcer, accounts, window_a=3 * DAY)
    host.warp(T0 + 3 * DAY + 1)

    pact = json.loads(enforcer.get_pact("pact-1"))
    assert pact["state_a"] == "BREACHED"
    assert pact["state_b"] == "EXCUSED"
    assert enforcer.obligation_state("pact-1", "B") == "EXCUSED"


def test_an_excused_side_cannot_and_need_not_submit(host, enforcer, accounts):
    chained(host, enforcer, accounts, window_a=3 * DAY)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    host.warp(T0 + 3 * DAY + 1)

    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="B")
    message = revert_message(excinfo)
    assert "excused" in message
    assert "nothing to perform" in message


def test_an_excused_side_forfeits_nothing_and_collects_the_predecessor_forfeit(host, enforcer, accounts):
    """
    The settlement consequence of the chain, and the reason the feature is worth
    having. Party B locked capital against a promise party A never performed, so B is
    made whole and paid A's forfeit as damages.
    """
    chained(host, enforcer, accounts, window_a=3 * DAY, stake_a=10 * ONE, stake_b=4 * ONE)
    host.warp(T0 + 3 * DAY + 1)

    settled = resolve(enforcer, accounts)
    assert settled["outcome"] == "A_BREACH"
    assert settled["state_a"] == "BREACHED"
    assert settled["state_b"] == "EXCUSED"
    assert settled["credit_a"] == 0
    assert settled["credit_b"] == 100

    # Party A scored nothing, so it forfeits its whole stake to party B.
    assert settled["forfeit_a_wei"] == str(10 * ONE)
    assert settled["forfeit_b_wei"] == "0"
    assert settled["payout_a_wei"] == "0"
    assert settled["payout_b_wei"] == str(14 * ONE)
    assert settled["conserves_value"] is True

    paid = transfers(host)
    assert paid[accounts["bob"]] == 14 * ONE
    assert accounts["alice"] not in paid


def test_a_predecessor_that_partly_delivered_forfeits_only_its_shortfall(host, enforcer, accounts):
    """
    Partial credit survives the chain. Party A delivered two of three deliverables
    and ran out of attempts, so it forfeits half its stake rather than all of it,
    and party B is still excused and still whole.
    """
    chained(
        host, enforcer, accounts,
        stake_a=10 * ONE, stake_b=10 * ONE,
        options=default_options(max_attempts=1),
    )
    serve_scoring(host, scoring_reply(TWO_OF_THREE))

    out = submit(enforcer, accounts, side="A")
    assert out["tier"] == "PARTIAL" and out["credit"] == 50

    # Attempts exhausted below the threshold, so party A is terminal now rather than
    # at its deadline, and party B is excused.
    assert out["state_a"] == "BREACHED"
    assert out["state_b"] == "EXCUSED"
    assert out["resolvable"] is True

    settled = resolve(enforcer, accounts)
    expected = settle_of(10 * ONE, 10 * ONE, eff_a=50, eff_b=100)
    assert settled["forfeit_a_wei"] == str(expected["forfeit_a"]) == str(5 * ONE)
    assert settled["payout_a_wei"] == str(expected["payout_a"]) == str(5 * ONE)
    assert settled["payout_b_wei"] == str(expected["payout_b"]) == str(15 * ONE)
    assert settled["conserves_value"] is True


def test_a_chained_pact_can_never_reach_mutual_breach_through_the_chain(host, enforcer, accounts):
    """
    A structural property worth stating. When the predecessor breaches, the dependent
    side is excused rather than in breach, so the chain itself cannot produce a
    mutual breach. Mutual breach needs both obligations to have been live.
    """
    chained(host, enforcer, accounts, window_a=2 * DAY)
    host.warp(T0 + 10 * DAY)

    settled = resolve(enforcer, accounts)
    assert settled["outcome"] == "A_BREACH"
    assert settled["state_b"] == "EXCUSED"


def test_a_chained_pact_can_still_reach_mutual_breach_after_activation(host, enforcer, accounts):
    """
    Once the dependent clock has started, the dependent side is a normal obligation
    and can breach like any other. Party A delivered only partly within one attempt
    and party B then let its own clock run out.
    """
    chained(
        host, enforcer, accounts,
        window_a=3 * DAY, window_b=3 * DAY,
        stake_a=10 * ONE, stake_b=10 * ONE,
        options=default_options(max_attempts=2),
    )
    # Party A clears the threshold, which starts party B's clock.
    host.mock_llm(
        rf"deciding whether party A\s+of a two party pact performed",
        scoring_reply(ALL_PRESENT_3),
    )
    submit(enforcer, accounts, side="A")
    assert enforcer.obligation_state("pact-1", "B") == "OPEN"

    # Party B submits weak evidence twice and runs out of attempts. Each attempt needs
    # a distinct URL, because replay protection refuses the same page twice.
    serve_scoring(host, scoring_reply(ONE_OF_THREE), side="B")
    serve_evidence(host, "https://evidence.example/report-v1", BODY_B_COMPLETE)
    serve_evidence(host, "https://evidence.example/report-v2", BODY_B_COMPLETE)

    submit(enforcer, accounts, side="B", url="https://evidence.example/report-v1")
    pact = json.loads(enforcer.get_pact("pact-1"))
    assert pact["state_a"] == "FULFILLED"
    assert pact["state_b"] == "OPEN"

    submit(enforcer, accounts, side="B", url="https://evidence.example/report-v2")

    settled = resolve(enforcer, accounts)
    assert settled["outcome"] == "B_BREACH"
    assert settled["credit_a"] == 100
    assert settled["credit_b"] == 20


# ---------------------------------------------------------------------------
# Resolvability through the chain
# ---------------------------------------------------------------------------

def test_a_chained_pact_is_not_resolvable_while_the_predecessor_is_open(host, enforcer, accounts):
    chained(host, enforcer, accounts, window_a=5 * DAY)
    status = json.loads(enforcer.is_resolvable("pact-1"))
    assert status["resolvable"] is False
    assert status["blocked_by"] == "Party A is still OPEN"

    with pytest.raises(Exception) as excinfo:
        resolve(enforcer, accounts)
    assert "cannot be resolved yet" in revert_message(excinfo)


def test_a_chained_pact_is_not_resolvable_while_the_dependent_side_is_waiting(host, enforcer, accounts):
    """
    Party A is terminal but party B has not been activated and has not been excused,
    which cannot happen through the normal path and is guarded anyway.
    """
    chained(host, enforcer, accounts, window_a=5 * DAY, window_b=5 * DAY)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")

    status = json.loads(enforcer.is_resolvable("pact-1"))
    assert status["resolvable"] is False
    assert status["blocked_by"] == "Party B is still OPEN"


def test_excusal_makes_a_chained_pact_resolvable_without_waiting_for_a_second_deadline(host, enforcer, accounts):
    """
    Party B's deadline would never arrive, because its clock never started. Waiting
    for it would strand both stakes forever, so excusal is terminal immediately.
    """
    chained(host, enforcer, accounts, window_a=2 * DAY, window_b=300 * DAY)
    host.warp(T0 + 2 * DAY + 1)

    status = json.loads(enforcer.is_resolvable("pact-1"))
    assert status["resolvable"] is True
    assert resolve(enforcer, accounts)["outcome"] == "A_BREACH"


def test_the_settlement_preview_shows_the_chained_outcome_before_it_is_reachable(host, enforcer, accounts):
    """
    A party watching a chained pact needs to see where it stands while the clock is
    still running, and the preview runs exactly the arithmetic the settlement runs.
    """
    chained(host, enforcer, accounts, window_a=5 * DAY, stake_a=10 * ONE, stake_b=4 * ONE)

    preview = json.loads(enforcer.settlement_preview("pact-1"))
    assert preview["hypothetical"] is True
    assert preview["resolvable_now"] is False
    assert preview["state_a"] == "OPEN"
    assert preview["state_b"] == "WAITING"

    host.warp(T0 + 5 * DAY + 1)
    preview = json.loads(enforcer.settlement_preview("pact-1"))
    assert preview["resolvable_now"] is True
    assert preview["outcome"] == "A_BREACH"
    assert preview["state_b"] == "EXCUSED"
    assert preview["payout_b_wei"] == str(14 * ONE)
    # A preview moves nothing.
    assert transfers(host) == {}
