"""
Replay protection and the attempt allowance.

`max_attempts` exists because real review is iterative: a panel says what is
missing, the party fixes it and comes back. Without replay protection that same
allowance is a free re-roll, where a party submits one URL five times and keeps
whichever reading happens to pass. These tests pin both halves.
"""

import pytest

from conftest import *  # noqa: F403


def ready(host, enforcer, accounts, **kwargs):
    """An active pact with party A's evidence served and a chosen reading."""
    active_pact(host, enforcer, accounts, **kwargs)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------

def test_the_same_url_cannot_be_adjudicated_twice_for_one_side(host, enforcer, accounts):
    """
    The attack this closes. With three attempts and no protection, a party submits one
    page three times and takes the luckiest panel reading.
    """
    ready(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(TWO_OF_THREE))

    first = submit(enforcer, accounts, side="A")
    assert first["passed"] is False
    assert first["attempts_remaining"] == 2

    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="A")
    message = revert_message(excinfo)
    assert "already been adjudicated for this side" in message
    assert "rather than the same page again" in message

    # And the refusal did not consume the remaining attempts.
    assert json.loads(enforcer.get_obligation("pact-1", "A"))["attempts_remaining"] == 2


@pytest.mark.parametrize(
    "variant",
    [
        "https://EVIDENCE.example/dataset-q3",
        "HTTPS://evidence.example/dataset-q3",
        "https://evidence.example/dataset-q3/",
        "https://evidence.example/dataset-q3#results",
        "  https://evidence.example/dataset-q3  ",
    ],
)
def test_cosmetic_url_mutation_does_not_evade_replay_protection(host, enforcer, accounts, variant):
    """
    Host case, scheme case, a trailing slash and a bare fragment all fetch the same
    document, so none of them is a different piece of evidence.
    """
    ready(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(TWO_OF_THREE))
    submit(enforcer, accounts, side="A")

    assert enforcer.evidence_already_used("pact-1", "A", variant) is True
    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="A", url=variant)
    assert "already been adjudicated" in revert_message(excinfo)


def test_path_case_is_preserved_because_servers_distinguish_it(host, enforcer, accounts):
    """
    `/Report.pdf` is not `/report.pdf` on most servers, so folding path case would
    refuse genuinely different evidence.
    """
    ready(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(TWO_OF_THREE))
    submit(enforcer, accounts, side="A")

    other = "https://evidence.example/Dataset-Q3"
    assert enforcer.evidence_already_used("pact-1", "A", other) is False


def test_a_query_string_makes_a_different_document(host, enforcer, accounts):
    """On many hosts the query selects the document, so it is never stripped."""
    ready(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(TWO_OF_THREE))
    submit(enforcer, accounts, side="A")
    assert enforcer.evidence_already_used("pact-1", "A", EVIDENCE_A + "?rev=2") is False


def test_replay_protection_is_scoped_to_one_side(host, enforcer, accounts):
    """
    The two sides are being judged against different commitments, so one party's
    evidence URL says nothing about the other's.
    """
    ready(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(TWO_OF_THREE))
    submit(enforcer, accounts, side="A")

    assert enforcer.evidence_already_used("pact-1", "A", EVIDENCE_A) is True
    assert enforcer.evidence_already_used("pact-1", "B", EVIDENCE_A) is False
    # And party B may genuinely use it.
    assert submit(enforcer, accounts, side="B", url=EVIDENCE_A)["attempt"] == 1


def test_replay_protection_is_scoped_to_one_pact(host, enforcer, accounts):
    ready(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(TWO_OF_THREE))
    submit(enforcer, accounts, side="A")

    serve_admissibility(host)
    create(host, enforcer, accounts, pact_id="pact-2", serve=False)
    accept(enforcer, accounts, pact_id="pact-2")
    assert enforcer.evidence_already_used("pact-2", "A", EVIDENCE_A) is False
    assert submit(enforcer, accounts, pact_id="pact-2", side="A")["attempt"] == 1


def test_a_failed_adjudication_does_not_burn_the_url(host, enforcer, accounts):
    """
    Nothing was committed, so nothing was recorded. A party whose submission reverted
    on a transient fetch failure must be able to retry the same page.
    """
    active_pact(host, enforcer, accounts)
    host.mock_web(re.escape(EVIDENCE_A) + r"$", {"status": 503, "body": ""})
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))

    with pytest.raises(Exception):
        submit(enforcer, accounts, side="A")
    assert enforcer.evidence_already_used("pact-1", "A", EVIDENCE_A) is False

    host.clear_routes()
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    assert submit(enforcer, accounts, side="A")["passed"] is True


# ---------------------------------------------------------------------------
# The attempt allowance
# ---------------------------------------------------------------------------

def test_a_party_may_resubmit_after_a_panel_says_what_is_missing(host, enforcer, accounts):
    """How real review works, and what the attempt allowance is for."""
    ready(host, enforcer, accounts, options=default_options(max_attempts=3))
    serve_evidence(host, "https://evidence.example/dataset-q3-v2", BODY_A_COMPLETE)

    serve_scoring(host, scoring_reply(TWO_OF_THREE, missing="No SHA-256 checksum is published."))
    first = submit(enforcer, accounts, side="A")
    assert first["passed"] is False
    assert first["panel_missing"] == "No SHA-256 checksum is published."

    host.clear_routes()
    serve_evidence(host, "https://evidence.example/dataset-q3-v2", BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    second = submit(enforcer, accounts, side="A", url="https://evidence.example/dataset-q3-v2")
    assert second["passed"] is True
    assert second["attempt"] == 2
    assert json.loads(enforcer.get_obligation("pact-1", "A"))["state"] == "FULFILLED"


def test_the_recorded_credit_is_the_best_across_attempts_not_the_latest(host, enforcer, accounts):
    """
    A party told what it was missing, which resubmits and happens to score worse,
    should not be worse off than if it had never tried. The best reading of the work
    it actually delivered is what its partial credit reflects.
    """
    ready(host, enforcer, accounts, options=default_options(max_attempts=2, pass_threshold=100))
    serve_evidence(host, "https://evidence.example/dataset-q3-v2", BODY_A_PARTIAL)

    serve_scoring(host, scoring_reply(TWO_OF_THREE))  # 4 of 6, PARTIAL, credit 50
    submit(enforcer, accounts, side="A")

    host.clear_routes()
    serve_evidence(host, "https://evidence.example/dataset-q3-v2", BODY_A_PARTIAL)
    serve_scoring(host, scoring_reply(ONE_OF_THREE))  # 2 of 6, MINIMAL, credit 20
    out = submit(enforcer, accounts, side="A", url="https://evidence.example/dataset-q3-v2")

    assert out["tier"] == "MINIMAL"       # this attempt
    assert out["best_tier"] == "PARTIAL"  # what counts
    assert out["best_credit"] == 50

    ob = json.loads(enforcer.get_obligation("pact-1", "A"))
    assert ob["best_attempt"] == 1
    assert ob["best_credit"] == 50
    assert ob["best_evidence_url"] == EVIDENCE_A


def test_exhausting_attempts_below_the_threshold_is_terminal_immediately(host, enforcer, accounts):
    """
    There is no route left to fulfilment, so waiting out a clock whose outcome is
    already fixed would only strand both stakes. Settling early is strictly better for
    both parties.
    """
    ready(host, enforcer, accounts, window_a=300 * DAY, options=default_options(max_attempts=1))
    serve_scoring(host, scoring_reply(TWO_OF_THREE))

    out = submit(enforcer, accounts, side="A")
    assert out["attempts_remaining"] == 0
    assert out["state_a"] == "BREACHED"
    assert enforcer.obligation_state("pact-1", "A") == "BREACHED"

    # And the deadline is still a long way off.
    assert json.loads(enforcer.get_obligation("pact-1", "A"))["seconds_remaining"] > 200 * DAY


def test_a_party_with_no_attempts_left_cannot_submit(host, enforcer, accounts):
    ready(host, enforcer, accounts, options=default_options(max_attempts=1))
    serve_evidence(host, "https://evidence.example/v2", BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(TWO_OF_THREE))
    submit(enforcer, accounts, side="A")

    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="A", url="https://evidence.example/v2")
    assert "BREACHED and can no longer submit" in revert_message(excinfo)


def test_a_confirmed_side_cannot_submit_again(host, enforcer, accounts):
    """Once a fulfilment is confirmed it is locked. A later submission cannot undo it."""
    ready(host, enforcer, accounts)
    serve_evidence(host, "https://evidence.example/v2", BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")

    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="A", url="https://evidence.example/v2")
    assert "already been confirmed as fulfilled" in revert_message(excinfo)


def test_a_party_cannot_submit_after_its_deadline(host, enforcer, accounts):
    ready(host, enforcer, accounts, window_a=3 * DAY)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    host.warp(T0 + 3 * DAY + 1)

    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="A")
    assert "BREACHED and can no longer submit" in revert_message(excinfo)


def test_a_party_may_submit_on_the_deadline_itself(host, enforcer, accounts):
    """The boundary is inclusive, so a submission landing on the deadline counts."""
    ready(host, enforcer, accounts, window_a=3 * DAY)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    host.warp(T0 + 3 * DAY)
    assert submit(enforcer, accounts, side="A")["passed"] is True


def test_an_outsider_cannot_submit_evidence(host, enforcer, accounts):
    ready(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    with pytest.raises(Exception) as excinfo:
        enforcer.submit_evidence("pact-1", EVIDENCE_A, sender=accounts["carol"])
    assert "not a party to this pact" in revert_message(excinfo)


def test_a_party_cannot_submit_on_behalf_of_the_other_side(host, enforcer, accounts):
    """
    `submit_evidence` takes no side argument on purpose. The caller is always
    submitting for its own obligation, so there is no way to file evidence against
    the counterparty's commitment.
    """
    ready(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    out = json.loads(enforcer.submit_evidence("pact-1", EVIDENCE_A, sender=accounts["bob"]))
    assert out["side"] == "B"


# ---------------------------------------------------------------------------
# Evidence URL validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    ["", "   ", "evidence.example/dataset", "ftp://evidence.example/dataset",
     "ipfs://QmSomething", "file:///etc/passwd", "javascript:alert(1)"],
)
def test_evidence_must_be_an_http_url_validators_can_reach(host, enforcer, accounts, bad):
    ready(host, enforcer, accounts)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="A", url=bad)
    assert "evidence_url" in revert_message(excinfo)


def test_the_adjudication_log_records_every_attempt_in_order(host, enforcer, accounts):
    ready(host, enforcer, accounts, options=default_options(max_attempts=3, pass_threshold=100))
    for index, verdicts in enumerate([NONE_OF_THREE, ONE_OF_THREE, TWO_OF_THREE], start=1):
        url = f"https://evidence.example/attempt-{index}"
        host.clear_routes()
        serve_evidence(host, url, BODY_A_PARTIAL)
        serve_scoring(host, scoring_reply(verdicts))
        submit(enforcer, accounts, side="A", url=url)

    log = json.loads(enforcer.list_adjudications("pact-1", "A"))
    assert log["attempts_used"] == 3
    assert [entry["tier"] for entry in log["adjudications"]] == ["NONE", "MINIMAL", "PARTIAL"]
    assert [entry["attempt"] for entry in log["adjudications"]] == [1, 2, 3]
    assert all(entry["kind"] == "SUBMISSION" for entry in log["adjudications"])
    assert log["best_attempt"] == 3
    assert log["best_credit"] == 50
