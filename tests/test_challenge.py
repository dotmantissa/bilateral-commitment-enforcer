"""
The challenge path.

Resubmission lets a party that fell short argue its own case with better evidence.
A challenge is the other direction: the party that stands to lose money because the
counterparty was credited gets one chance to say the evidence does not support the
credit, and to say why.

A frivolous challenge has to cost something or it is free griefing. It costs the
challenger its single challenge, because the revised score is adopted only when the
panel finds the grounds substantiated.
"""

import pytest

from conftest import *  # noqa: F403
from genvm_host import ConsensusFailure


GROUNDS = (
    "The page mentions a schema document and a checksum but neither is actually "
    "published on it. Only the CSV is reachable."
)


def confirmed(host, enforcer, accounts, window=2 * DAY, verdicts=None, **kwargs):
    """A pact where party A's fulfilment is confirmed and still challengeable."""
    options = kwargs.pop("options", None) or default_options(challenge_window=window)
    active_pact(host, enforcer, accounts, options=options, **kwargs)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_evidence(host, EVIDENCE_B, BODY_B_COMPLETE)
    serve_scoring(host, scoring_reply(verdicts or ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")
    host.clear_routes()


def challenge(enforcer, accounts, side="A", grounds=GROUNDS, sender=None):
    caller = sender or (accounts["bob"] if side == "A" else accounts["alice"])
    return json.loads(enforcer.challenge_fulfilment("pact-1", side, grounds, sender=caller))


# ---------------------------------------------------------------------------
# A substantiated challenge
# ---------------------------------------------------------------------------

def test_a_substantiated_challenge_replaces_the_credit_and_withdraws_the_fulfilment(host, enforcer, accounts):
    confirmed(host, enforcer, accounts)
    assert enforcer.obligation_state("pact-1", "A") == "FULFILLED"

    serve_evidence(host, EVIDENCE_A, BODY_A_PARTIAL)
    serve_scoring(host, scoring_reply(ONE_OF_THREE, substantiated=True))

    out = challenge(enforcer, accounts)
    assert out["substantiated"] is True
    assert out["revision_adopted"] is True
    assert out["original_tier"] == "COMPLETE" and out["original_credit"] == 100
    assert out["revised_tier"] == "MINIMAL" and out["revised_credit"] == 20
    assert out["final_credit"] == 20
    assert out["fulfilment_withdrawn"] is True

    # Party A still has time and an attempt, so it returns to OPEN and can cure the
    # defect the challenge identified. A challenge is a way to correct a pact, not a
    # way to end one.
    assert enforcer.obligation_state("pact-1", "A") == "OPEN"
    assert out["resolvable"] is False


def test_an_overturned_side_may_cure_the_defect_the_challenge_identified(host, enforcer, accounts):
    """The same courtesy the resubmission path extends to a side that fell short."""
    confirmed(host, enforcer, accounts, options=default_options(challenge_window=2 * DAY, max_attempts=3))
    serve_evidence(host, EVIDENCE_A, BODY_A_PARTIAL)
    serve_scoring(host, scoring_reply(ONE_OF_THREE, substantiated=True))
    challenge(enforcer, accounts)
    assert enforcer.obligation_state("pact-1", "A") == "OPEN"

    host.clear_routes()
    serve_evidence(host, "https://evidence.example/dataset-q3-fixed", BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    out = submit(enforcer, accounts, side="A", url="https://evidence.example/dataset-q3-fixed")
    assert out["passed"] is True
    assert enforcer.obligation_state("pact-1", "A") == "FULFILLED"


def test_an_overturned_side_with_nothing_left_goes_straight_to_breached(host, enforcer, accounts):
    """
    Out of attempts, so there is no route back to fulfilment and the side is terminal
    immediately rather than at a deadline whose outcome is already fixed.
    """
    confirmed(host, enforcer, accounts, options=default_options(challenge_window=2 * DAY, max_attempts=2))
    serve_evidence(host, EVIDENCE_A, BODY_A_PARTIAL)
    serve_scoring(host, scoring_reply(NONE_OF_THREE, substantiated=True))

    out = challenge(enforcer, accounts)
    assert out["fulfilment_withdrawn"] is True
    assert enforcer.obligation_state("pact-1", "A") == "BREACHED"


def test_a_substantiated_challenge_that_still_clears_the_threshold_keeps_the_fulfilment(host, enforcer, accounts):
    """
    A challenge can be right about a gap without the gap being enough to fail the
    pact. The credit drops, the confirmation stands.
    """
    confirmed(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(["PRESENT", "PRESENT", "PARTIAL"], substantiated=True))

    out = challenge(enforcer, accounts)
    assert out["revision_adopted"] is True
    assert out["revised_tier"] == "SUBSTANTIAL"
    assert out["fulfilment_withdrawn"] is False
    assert enforcer.obligation_state("pact-1", "A") == "FULFILLED"


def test_a_withdrawn_fulfilment_changes_the_settlement(host, enforcer, accounts):
    """The point of the mechanism: the challenge has to be able to move money."""
    confirmed(host, enforcer, accounts, stake_a=10 * ONE, stake_b=10 * ONE, window_a=3 * DAY, window_b=3 * DAY)
    serve_evidence(host, EVIDENCE_A, BODY_A_PARTIAL)
    serve_evidence(host, EVIDENCE_B, BODY_B_COMPLETE)
    serve_scoring(host, scoring_reply(NONE_OF_THREE, substantiated=True), side="A")
    challenge(enforcer, accounts)

    serve_scoring(host, scoring_reply(ALL_PRESENT_3), side="B")
    submit(enforcer, accounts, side="B")

    host.warp(T0 + 4 * DAY)
    settled = resolve(enforcer, accounts)
    assert settled["outcome"] == "A_BREACH"
    assert settled["credit_a"] == 0
    assert settled["payout_b_wei"] == str(20 * ONE)


# ---------------------------------------------------------------------------
# An unsubstantiated challenge
# ---------------------------------------------------------------------------

def test_an_unsubstantiated_challenge_leaves_the_original_credit_standing(host, enforcer, accounts):
    """
    This is what makes a frivolous challenge cost something without needing a bond.
    The panel re-scored lower, but it did not find the grounds substantiated, so the
    revision is not adopted and the challenger has spent its single challenge.
    """
    confirmed(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ONE_OF_THREE, substantiated=False))

    out = challenge(enforcer, accounts)
    assert out["substantiated"] is False
    assert out["revision_adopted"] is False
    assert out["revised_credit"] == 20          # what this panel read
    assert out["final_credit"] == 100           # what stands
    assert out["fulfilment_withdrawn"] is False
    assert "not substantiated" in out["outcome_note"]
    assert enforcer.obligation_state("pact-1", "A") == "FULFILLED"

    ob = json.loads(enforcer.get_obligation("pact-1", "A"))
    assert ob["best_credit"] == 100
    assert ob["challenge_used"] is True
    assert ob["challenge_substantiated"] is False


def test_the_challenge_is_spent_either_way(host, enforcer, accounts):
    confirmed(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ONE_OF_THREE, substantiated=False))
    challenge(enforcer, accounts)

    with pytest.raises(Exception) as excinfo:
        challenge(enforcer, accounts, grounds="On reflection, here is a second and better argument about the schema.")
    assert "already been challenged once" in revert_message(excinfo)


def test_a_spent_challenge_closes_the_window_and_unblocks_resolution(host, enforcer, accounts):
    confirmed(host, enforcer, accounts, window=10 * DAY, window_a=3 * DAY, window_b=3 * DAY)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_evidence(host, EVIDENCE_B, BODY_B_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3, substantiated=False), side="A")
    serve_scoring(host, scoring_reply(ALL_PRESENT_3), side="B")
    submit(enforcer, accounts, side="B")

    # Party B's own fulfilment is still challengeable by party A, so resolution waits.
    assert json.loads(enforcer.is_resolvable("pact-1"))["resolvable"] is False
    challenge(enforcer, accounts, side="A")

    status = json.loads(enforcer.is_resolvable("pact-1"))
    assert status["blocked_by"] == "Party B's fulfilment is still open to challenge"

    host.warp(T0 + 11 * DAY)
    assert json.loads(enforcer.is_resolvable("pact-1"))["resolvable"] is True


# ---------------------------------------------------------------------------
# Consensus on a challenge
# ---------------------------------------------------------------------------

def test_the_substantiated_boolean_is_consensus_bound(host, enforcer, accounts):
    """
    The field that decides whether the revision is adopted, so it is bound outright.
    Here both panels reach the same tier and differ only on the grounds.
    """
    confirmed(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_PARTIAL)
    host.mock_llm(
        SCORING_PATTERN,
        [
            scoring_reply(ONE_OF_THREE, substantiated=True),
            scoring_reply(ONE_OF_THREE, substantiated=False),
        ],
    )
    with pytest.raises(ConsensusFailure):
        challenge(enforcer, accounts)

    # Nothing changed, so the challenge is not spent either.
    ob = json.loads(enforcer.get_obligation("pact-1", "A"))
    assert ob["challenge_used"] is False
    assert ob["best_credit"] == 100


def test_the_revised_tier_is_consensus_bound_too(host, enforcer, accounts):
    confirmed(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_PARTIAL)
    host.mock_llm(
        SCORING_PATTERN,
        [
            scoring_reply(NONE_OF_THREE, substantiated=True),
            scoring_reply(ALL_PRESENT_3, substantiated=True),
        ],
    )
    with pytest.raises(ConsensusFailure):
        challenge(enforcer, accounts)


def test_the_challenge_panel_really_reruns_the_whole_reading(host, enforcer, accounts):
    confirmed(host, enforcer, accounts)
    host.web_calls.clear()
    host.rendered.clear()
    host.llm_calls.clear()

    serve_evidence(host, EVIDENCE_A, BODY_A_PARTIAL)
    serve_scoring(host, scoring_reply(ONE_OF_THREE, substantiated=True))
    challenge(enforcer, accounts)

    assert len(host.rendered) == 2
    assert len(host.llm_calls) == 2
    # And the grounds reached the panel, which is what it is being asked to examine.
    assert all("THIS IS A CHALLENGE" in call["prompt"] for call in host.llm_calls)
    assert all(GROUNDS in call["prompt"] for call in host.llm_calls)


def test_the_challenge_re_examines_the_original_evidence(host, enforcer, accounts):
    """
    A challenge is about the evidence that was accepted, not about new evidence. The
    URL on record is what is refetched.
    """
    confirmed(host, enforcer, accounts)
    host.rendered.clear()
    serve_evidence(host, EVIDENCE_A, BODY_A_PARTIAL)
    serve_scoring(host, scoring_reply(ONE_OF_THREE, substantiated=True))
    challenge(enforcer, accounts)
    assert set(host.rendered) == {EVIDENCE_A}

    record = json.loads(enforcer.get_adjudication("pact-1", "A", 2))
    assert record["kind"] == "CHALLENGE"
    assert record["evidence_url"] == EVIDENCE_A
    assert record["grounds"] == GROUNDS


# ---------------------------------------------------------------------------
# Access control and windows
# ---------------------------------------------------------------------------

def test_a_party_cannot_challenge_its_own_fulfilment(host, enforcer, accounts):
    confirmed(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        challenge(enforcer, accounts, sender=accounts["alice"])
    assert "cannot challenge its own fulfilment" in revert_message(excinfo)


def test_an_outsider_cannot_challenge(host, enforcer, accounts):
    confirmed(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        challenge(enforcer, accounts, sender=accounts["carol"])
    assert "not a party to this pact" in revert_message(excinfo)


def test_only_a_confirmed_fulfilment_can_be_challenged(host, enforcer, accounts):
    active_pact(host, enforcer, accounts, options=default_options(challenge_window=2 * DAY))
    with pytest.raises(Exception) as excinfo:
        challenge(enforcer, accounts, side="A")
    assert "Party A is OPEN, and only a confirmed fulfilment can be challenged" in revert_message(excinfo)


def test_a_challenge_after_the_window_closes_is_refused(host, enforcer, accounts):
    confirmed(host, enforcer, accounts, window=2 * DAY)
    host.warp(T0 + 2 * DAY + 1)
    serve_evidence(host, EVIDENCE_A, BODY_A_PARTIAL)
    serve_scoring(host, scoring_reply(ONE_OF_THREE, substantiated=True))

    with pytest.raises(Exception) as excinfo:
        challenge(enforcer, accounts)
    assert "challenge window for party A closed" in revert_message(excinfo)


def test_a_challenge_on_the_closing_instant_is_allowed(host, enforcer, accounts):
    confirmed(host, enforcer, accounts, window=2 * DAY)
    host.warp(T0 + 2 * DAY)
    serve_evidence(host, EVIDENCE_A, BODY_A_PARTIAL)
    serve_scoring(host, scoring_reply(ONE_OF_THREE, substantiated=True))
    assert challenge(enforcer, accounts)["revision_adopted"] is True


def test_a_pact_with_no_challenge_window_makes_fulfilments_final(host, enforcer, accounts):
    """
    The right default for parties that do not want a dispute period. The pact settles
    the instant both sides are terminal.
    """
    confirmed(host, enforcer, accounts, window=0)
    with pytest.raises(Exception) as excinfo:
        challenge(enforcer, accounts)
    assert "declared no challenge window" in revert_message(excinfo)

    ob = json.loads(enforcer.get_obligation("pact-1", "A"))
    assert ob["challenge_open"] is False


def test_an_open_challenge_window_blocks_resolution(host, enforcer, accounts):
    """
    Paying out against a fulfilment that can still be overturned would make the
    challenge path meaningless.
    """
    confirmed(host, enforcer, accounts, window=3 * DAY, window_a=2 * DAY, window_b=2 * DAY)
    serve_evidence(host, EVIDENCE_B, BODY_B_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3), side="B")
    submit(enforcer, accounts, side="B")

    status = json.loads(enforcer.is_resolvable("pact-1"))
    assert status["resolvable"] is False
    assert "still open to challenge" in status["blocked_by"]

    host.warp(T0 + 4 * DAY)
    assert json.loads(enforcer.is_resolvable("pact-1"))["resolvable"] is True
    assert resolve(enforcer, accounts)["outcome"] == "MUTUAL_FULFILMENT"


@pytest.mark.parametrize("grounds", ["", "   ", "too short"])
def test_grounds_must_be_substantial_enough_to_examine(host, enforcer, accounts, grounds):
    confirmed(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        challenge(enforcer, accounts, grounds=grounds)
    assert "grounds" in revert_message(excinfo)


def test_a_challenge_consumes_an_attempt_slot_on_the_challenged_side(host, enforcer, accounts):
    """
    The challenge is recorded in the same attempt series, so the adjudication log for a
    side reads as one ordered history rather than two interleaved ones.
    """
    confirmed(host, enforcer, accounts, options=default_options(challenge_window=2 * DAY, max_attempts=3))
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3, substantiated=False))
    challenge(enforcer, accounts)

    log = json.loads(enforcer.list_adjudications("pact-1", "A"))
    assert [e["kind"] for e in log["adjudications"]] == ["SUBMISSION", "CHALLENGE"]
    assert log["attempts_used"] == 2


def test_an_invalid_side_argument_is_refused(host, enforcer, accounts):
    confirmed(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        enforcer.challenge_fulfilment("pact-1", "C", GROUNDS, sender=accounts["bob"])
    assert "side must be 'A' or 'B'" in revert_message(excinfo)
