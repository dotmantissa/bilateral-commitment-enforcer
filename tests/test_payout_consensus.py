"""Exercise real validator closures: compatible results must settle identically."""

from itertools import product

import pytest

from conftest import *  # noqa: F403
from genvm_host import ConsensusFailure


# Every tier, including adjacent failing tiers one rubric point apart.
READINGS = [
    ("NONE", ["ABSENT", "ABSENT", "ABSENT"]),
    ("MINIMAL", ["PARTIAL", "PARTIAL", "ABSENT"]),
    ("PARTIAL", ["PRESENT", "PARTIAL", "ABSENT"]),
    ("SUBSTANTIAL", ["PRESENT", "PRESENT", "PARTIAL"]),
    ("COMPLETE", ALL_PRESENT_3),
]
GROUNDS = "The evidence does not establish all the agreed deliverables."


@pytest.mark.parametrize("reverse", [False, True])
def test_default_pact_rejects_adjacent_failing_tiers(host, enforcer, accounts, reverse):
    """The reviewer's exact case, with the registry default rather than an override."""
    options = default_options()
    del options["tier_tolerance_points"]
    active_pact(host, enforcer, accounts, options=options)
    serve_evidence(host)
    replies = [scoring_reply(READINGS[2][1]), scoring_reply(READINGS[1][1])]
    serve_scoring(host, replies[::-1] if reverse else replies)
    with pytest.raises(ConsensusFailure):
        submit(enforcer, accounts)
    assert json.loads(enforcer.get_obligation("pact-1", "A"))["attempts_used"] == 0


@pytest.mark.parametrize("operation", ["submission", "challenge"])
@pytest.mark.parametrize("tolerance", range(5))
@pytest.mark.parametrize("leader,validator", list(product(READINGS, repeat=2)))
def test_only_identical_tiers_can_be_accepted(
    host, enforcer, accounts, operation, tolerance, leader, validator
):
    """All tier pairs, both leader orders, every supported legacy tolerance."""
    options = default_options(
        pass_threshold=100, tier_tolerance_points=tolerance, challenge_window=DAY
    )
    active_pact(host, enforcer, accounts, options=options)
    serve_evidence(host)
    if operation == "challenge":
        serve_scoring(host, scoring_reply(ALL_PRESENT_3))
        submit(enforcer, accounts)
        host.clear_routes()
        serve_evidence(host)

    before = enforcer.get_obligation("pact-1", "A")
    before_pact = enforcer.get_pact("pact-1")
    before_records = enforcer.list_adjudications("pact-1", "A")
    before_storage = {key: bytes(value) for key, value in host._store.items()}
    serve_scoring(host, [
        scoring_reply(leader[1], substantiated=True),
        scoring_reply(validator[1], substantiated=True),
    ])

    def adjudicate():
        if operation == "challenge":
            return json.loads(enforcer.challenge_fulfilment(
                "pact-1", "A", GROUNDS, sender=accounts["bob"]
            ))
        return submit(enforcer, accounts)

    if leader[0] != validator[0]:
        with pytest.raises(ConsensusFailure):
            adjudicate()
        assert host.nondet_runs[-1]["validator_agreed"] is False
        assert enforcer.get_obligation("pact-1", "A") == before
        assert enforcer.get_pact("pact-1") == before_pact
        assert enforcer.list_adjudications("pact-1", "A") == before_records
        assert {key: bytes(value) for key, value in host._store.items()} == before_storage
        if operation == "submission":
            assert enforcer.evidence_already_used("pact-1", "A", EVIDENCE_A) is False
    else:
        adjudicate()
        assert host.nondet_runs[-1]["validator_agreed"] is True
        obligation = json.loads(enforcer.get_obligation("pact-1", "A"))
        assert obligation["best_tier"] == leader[0]
        assert obligation["best_credit"] == credit_of(leader[0])


@pytest.mark.parametrize("operation", ["submission", "challenge"])
@pytest.mark.parametrize("side", ["A", "B"])
@pytest.mark.parametrize("sink", [False, True])
@pytest.mark.parametrize("leader_points,validator_points", [(3, 3), (3, 4), (4, 3)])
def test_compatible_readings_produce_the_same_actual_payouts(
    host, enforcer, accounts, operation, side, sink, leader_points, validator_points
):
    """Different point totals in PARTIAL must yield the same transfers, even on appeal."""
    options = default_options(
        challenge_window=DAY, max_attempts=2,
        mutual_breach_sink=accounts["sink"] if sink else ZERO,
    )
    active_pact(host, enforcer, accounts, options=options, stake_a=101, stake_b=207)
    url = EVIDENCE_A if side == "A" else EVIDENCE_B
    serve_evidence(host, url)
    if operation == "challenge":
        serve_scoring(host, scoring_reply(ALL_PRESENT_3))
        submit(enforcer, accounts, side=side)
        host.clear_routes()
        serve_evidence(host, url)

    readings = {
        3: ["PRESENT", "PARTIAL", "ABSENT"],
        4: ["PRESENT", "PRESENT", "ABSENT"],
    }
    serve_scoring(host, [
        scoring_reply(readings[leader_points], substantiated=True),
        scoring_reply(readings[validator_points], substantiated=True),
    ])
    if operation == "challenge":
        enforcer.challenge_fulfilment(
            "pact-1", side, GROUNDS,
            sender=accounts["bob"] if side == "A" else accounts["alice"],
        )
    else:
        submit(enforcer, accounts, side=side)

    host.warp(T0 + 8 * DAY)
    preview = json.loads(enforcer.settlement_preview("pact-1"))
    settled = resolve(enforcer, accounts)
    # Independent integer arithmetic, including asymmetric stakes and rounding.
    credit_a, credit_b = (50, 0) if side == "A" else (0, 50)
    forfeit_a = 101 * (100 - credit_a) // 100
    forfeit_b = 207 * (100 - credit_b) // 100
    payout_a = 101 - forfeit_a + (0 if sink else forfeit_b)
    payout_b = 207 - forfeit_b + (0 if sink else forfeit_a)
    sink_amount = forfeit_a + forfeit_b if sink else 0
    for result in (preview, settled):
        assert int(result["payout_a_wei"]) == payout_a
        assert int(result["payout_b_wei"]) == payout_b
        assert int(result["sink_wei"]) == sink_amount
    actual = transfers(host)
    assert actual.get(accounts["alice"], 0) == payout_a
    assert actual.get(accounts["bob"], 0) == payout_b
    assert actual.get(accounts["sink"], 0) == sink_amount
    assert payout_a + payout_b + sink_amount == 308


def test_registry_defaults_cannot_restore_cross_tier_acceptance(host, enforcer, accounts):
    enforcer.set_defaults(json.dumps({"tier_tolerance_points": 4}), sender=accounts["deployer"])
    options = default_options()
    del options["tier_tolerance_points"]
    active_pact(host, enforcer, accounts, options=options)
    assert json.loads(enforcer.get_pact("pact-1"))["tier_tolerance_points"] == 4
    serve_evidence(host)
    serve_scoring(host, [scoring_reply(READINGS[2][1]), scoring_reply(READINGS[1][1])])
    with pytest.raises(ConsensusFailure):
        submit(enforcer, accounts)
