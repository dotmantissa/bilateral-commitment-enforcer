"""
Pact lifecycle: creation, consent, acceptance and withdrawal.

The arc these tests walk is the one that decides whether a party is bound at all.
A pact only binds when both sides have put value in against terms they both saw,
so most of this file is about the consent machinery rather than about adjudication.
"""

import pytest

from conftest import *  # noqa: F403


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

def test_registry_starts_empty_with_documented_defaults(host, enforcer, accounts):
    info = json.loads(enforcer.get_registry_info())
    assert info["pact_count"] == 0
    assert info["pacts_settled"] == "0"
    assert info["total_locked_wei"] == "0"
    assert info["steward"] == accounts["deployer"]
    assert info["defaults"]["pass_threshold"] == 80
    assert info["defaults"]["max_attempts"] == 3
    # The tier table is published, because an integrator pricing a pact needs to
    # know what partial credit is worth before anyone stakes anything.
    assert info["tiers"] == {"NONE": 0, "MINIMAL": 20, "PARTIAL": 50, "SUBSTANTIAL": 80, "COMPLETE": 100}
    assert info["dependency_modes"] == ["none", "b_after_a", "a_after_b"]


def test_create_locks_party_a_stake_and_leaves_pact_proposed(host, enforcer, accounts):
    created = create(host, enforcer, accounts, stake_a=7 * ONE, stake_b=3 * ONE)

    assert created["status"] == "PROPOSED"
    assert created["party_a"] == accounts["alice"]
    assert created["party_b"] == accounts["bob"]
    assert created["stake_a_wei"] == str(7 * ONE)
    assert created["stake_b_wei"] == str(3 * ONE)

    # Party A's stake is locked now. Party B's is only declared.
    info = json.loads(enforcer.get_registry_info())
    assert info["total_locked_wei"] == str(7 * ONE)
    assert info["pact_count"] == 1

    pact = json.loads(enforcer.get_pact("pact-1"))
    assert pact["status"] == "PROPOSED"
    assert pact["activated_at"] == 0
    # No state is reported before activation, because neither obligation exists yet
    # in any meaningful sense.
    assert pact["state_a"] == ""
    assert pact["state_b"] == ""


def test_create_indexes_the_pact_against_both_parties(host, enforcer, accounts):
    create(host, enforcer, accounts, pact_id="pact-1")
    serve_admissibility(host)
    create(host, enforcer, accounts, pact_id="pact-2", serve=False)

    assert enforcer.party_pact_count_of(accounts["alice"]) == 2
    assert enforcer.party_pact_count_of(accounts["bob"]) == 2
    assert enforcer.party_pact_count_of(accounts["outsider"]) == 0
    assert enforcer.party_pact_id_at(accounts["alice"], 0) == "pact-1"
    assert enforcer.party_pact_id_at(accounts["bob"], 1) == "pact-2"

    assert enforcer.get_pact_count() == 2
    assert enforcer.get_pact_id_at(0) == "pact-1"


def test_create_refuses_a_duplicate_pact_id(host, enforcer, accounts):
    create(host, enforcer, accounts, pact_id="pact-1")
    serve_admissibility(host)
    with pytest.raises(Exception) as excinfo:
        create(host, enforcer, accounts, pact_id="pact-1", serve=False)
    assert "already exists" in revert_message(excinfo)


def test_create_refuses_a_pact_with_one_party_on_both_sides(host, enforcer, accounts):
    serve_admissibility(host)
    with pytest.raises(Exception) as excinfo:
        create(host, enforcer, accounts, party_b=accounts["alice"], serve=False)
    assert "two different parties" in revert_message(excinfo)


def test_create_requires_a_stake_from_party_a(host, enforcer, accounts):
    """A commitment enforcer with nothing at stake enforces nothing."""
    serve_admissibility(host)
    with pytest.raises(Exception) as excinfo:
        enforcer.create_pact(
            "pact-1", "No stake", accounts["bob"], COMMITMENT_A, COMMITMENT_B,
            str(ONE), "none", 7 * DAY, 7 * DAY, json.dumps(default_options()),
            sender=accounts["alice"], value=0,
        )
    assert "must send its stake" in revert_message(excinfo)


def test_create_requires_a_nonzero_stake_from_party_b(host, enforcer, accounts):
    serve_admissibility(host)
    with pytest.raises(Exception) as excinfo:
        create(host, enforcer, accounts, stake_b=0, serve=False)
    assert "greater than zero" in revert_message(excinfo)


def test_create_refuses_an_unknown_dependency_mode(host, enforcer, accounts):
    serve_admissibility(host)
    with pytest.raises(Exception) as excinfo:
        create(host, enforcer, accounts, dependency="mutual", serve=False)
    assert "dependency must be one of" in revert_message(excinfo)


@pytest.mark.parametrize("window", [0, 59, 400 * DAY])
def test_create_bounds_the_deadline_windows(host, enforcer, accounts, window):
    serve_admissibility(host)
    with pytest.raises(Exception) as excinfo:
        create(host, enforcer, accounts, window_a=window, serve=False)
    assert "window_a_seconds must be between" in revert_message(excinfo)


def test_create_refuses_a_sink_that_is_one_of_the_parties(host, enforcer, accounts):
    """A sink that pays a party back defeats the only deterrent mutual breach has."""
    serve_admissibility(host)
    with pytest.raises(Exception) as excinfo:
        create(
            host, enforcer, accounts, serve=False,
            options=default_options(mutual_breach_sink=accounts["bob"]),
        )
    assert "cannot be one of the parties" in revert_message(excinfo)


def test_create_rejects_an_option_that_is_out_of_range(host, enforcer, accounts):
    serve_admissibility(host)
    with pytest.raises(Exception) as excinfo:
        create(host, enforcer, accounts, serve=False, options=default_options(pass_threshold=0))
    assert "'pass_threshold' must be between 1 and 100" in revert_message(excinfo)


def test_create_rejects_a_malformed_options_bag(host, enforcer, accounts):
    serve_admissibility(host)
    with pytest.raises(Exception) as excinfo:
        enforcer.create_pact(
            "pact-1", "Bad options", accounts["bob"], COMMITMENT_A, COMMITMENT_B,
            str(ONE), "none", 7 * DAY, 7 * DAY, "{not json",
            sender=accounts["alice"], value=ONE,
        )
    assert "not valid JSON" in revert_message(excinfo)


def test_create_rejects_a_duplicated_entry_in_a_supplied_rubric(host, enforcer, accounts):
    """Two identically worded deliverables would double one requirement's weight."""
    serve_admissibility(host)
    with pytest.raises(Exception) as excinfo:
        create(
            host, enforcer, accounts, serve=False,
            options=default_options(checklist_a=["Publish the CSV", "publish the CSV!"]),
        )
    assert "repeats" in revert_message(excinfo)


def test_stake_amounts_cross_the_abi_as_strings_without_losing_precision(host, enforcer, accounts):
    """
    A stake in wei routinely exceeds what JSON and JavaScript carry losslessly, and
    a stake that is silently rounded is a stake that breaks conservation.
    """
    awkward = 12_345_678_901_234_567_890_123
    assert awkward > 2**53
    create(host, enforcer, accounts, stake_a=ONE, stake_b=awkward)
    pact = json.loads(enforcer.get_pact("pact-1"))
    assert pact["stake_b_wei"] == str(awkward)
    assert int(pact["stake_b_wei"]) == awkward


def test_create_rejects_a_non_numeric_stake_string(host, enforcer, accounts):
    serve_admissibility(host)
    with pytest.raises(Exception) as excinfo:
        enforcer.create_pact(
            "pact-1", "Bad stake", accounts["bob"], COMMITMENT_A, COMMITMENT_B,
            "1.5e18", "none", 7 * DAY, 7 * DAY, json.dumps(default_options()),
            sender=accounts["alice"], value=ONE,
        )
    assert "decimal wei string" in revert_message(excinfo)


# ---------------------------------------------------------------------------
# Consent: the terms digest
# ---------------------------------------------------------------------------

def test_terms_digest_matches_an_independent_recomputation(host, enforcer, accounts):
    """
    The digest is what makes party B's acceptance a statement about specific terms.
    Recomputed here from the published terms by an independent implementation, so
    this checks the contract's commitment rather than agreeing with it.
    """
    created = create(host, enforcer, accounts, stake_a=4 * ONE, stake_b=6 * ONE, dependency="b_after_a")

    expected = terms_digest_of(
        pact_id="pact-1",
        party_a=accounts["alice"],
        party_b=accounts["bob"],
        stake_a=4 * ONE,
        stake_b=6 * ONE,
        threshold=80,
        dependency="b_after_a",
        attempts=3,
        challenge_window=0,
        tolerance=1,
        sink=ZERO,
        commitment_a=COMMITMENT_A,
        commitment_b=COMMITMENT_B,
        window_a=7 * DAY,
        window_b=7 * DAY,
        checklist_a=CHECKLIST_A,
        checklist_b=CHECKLIST_B,
    )
    assert created["terms_digest"] == expected
    assert enforcer.terms_digest("pact-1") == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("stake_b", 11 * ONE),
        ("window_a", 8 * DAY),
        ("dependency", "b_after_a"),
        ("commitment_a", COMMITMENT_A + " Also include a changelog."),
    ],
)
def test_the_digest_changes_when_any_binding_term_changes(host, enforcer, accounts, field, value):
    """
    Every term whose change would mean party B is agreeing to something else must
    move the digest, or the digest is not protecting B.
    """
    base = create(host, enforcer, accounts, pact_id="pact-base")
    serve_admissibility(host)
    other = create(host, enforcer, accounts, pact_id="pact-other", serve=False, **{field: value})
    assert other["terms_digest"] != base["terms_digest"]


def test_the_digest_covers_the_rubric_text(host, enforcer, accounts):
    """
    Party B's consent must bind the exact deterministic rubric text in addition
    to the validator comparison against transaction inputs.
    """
    base = create(host, enforcer, accounts, pact_id="pact-base")
    serve_admissibility(host)
    altered = create(
        host, enforcer, accounts, pact_id="pact-altered", serve=False,
        options=default_options(checklist_a=CHECKLIST_A[:2] + ["A SHA-512 checksum for the CSV"]),
    )
    assert altered["terms_digest"] != base["terms_digest"]


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------

def test_accept_locks_party_b_stake_and_activates_the_pact(host, enforcer, accounts):
    create(host, enforcer, accounts, stake_a=4 * ONE, stake_b=6 * ONE)
    accepted = accept(enforcer, accounts, stake_b=6 * ONE)

    assert accepted["status"] == "ACTIVE"
    assert accepted["activated_at"] == T0
    assert accepted["total_locked_wei"] == str(10 * ONE)

    info = json.loads(enforcer.get_registry_info())
    assert info["total_locked_wei"] == str(10 * ONE)


def test_accept_refuses_a_digest_that_does_not_match(host, enforcer, accounts):
    """This is the guard that stops party B being bound to terms it never read."""
    create(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        accept(enforcer, accounts, digest="0" * 64)
    assert "terms_digest does not match" in revert_message(excinfo)
    assert json.loads(enforcer.get_pact("pact-1"))["status"] == "PROPOSED"


def test_accept_tolerates_a_digest_written_with_an_0x_prefix(host, enforcer, accounts):
    """Tooling adds the prefix routinely, and refusing it would be a trap, not a check."""
    created = create(host, enforcer, accounts)
    accepted = accept(enforcer, accounts, digest="0X" + created["terms_digest"].upper())
    assert accepted["status"] == "ACTIVE"


def test_accept_requires_the_exact_declared_stake(host, enforcer, accounts):
    create(host, enforcer, accounts, stake_b=6 * ONE)
    for wrong in (5 * ONE, 7 * ONE):
        with pytest.raises(Exception) as excinfo:
            accept(enforcer, accounts, stake_b=wrong)
        assert "must send exactly" in revert_message(excinfo)
    assert accept(enforcer, accounts, stake_b=6 * ONE)["status"] == "ACTIVE"


def test_only_the_named_party_b_may_accept(host, enforcer, accounts):
    create(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        accept(enforcer, accounts, sender=accounts["carol"])
    assert "Only the named party B" in revert_message(excinfo)


def test_accept_refuses_after_the_funding_window_closes(host, enforcer, accounts):
    create(host, enforcer, accounts, options=default_options(funding_window=2 * DAY))
    host.warp(T0 + 2 * DAY + 1)
    with pytest.raises(Exception) as excinfo:
        accept(enforcer, accounts)
    assert "funding window" in revert_message(excinfo)


def test_a_pact_cannot_be_accepted_twice(host, enforcer, accounts):
    active_pact(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        accept(enforcer, accounts)
    assert "is ACTIVE, not PROPOSED" in revert_message(excinfo)


# ---------------------------------------------------------------------------
# Withdrawal
# ---------------------------------------------------------------------------

def test_party_a_may_withdraw_a_proposal_and_is_refunded_in_full(host, enforcer, accounts):
    """
    Party A can withdraw while its deterministic proposal awaits B's consent.
    """
    create(host, enforcer, accounts, stake_a=5 * ONE)
    out = json.loads(enforcer.withdraw_proposal("pact-1", sender=accounts["alice"]))

    assert out["outcome"] == "WITHDRAWN_UNFUNDED"
    assert out["refund_wei"] == str(5 * ONE)
    assert transfers(host)[accounts["alice"]] == 5 * ONE
    assert json.loads(enforcer.get_registry_info())["total_locked_wei"] == "0"


def test_party_b_cannot_withdraw_a_proposal_before_the_window_closes(host, enforcer, accounts):
    create(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        enforcer.withdraw_proposal("pact-1", sender=accounts["bob"])
    assert "Only party A may withdraw" in revert_message(excinfo)


def test_anyone_may_withdraw_an_expired_proposal_so_a_stake_is_never_stranded(host, enforcer, accounts):
    create(host, enforcer, accounts, stake_a=5 * ONE, options=default_options(funding_window=DAY))
    host.warp(T0 + DAY + 1)

    out = json.loads(enforcer.withdraw_proposal("pact-1", sender=accounts["outsider"]))
    assert out["reason"] == "funding window expired"
    # The refund still goes to party A, not to whoever triggered it.
    assert transfers(host)[accounts["alice"]] == 5 * ONE
    assert accounts["outsider"] not in transfers(host)


def test_an_accepted_pact_can_no_longer_be_withdrawn(host, enforcer, accounts):
    active_pact(host, enforcer, accounts)
    with pytest.raises(Exception) as excinfo:
        enforcer.withdraw_proposal("pact-1", sender=accounts["alice"])
    assert "is ACTIVE, not PROPOSED" in revert_message(excinfo)


def test_a_withdrawn_pact_cannot_be_withdrawn_or_accepted_again(host, enforcer, accounts):
    create(host, enforcer, accounts)
    enforcer.withdraw_proposal("pact-1", sender=accounts["alice"])

    with pytest.raises(Exception) as excinfo:
        enforcer.withdraw_proposal("pact-1", sender=accounts["alice"])
    assert "is WITHDRAWN" in revert_message(excinfo)

    with pytest.raises(Exception) as excinfo:
        accept(enforcer, accounts)
    assert "is WITHDRAWN, not PROPOSED" in revert_message(excinfo)


def test_a_withdrawal_is_recorded_as_a_settlement_for_auditability(host, enforcer, accounts):
    create(host, enforcer, accounts, stake_a=5 * ONE)
    enforcer.withdraw_proposal("pact-1", sender=accounts["alice"])

    settlement = json.loads(enforcer.get_settlement("pact-1"))
    assert settlement["outcome"] == "WITHDRAWN_UNFUNDED"
    assert settlement["payout_a_wei"] == str(5 * ONE)
    assert settlement["payout_b_wei"] == "0"
    assert settlement["total_out_wei"] == str(5 * ONE)
