"""
Settlement: the four outcomes, the arithmetic, and the conservation invariant.

One formula covers everything:

    effective = 100 if the side is FULFILLED or EXCUSED else the confirmed credit
    forfeit   = stake * (100 - effective) / 100
    payout    = stake - own forfeit + the other side's forfeit

Expected values here come from `settle_of` in conftest, an independent
reimplementation, so these tests check the contract's arithmetic rather than
restating it.

The invariant that makes the contract safe to hold both stakes is that payouts plus
sink always equal stakes in. It is swept across the reachable space at the end of
this file rather than asserted once on a happy path.
"""

import itertools

import pytest

from conftest import *  # noqa: F403


def settle_with(host, enforcer, accounts, verdicts_a, verdicts_b, **kwargs):
    """
    Drive a pact to settlement with a chosen reading for each side.

    `None` means that side never submits, which is how a deadline breach with no
    evidence at all is reached.
    """
    kwargs.setdefault("window_a", 3 * DAY)
    kwargs.setdefault("window_b", 3 * DAY)
    active_pact(host, enforcer, accounts, **kwargs)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_evidence(host, EVIDENCE_B, BODY_B_COMPLETE)

    if verdicts_a is not None:
        serve_scoring(host, scoring_reply(verdicts_a), side="A")
        submit(enforcer, accounts, side="A")
    if verdicts_b is not None:
        serve_scoring(host, scoring_reply(verdicts_b), side="B")
        submit(enforcer, accounts, side="B")

    # Past both deadlines, so any side that did not clear the threshold is terminal.
    host.warp(T0 + 4 * DAY)
    return resolve(enforcer, accounts)


# ---------------------------------------------------------------------------
# The four outcomes
# ---------------------------------------------------------------------------

def test_mutual_fulfilment_returns_each_stake_in_full(host, enforcer, accounts):
    settled = settle_with(
        host, enforcer, accounts, ALL_PRESENT_3, ALL_PRESENT_3,
        stake_a=10 * ONE, stake_b=4 * ONE,
    )
    assert settled["outcome"] == "MUTUAL_FULFILMENT"
    assert settled["forfeit_a_wei"] == "0" and settled["forfeit_b_wei"] == "0"
    assert settled["payout_a_wei"] == str(10 * ONE)
    assert settled["payout_b_wei"] == str(4 * ONE)
    assert settled["summary"] == "Both parties fulfilled pact 'pact-1'. Each stake returned in full."

    paid = transfers(host)
    assert paid[accounts["alice"]] == 10 * ONE
    assert paid[accounts["bob"]] == 4 * ONE


def test_passing_at_substantial_still_forfeits_nothing(host, enforcer, accounts):
    """
    A side that cleared the threshold forfeits nothing regardless of the tier that
    cleared it. Docking it twenty percent would punish performance the pact itself
    declared acceptable.
    """
    settled = settle_with(
        host, enforcer, accounts,
        ["PRESENT", "PRESENT", "PARTIAL"], ALL_PRESENT_3,
        stake_a=10 * ONE, stake_b=10 * ONE,
    )
    assert settled["outcome"] == "MUTUAL_FULFILMENT"
    assert settled["scored_credit_a"] == 80
    assert settled["credit_a"] == 100  # effective, because it passed
    assert settled["payout_a_wei"] == str(10 * ONE)


def test_a_breach_with_no_evidence_forfeits_the_whole_stake(host, enforcer, accounts):
    settled = settle_with(
        host, enforcer, accounts, None, ALL_PRESENT_3,
        stake_a=10 * ONE, stake_b=4 * ONE,
    )
    assert settled["outcome"] == "A_BREACH"
    assert settled["state_a"] == "BREACHED"
    assert settled["credit_a"] == 0
    assert settled["payout_a_wei"] == "0"
    assert settled["payout_b_wei"] == str(14 * ONE)
    assert "forfeiting 10000000000000000000 wei of its stake to party B" in settled["summary"]


def test_b_breach_is_the_exact_mirror(host, enforcer, accounts):
    settled = settle_with(
        host, enforcer, accounts, ALL_PRESENT_3, None,
        stake_a=4 * ONE, stake_b=10 * ONE,
    )
    assert settled["outcome"] == "B_BREACH"
    assert settled["payout_a_wei"] == str(14 * ONE)
    assert settled["payout_b_wei"] == "0"


@pytest.mark.parametrize(
    "verdicts,credit",
    [(["PRESENT", "PRESENT", "PARTIAL"], 80), (TWO_OF_THREE, 50), (ONE_OF_THREE, 20), (NONE_OF_THREE, 0)],
)
def test_partial_credit_scales_the_forfeit(host, enforcer, accounts, verdicts, credit):
    """
    The whole reason partial credit exists. A side that delivered most of the work
    forfeits proportionally rather than everything, which is what the brief asks for
    and what makes a near miss survivable.
    """
    threshold = 100  # so that only COMPLETE passes and every reading here is a breach
    settled = settle_with(
        host, enforcer, accounts, verdicts, ALL_PRESENT_3,
        stake_a=10 * ONE, stake_b=10 * ONE,
        options=default_options(pass_threshold=threshold),
    )
    expected = settle_of(10 * ONE, 10 * ONE, eff_a=credit, eff_b=100)
    assert settled["scored_credit_a"] == credit
    assert settled["forfeit_a_wei"] == str(expected["forfeit_a"])
    assert settled["payout_a_wei"] == str(expected["payout_a"])
    assert settled["payout_b_wei"] == str(expected["payout_b"])
    assert settled["conserves_value"] is True


def test_mutual_breach_crosses_the_forfeits_by_default(host, enforcer, accounts):
    """
    Two sides that both did nothing get their money back when the stakes are equal.
    That is the honest result of a symmetric mutual breach and where contract law
    lands too, and it is documented rather than accidental.
    """
    settled = settle_with(
        host, enforcer, accounts, None, None,
        stake_a=10 * ONE, stake_b=10 * ONE,
    )
    assert settled["outcome"] == "MUTUAL_BREACH"
    assert settled["forfeit_a_wei"] == str(10 * ONE)
    assert settled["forfeit_b_wei"] == str(10 * ONE)
    assert settled["payout_a_wei"] == str(10 * ONE)
    assert settled["payout_b_wei"] == str(10 * ONE)
    assert settled["sink_wei"] == "0"
    assert "across to the other party" in settled["summary"]


def test_mutual_breach_with_unequal_stakes_is_not_neutral(host, enforcer, accounts):
    """
    The lever a party actually has against mutual breach neutrality. Staking less
    than the counterparty makes a symmetric non performance profitable for the
    smaller staker, so stake asymmetry prices the risk.
    """
    settled = settle_with(
        host, enforcer, accounts, None, None,
        stake_a=10 * ONE, stake_b=2 * ONE,
    )
    assert settled["outcome"] == "MUTUAL_BREACH"
    assert settled["payout_a_wei"] == str(2 * ONE)
    assert settled["payout_b_wei"] == str(10 * ONE)
    assert settled["conserves_value"] is True


def test_the_sink_only_fires_on_mutual_breach(host, enforcer, accounts):
    """A single breach pays the performing counterparty, never the sink."""
    options = default_options(mutual_breach_sink=accounts["sink"])

    settled = settle_with(
        host, enforcer, accounts, None, ALL_PRESENT_3,
        stake_a=10 * ONE, stake_b=10 * ONE, options=options,
    )
    assert settled["outcome"] == "A_BREACH"
    assert settled["sink_wei"] == "0"
    assert settled["payout_b_wei"] == str(20 * ONE)
    assert accounts["sink"] not in transfers(host)


def test_a_declared_sink_collects_both_shortfalls_on_mutual_breach(host, enforcer, accounts):
    settled = settle_with(
        host, enforcer, accounts, None, None,
        stake_a=10 * ONE, stake_b=6 * ONE,
        options=default_options(mutual_breach_sink=accounts["sink"]),
    )
    expected = settle_of(10 * ONE, 6 * ONE, eff_a=0, eff_b=0, sink=True)
    assert settled["outcome"] == "MUTUAL_BREACH"
    assert settled["sink_wei"] == str(expected["sink"]) == str(16 * ONE)
    assert settled["payout_a_wei"] == "0"
    assert settled["payout_b_wei"] == "0"
    assert settled["sink_address"] == accounts["sink"]
    assert settled["conserves_value"] is True
    assert transfers(host)[accounts["sink"]] == 16 * ONE


def test_a_partial_mutual_breach_with_a_sink_still_returns_the_earned_share(host, enforcer, accounts):
    """
    The sink takes the shortfalls, not the stakes. Both sides keep what they earned.
    """
    settled = settle_with(
        host, enforcer, accounts, TWO_OF_THREE, ONE_OF_THREE,
        stake_a=10 * ONE, stake_b=10 * ONE,
        options=default_options(mutual_breach_sink=accounts["sink"]),
    )
    expected = settle_of(10 * ONE, 10 * ONE, eff_a=50, eff_b=20, sink=True)
    assert settled["outcome"] == "MUTUAL_BREACH"
    assert settled["payout_a_wei"] == str(expected["payout_a"]) == str(5 * ONE)
    assert settled["payout_b_wei"] == str(expected["payout_b"]) == str(2 * ONE)
    assert settled["sink_wei"] == str(expected["sink"]) == str(13 * ONE)
    assert settled["conserves_value"] is True


# ---------------------------------------------------------------------------
# Mutual release
# ---------------------------------------------------------------------------

def test_mutual_release_needs_both_parties(host, enforcer, accounts):
    active_pact(host, enforcer, accounts)

    first = json.loads(enforcer.agree_release("pact-1", sender=accounts["alice"]))
    assert first["settled"] is False
    assert first["awaiting"] == "B"
    assert enforcer.release_consent_of("pact-1", "A") is True
    assert enforcer.release_consent_of("pact-1", "B") is False
    assert transfers(host) == {}

    second = json.loads(enforcer.agree_release("pact-1", sender=accounts["bob"]))
    assert second["settled"] is True
    assert second["outcome"] == "RELEASED"


def test_mutual_release_returns_both_stakes_in_full(host, enforcer, accounts):
    active_pact(host, enforcer, accounts, stake_a=7 * ONE, stake_b=3 * ONE)
    enforcer.agree_release("pact-1", sender=accounts["alice"])
    settled = json.loads(enforcer.agree_release("pact-1", sender=accounts["bob"]))

    assert settled["payout_a_wei"] == str(7 * ONE)
    assert settled["payout_b_wei"] == str(3 * ONE)
    assert settled["conserves_value"] is True
    assert "released pact 'pact-1' by agreement" in settled["summary"]
    paid = transfers(host)
    assert paid[accounts["alice"]] == 7 * ONE and paid[accounts["bob"]] == 3 * ONE


def test_release_waives_an_open_challenge_window(host, enforcer, accounts):
    """
    Safe by construction rather than by policy. The only two people with a claim on
    the money are the two parties, and both of them said so.
    """
    active_pact(host, enforcer, accounts, options=default_options(challenge_window=5 * DAY))
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_evidence(host, EVIDENCE_B, BODY_B_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")
    submit(enforcer, accounts, side="B")

    assert json.loads(enforcer.is_resolvable("pact-1"))["resolvable"] is False

    enforcer.agree_release("pact-1", sender=accounts["alice"])
    settled = json.loads(enforcer.agree_release("pact-1", sender=accounts["bob"]))
    assert settled["outcome"] == "RELEASED"


def test_a_side_that_is_owed_money_simply_does_not_consent(host, enforcer, accounts):
    """
    There is no way to use release to escape a breach, because it needs the consent of
    the party that would lose by it.
    """
    active_pact(host, enforcer, accounts, window_a=3 * DAY, window_b=3 * DAY)
    serve_evidence(host, EVIDENCE_B, BODY_B_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3), side="B")
    submit(enforcer, accounts, side="B")
    host.warp(T0 + 4 * DAY)

    # Party A, in breach, consents. Nothing happens without party B.
    assert json.loads(enforcer.agree_release("pact-1", sender=accounts["alice"]))["settled"] is False
    assert transfers(host) == {}

    # Party B resolves normally instead and collects.
    settled = resolve(enforcer, accounts, sender=accounts["bob"])
    assert settled["outcome"] == "A_BREACH"
    assert settled["payout_b_wei"] == str(20 * ONE)


def test_a_party_cannot_consent_to_release_twice(host, enforcer, accounts):
    active_pact(host, enforcer, accounts)
    enforcer.agree_release("pact-1", sender=accounts["alice"])
    with pytest.raises(Exception) as excinfo:
        enforcer.agree_release("pact-1", sender=accounts["alice"])
    assert "already consented" in revert_message(excinfo)


def test_an_outsider_cannot_consent_to_release(host, enforcer, accounts):
    active_pact(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        enforcer.agree_release("pact-1", sender=accounts["carol"])
    assert "not a party to this pact" in revert_message(excinfo)


# ---------------------------------------------------------------------------
# Resolution mechanics
# ---------------------------------------------------------------------------

def test_anyone_may_resolve_a_resolvable_pact(host, enforcer, accounts):
    """
    Settlement is pure arithmetic over consensus bound facts, so there is nothing for
    a caller to influence. Leaving it open means a party cannot strand the
    counterparty's money by never calling.
    """
    settled = settle_with(host, enforcer, accounts, ALL_PRESENT_3, ALL_PRESENT_3)
    assert settled["resolved_by"] == accounts["outsider"]
    assert accounts["outsider"] not in transfers(host)


def test_a_pact_cannot_be_resolved_twice(host, enforcer, accounts):
    settle_with(host, enforcer, accounts, ALL_PRESENT_3, ALL_PRESENT_3)
    with pytest.raises(Exception) as excinfo:
        resolve(enforcer, accounts)
    assert "already settled" in revert_message(excinfo)


def test_a_settled_pact_refuses_further_evidence_challenges_and_release(host, enforcer, accounts):
    settle_with(host, enforcer, accounts, ALL_PRESENT_3, ALL_PRESENT_3)

    for call, args in (
        ("submit_evidence", ("pact-1", "https://evidence.example/late")),
        ("agree_release", ("pact-1",)),
    ):
        with pytest.raises(Exception) as excinfo:
            getattr(enforcer, call)(*args, sender=accounts["alice"])
        assert "is SETTLED, not ACTIVE" in revert_message(excinfo)


def test_resolution_updates_the_registry_counters(host, enforcer, accounts):
    settle_with(host, enforcer, accounts, ALL_PRESENT_3, ALL_PRESENT_3, stake_a=6 * ONE, stake_b=4 * ONE)
    info = json.loads(enforcer.get_registry_info())
    assert info["pacts_settled"] == "1"
    assert info["total_locked_wei"] == "0"
    assert info["total_settled_wei"] == str(10 * ONE)


def test_the_settled_record_matches_what_resolution_returned(host, enforcer, accounts):
    settled = settle_with(host, enforcer, accounts, TWO_OF_THREE, ALL_PRESENT_3, stake_a=10 * ONE, stake_b=10 * ONE)
    record = json.loads(enforcer.get_settlement("pact-1"))
    for field in ("outcome", "summary", "state_a", "state_b", "forfeit_a_wei", "payout_a_wei", "payout_b_wei"):
        assert record[field] == settled[field], field
    assert record["total_out_wei"] == str(20 * ONE)


def test_a_settled_pact_preview_returns_the_settled_record(host, enforcer, accounts):
    settle_with(host, enforcer, accounts, ALL_PRESENT_3, ALL_PRESENT_3)
    preview = json.loads(enforcer.settlement_preview("pact-1"))
    assert preview["outcome"] == "MUTUAL_FULFILMENT"
    assert "hypothetical" not in preview


def test_integer_division_leaves_the_remainder_with_the_side_that_staked_it(host, enforcer, accounts):
    """
    A stake that does not divide evenly must not lose wei. The forfeit floors, so the
    remainder stays with the staking side and conservation still holds exactly.
    """
    odd = 7  # seven wei, so every division has a remainder
    settled = settle_with(
        host, enforcer, accounts, TWO_OF_THREE, ALL_PRESENT_3,
        stake_a=odd, stake_b=odd, options=default_options(pass_threshold=100),
    )
    expected = settle_of(odd, odd, eff_a=50, eff_b=100)
    assert settled["forfeit_a_wei"] == str(expected["forfeit_a"]) == "3"
    assert settled["payout_a_wei"] == str(expected["payout_a"]) == "4"
    assert settled["payout_b_wei"] == str(expected["payout_b"]) == "10"
    assert int(settled["payout_a_wei"]) + int(settled["payout_b_wei"]) == 2 * odd


# ---------------------------------------------------------------------------
# The invariant, swept
# ---------------------------------------------------------------------------

READINGS = {
    "complete": ALL_PRESENT_3,
    "substantial": ["PRESENT", "PRESENT", "PARTIAL"],
    "partial": TWO_OF_THREE,
    "minimal": ONE_OF_THREE,
    "none": NONE_OF_THREE,
    "absent": None,
}


@pytest.mark.parametrize("name_a,name_b", list(itertools.product(READINGS, repeat=2)))
def test_value_is_conserved_across_the_whole_outcome_space(host, enforcer, accounts, name_a, name_b):
    """
    The invariant that makes this contract safe to hold both stakes, swept across
    every combination of readings including no submission at all, with unequal stakes
    so an accidental symmetry cannot hide an error.

    `resolve_pact` also asserts this internally before moving anything, so a
    settlement that would not balance reverts whole rather than paying one side.
    """
    stake_a, stake_b = 11 * ONE, 7 * ONE
    settled = settle_with(
        host, enforcer, accounts, READINGS[name_a], READINGS[name_b],
        stake_a=stake_a, stake_b=stake_b, options=default_options(pass_threshold=100),
    )

    total_in = stake_a + stake_b
    total_out = int(settled["payout_a_wei"]) + int(settled["payout_b_wei"]) + int(settled["sink_wei"])
    assert total_out == total_in, settled
    assert settled["conserves_value"] is True

    # And the independent reimplementation reaches the same split.
    expected = settle_of(stake_a, stake_b, settled["credit_a"], settled["credit_b"])
    assert settled["payout_a_wei"] == str(expected["payout_a"])
    assert settled["payout_b_wei"] == str(expected["payout_b"])


@pytest.mark.parametrize("name_a,name_b", list(itertools.product(READINGS, repeat=2)))
def test_value_is_conserved_with_a_sink_declared(host, enforcer, accounts, name_a, name_b):
    """The same sweep with a sink, which is the branch that moves money out of the pair."""
    stake_a, stake_b = 11 * ONE, 7 * ONE
    settled = settle_with(
        host, enforcer, accounts, READINGS[name_a], READINGS[name_b],
        stake_a=stake_a, stake_b=stake_b,
        options=default_options(pass_threshold=100, mutual_breach_sink=accounts["sink"]),
    )
    total_out = int(settled["payout_a_wei"]) + int(settled["payout_b_wei"]) + int(settled["sink_wei"])
    assert total_out == stake_a + stake_b, settled

    expected = settle_of(stake_a, stake_b, settled["credit_a"], settled["credit_b"], sink=True)
    assert settled["sink_wei"] == str(expected["sink"])

    # Whatever was queued for transfer must also add up to the stakes in.
    assert sum(transfers(host).values()) == stake_a + stake_b
