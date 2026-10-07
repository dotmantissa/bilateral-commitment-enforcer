"""
The cross-contract gate, exercised end to end.

The enforcer's view methods are its integration surface, and a gate that has never
been called across a contract boundary is a gate nobody has tested. These tests
deploy the reference consumer against a real enforcer and drive the whole path:
registration refused for an unknown pact, a claim refused with the enforcer's own
reason attached, and the same claim succeeding once consensus confirms the
commitment.

The harness runs these as real synchronous cross-contract view calls. The callee
executes in its own storage namespace with the caller as sender, which is exactly
what happens on chain.
"""

import pytest

from conftest import *  # noqa: F403


DATASET_PAYLOAD = "https://data.example/private/lending-q3.csv?token=f4c1a9e0b7d2358e"


@pytest.fixture
def release(host, enforcer, accounts):
    """The reference consumer, bound to the deployed enforcer."""
    return host.deploy(
        RELEASE_SOURCE,
        "CommitmentGatedRelease",
        args=[enforcer.address],
        sender=accounts["carol"],
    )


def register(release, accounts, gate="SIDE_A", key="dataset", pact_id="pact-1", open_to_all=True):
    return json.loads(
        release.register_release(
            key, "Q3 lending dataset access", pact_id, gate, DATASET_PAYLOAD, open_to_all,
            sender=accounts["carol"],
        )
    )


# ---------------------------------------------------------------------------
# Binding and registration
# ---------------------------------------------------------------------------

def test_the_consumer_reports_the_enforcer_it_is_bound_to(host, enforcer, accounts, release):
    info = json.loads(release.get_info())
    assert info["enforcer"] == enforcer.address
    assert info["owner"] == accounts["carol"]
    assert info["gate_modes"] == ["SIDE_A", "SIDE_B", "MUTUAL"]


def test_registration_refuses_a_pact_the_enforcer_does_not_have(host, enforcer, accounts, release):
    """
    Checked at registration rather than at claim time, so a typo in a pact id fails for
    the person who made it instead of for a recipient weeks later.
    """
    with pytest.raises(Exception) as excinfo:
        register(release, accounts, pact_id="no-such-pact")
    assert "The enforcer has no pact 'no-such-pact'" in revert_message(excinfo)


def test_registration_succeeds_once_the_pact_exists(host, enforcer, accounts, release):
    active_pact(host, enforcer, accounts)
    out = register(release, accounts)
    assert out["pact_id"] == "pact-1"
    assert out["gate"] == "SIDE_A"
    assert release.get_release_count() == 1
    assert release.get_release_key_at(0) == "dataset"


def test_only_the_owner_may_register_or_revoke(host, enforcer, accounts, release):
    active_pact(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        release.register_release("x", "x", "pact-1", "SIDE_A", DATASET_PAYLOAD, True, sender=accounts["alice"])
    assert "Only the owner may register" in revert_message(excinfo)

    register(release, accounts)
    with pytest.raises(Exception) as excinfo:
        release.revoke_release("dataset", sender=accounts["alice"])
    assert "Only the release owner may revoke" in revert_message(excinfo)


# ---------------------------------------------------------------------------
# The single direction gate
# ---------------------------------------------------------------------------

def test_a_claim_is_refused_while_the_commitment_is_unproven(host, enforcer, accounts, release):
    """
    The gate closed, with the enforcer's reason carried through to the caller rather
    than flattened to a bare refusal.
    """
    active_pact(host, enforcer, accounts, dependency="b_after_a")
    register(release, accounts, gate="SIDE_A")

    status = json.loads(release.gate_status("dataset"))
    assert status["open"] is False
    assert status["reason"] == "party A is not confirmed as fulfilled"

    with pytest.raises(Exception) as excinfo:
        release.claim("dataset", sender=accounts["bob"])
    message = revert_message(excinfo)
    assert "gate on 'dataset' is closed" in message
    assert "party A is not confirmed as fulfilled" in message


def test_consensus_confirming_the_commitment_opens_the_gate(host, enforcer, accounts, release):
    """
    The whole arc. Nothing about the gate is simulated: the claim makes a real view
    call into the enforcer and acts on what comes back.
    """
    active_pact(host, enforcer, accounts, dependency="b_after_a")
    register(release, accounts, gate="SIDE_A")

    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")

    assert json.loads(release.gate_status("dataset"))["open"] is True
    claimed = json.loads(release.claim("dataset", sender=accounts["bob"]))
    assert claimed["payload"] == DATASET_PAYLOAD
    assert claimed["gate_reason"] == "party A is confirmed as fulfilled"
    assert claimed["claims"] == 1


def test_a_failed_adjudication_leaves_the_gate_shut(host, enforcer, accounts, release):
    active_pact(host, enforcer, accounts)
    register(release, accounts, gate="SIDE_A")

    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(TWO_OF_THREE))
    assert submit(enforcer, accounts, side="A")["passed"] is False
    assert json.loads(release.gate_status("dataset"))["open"] is False


def test_the_gate_opens_at_exactly_the_moment_the_next_obligation_begins(host, enforcer, accounts, release):
    """
    The sequential workflow the primitive exists for, written out. A pact declared
    `b_after_a` plus a release gated on SIDE_A means the data becomes readable at
    precisely the instant party B's clock starts.
    """
    active_pact(host, enforcer, accounts, dependency="b_after_a", window_b=14 * DAY)
    register(release, accounts, gate="SIDE_A")

    assert json.loads(release.gate_status("dataset"))["open"] is False
    assert enforcer.obligation_state("pact-1", "B") == "WAITING"

    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    host.warp(T0 + 2 * DAY)
    submit(enforcer, accounts, side="A")

    assert json.loads(release.gate_status("dataset"))["open"] is True
    ob_b = json.loads(enforcer.get_obligation("pact-1", "B"))
    assert ob_b["state"] == "OPEN"
    assert ob_b["deadline_at"] == T0 + 2 * DAY + 14 * DAY


def test_a_challenge_that_overturns_a_fulfilment_shuts_the_gate_again(host, enforcer, accounts, release):
    """
    The gate reads live state, so it tracks an overturned fulfilment without anyone
    telling the consumer contract anything.
    """
    active_pact(host, enforcer, accounts, options=default_options(challenge_window=2 * DAY))
    register(release, accounts, gate="SIDE_A")

    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")
    assert json.loads(release.gate_status("dataset"))["open"] is True

    host.clear_routes()
    serve_evidence(host, EVIDENCE_A, BODY_A_PARTIAL)
    serve_scoring(host, scoring_reply(NONE_OF_THREE, substantiated=True))
    enforcer.challenge_fulfilment(
        "pact-1", "A", "Neither the schema nor the checksum is actually published on that page.",
        sender=accounts["bob"],
    )
    assert json.loads(release.gate_status("dataset"))["open"] is False


# ---------------------------------------------------------------------------
# The mutual gate
# ---------------------------------------------------------------------------

def test_the_mutual_gate_needs_both_sides(host, enforcer, accounts, release):
    active_pact(host, enforcer, accounts)
    register(release, accounts, gate="MUTUAL", key="licence")

    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_evidence(host, EVIDENCE_B, BODY_B_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))

    submit(enforcer, accounts, side="A")
    status = json.loads(release.gate_status("licence"))
    assert status["open"] is False
    # The enforcer's reverting view carries its own reason through, which is why the
    # consumer uses it for the mutual gate rather than a bare boolean.
    assert "Party B is OPEN, not FULFILLED" in status["reason"]

    submit(enforcer, accounts, side="B")
    status = json.loads(release.gate_status("licence"))
    assert status["open"] is True
    assert status["reason"] == "both parties are confirmed as fulfilled"

    assert json.loads(release.claim("licence", sender=accounts["alice"]))["payload"] == DATASET_PAYLOAD


def test_the_mutual_gate_stays_open_after_the_pact_settles(host, enforcer, accounts, release):
    """
    A settled pact keeps its fulfilment record, so a release that opened does not shut
    simply because the money moved.
    """
    active_pact(host, enforcer, accounts)
    register(release, accounts, gate="MUTUAL", key="licence")

    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_evidence(host, EVIDENCE_B, BODY_B_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")
    submit(enforcer, accounts, side="B")
    assert resolve(enforcer, accounts)["outcome"] == "MUTUAL_FULFILMENT"

    assert json.loads(enforcer.get_pact("pact-1"))["status"] == "SETTLED"
    assert json.loads(release.gate_status("licence"))["open"] is True


def test_a_breached_pact_never_opens_the_mutual_gate(host, enforcer, accounts, release):
    active_pact(host, enforcer, accounts, window_a=2 * DAY, window_b=2 * DAY)
    register(release, accounts, gate="MUTUAL", key="licence")
    host.warp(T0 + 3 * DAY)

    status = json.loads(release.gate_status("licence"))
    assert status["open"] is False
    assert "Party A is BREACHED" in status["reason"]

    resolve(enforcer, accounts)
    assert json.loads(release.gate_status("licence"))["open"] is False


def test_the_side_b_gate_works_the_same_way(host, enforcer, accounts, release):
    active_pact(host, enforcer, accounts)
    register(release, accounts, gate="SIDE_B", key="report")

    serve_evidence(host, EVIDENCE_B, BODY_B_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3), side="B")
    assert json.loads(release.gate_status("report"))["open"] is False
    submit(enforcer, accounts, side="B")
    assert json.loads(release.gate_status("report"))["open"] is True


# ---------------------------------------------------------------------------
# Consumer side access control
# ---------------------------------------------------------------------------

def test_a_restricted_release_only_admits_allowed_recipients(host, enforcer, accounts, release):
    active_pact(host, enforcer, accounts)
    register(release, accounts, open_to_all=False)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")

    assert release.is_recipient_allowed("dataset", accounts["bob"]) is False
    with pytest.raises(Exception) as excinfo:
        release.claim("dataset", sender=accounts["bob"])
    assert "restricted and you are not on its allow list" in revert_message(excinfo)

    release.allow_recipient("dataset", accounts["bob"], sender=accounts["carol"])
    assert release.is_recipient_allowed("dataset", accounts["bob"]) is True
    assert json.loads(release.claim("dataset", sender=accounts["bob"]))["payload"] == DATASET_PAYLOAD


def test_a_recipient_cannot_claim_twice(host, enforcer, accounts, release):
    active_pact(host, enforcer, accounts)
    register(release, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")

    release.claim("dataset", sender=accounts["bob"])
    assert release.has_claimed("dataset", accounts["bob"]) is True
    with pytest.raises(Exception) as excinfo:
        release.claim("dataset", sender=accounts["bob"])
    assert "already claimed" in revert_message(excinfo)


def test_a_blocked_claim_costs_the_recipient_nothing(host, enforcer, accounts, release):
    """
    Claims are counted rather than attempts, so a recipient turned away by a gate that
    is not yet open can come back later.
    """
    active_pact(host, enforcer, accounts)
    register(release, accounts)
    with pytest.raises(Exception):
        release.claim("dataset", sender=accounts["bob"])
    assert release.has_claimed("dataset", accounts["bob"]) is False

    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")
    assert json.loads(release.claim("dataset", sender=accounts["bob"]))["claims"] == 1


def test_a_revoked_release_refuses_claims_even_with_the_gate_open(host, enforcer, accounts, release):
    active_pact(host, enforcer, accounts)
    register(release, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")

    release.revoke_release("dataset", sender=accounts["carol"])
    assert json.loads(release.gate_status("dataset"))["open"] is True
    with pytest.raises(Exception) as excinfo:
        release.claim("dataset", sender=accounts["bob"])
    assert "is revoked" in revert_message(excinfo)


def test_the_release_metadata_view_never_leaks_the_payload(host, enforcer, accounts, release):
    active_pact(host, enforcer, accounts)
    register(release, accounts)
    record = json.loads(release.get_release("dataset"))
    assert "payload" not in record
    assert record["payload_length"] == len(DATASET_PAYLOAD)
    assert record["gate_open"] is False


def test_an_unknown_gate_mode_is_refused(host, enforcer, accounts, release):
    active_pact(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        release.register_release("x", "x", "pact-1", "EITHER", DATASET_PAYLOAD, True, sender=accounts["carol"])
    assert "gate must be one of" in revert_message(excinfo)
