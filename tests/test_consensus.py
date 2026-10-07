"""
Consensus: the three nondeterministic operations and their equivalence principles.

These are the tests that matter most, and they are only possible because the
harness runs the real `gl.vm.run_nondet_unsafe`. The leader closure runs, its
result is encoded exactly as the node encodes it, and the validator closure is
then executed against bytes it did not produce. Giving the web and model channels
a two entry list makes the leader's call and the validator's call see different
answers, which is genuine validator disagreement rather than a simulation of it.

`ConsensusFailure` is what a rejected leader result looks like here. On chain it
terminates the VM and the transaction applies no state change, which is the same
observable effect: nothing was committed.
"""

import pytest

from conftest import *  # noqa: F403
from genvm_host import ConsensusFailure


# ---------------------------------------------------------------------------
# Operation 1: admissibility and rubric extraction
# ---------------------------------------------------------------------------

def test_the_validator_really_runs_and_really_agrees(host, enforcer, accounts):
    """
    Both closures execute. Two model calls per nondeterministic block, one for the
    leader and one for the validator, is the signature of a validator that did the
    work rather than inspected the leader's answer.
    """
    create(host, enforcer, accounts)
    assert len(host.llm_calls) == 2
    assert len(host.nondet_runs) == 1
    assert host.nondet_runs[0]["validator_agreed"] is True


def test_a_validator_that_disagrees_on_admissibility_fails_the_block(host, enforcer, accounts):
    """
    The leader says both commitments are adjudicable, the validator says party B's
    is not. That gates whether the pact may exist at all, so it is never given any
    slack.
    """
    host.mock_llm(
        ADMISSIBILITY_PATTERN,
        [
            admissibility_reply(),
            admissibility_reply(b_ok=False, b_defect="unspecific"),
        ],
    )
    with pytest.raises(ConsensusFailure):
        create(host, enforcer, accounts, serve=False)
    # No state change, which is what a failed nondeterministic block means on chain.
    assert enforcer.get_pact_count() == 0


def test_a_validator_that_disagrees_on_the_defect_class_fails_the_block(host, enforcer, accounts):
    """
    Two panels refusing a commitment for unrelated reasons are not agreeing. The
    defect is the ground of the refusal and it is recorded as such.
    """
    host.mock_llm(
        ADMISSIBILITY_PATTERN,
        [
            admissibility_reply(a_ok=False, a_defect="unspecific"),
            admissibility_reply(a_ok=False, a_defect="subjective_only"),
        ],
    )
    with pytest.raises(ConsensusFailure):
        create(host, enforcer, accounts, serve=False)


def test_a_validator_that_counts_a_different_number_of_deliverables_fails_the_block(host, enforcer, accounts):
    """
    Item count is bound even though item text is not. A different count means the
    two panels read the promise as asking for a different number of things, which is
    a real disagreement about what was promised.
    """
    host.mock_llm(
        ADMISSIBILITY_PATTERN,
        [
            admissibility_reply(a_items=CHECKLIST_A),
            admissibility_reply(a_items=CHECKLIST_A[:2]),
        ],
    )
    with pytest.raises(ConsensusFailure):
        create(host, enforcer, accounts, serve=False, options=default_options(checklist_a=None, checklist_b=None))


def test_differently_worded_rubrics_with_the_same_count_still_reach_consensus(host, enforcer, accounts):
    """
    The deliberate design decision, stated as a test.

    Two honest panels enumerating one promise write "ship the dataset" and "provide
    the data file". Those are one requirement in two wordings, so comparing the text
    would mean no majority ever forms. The count agrees, consensus holds, and the
    rubric is made binding by consent instead: it is covered by the terms digest
    party B must pass.
    """
    paraphrased = [
        "Provide the de-identified third quarter loan data file in CSV form on the open web",
        "Supply documentation of each field in that file",
        "Supply a SHA-256 digest of the data file",
    ]
    host.mock_llm(
        ADMISSIBILITY_PATTERN,
        [
            admissibility_reply(a_items=CHECKLIST_A),
            admissibility_reply(a_items=paraphrased),
        ],
    )
    created = create(
        host, enforcer, accounts, serve=False,
        options=default_options(checklist_a=None, checklist_b=None),
    )
    assert created["status"] == "PROPOSED"
    # The leader's wording is what was stored, and the digest covers it.
    assert created["checklist_a"] == CHECKLIST_A
    assert enforcer.terms_digest("pact-1") == created["terms_digest"]


def test_an_inadmissible_commitment_is_refused_deterministically_after_consensus(host, enforcer, accounts):
    """
    Refusal happens in deterministic code on the consensus bound verdict, not inside
    the nondeterministic block. Raising inside the block would make the revert
    message depend on prose, and two panels would then disagree on a failure they
    actually agreed about.
    """
    host.mock_llm(ADMISSIBILITY_PATTERN, admissibility_reply(b_ok=False, b_defect="subjective_only"))
    with pytest.raises(Exception) as excinfo:
        create(
            host, enforcer, accounts, serve=False,
            commitment_b="I will use my best efforts and act in good faith throughout.",
        )
    message = revert_message(excinfo)
    assert "[EXPECTED]" in message
    assert "Party B's commitment cannot be adjudicated" in message
    assert "subjective_only" in message
    # Both sides agreed, so this is a clean revert rather than a consensus failure.
    assert host.nondet_runs[-1]["validator_agreed"] is True


def test_a_panel_that_contradicts_itself_is_reconciled_to_the_bound_field(host, enforcer, accounts):
    """
    A reply that says adjudicable while naming a defect has contradicted itself. The
    defect is the bound field, so the boolean is reconciled to it rather than the
    other way round, and both sides reconcile identically so consensus holds.
    """
    host.mock_llm(ADMISSIBILITY_PATTERN, admissibility_reply(a_ok=True, a_defect="unspecific"))
    with pytest.raises(Exception) as excinfo:
        create(host, enforcer, accounts, serve=False)
    assert "Party A's commitment cannot be adjudicated" in revert_message(excinfo)
    assert host.nondet_runs[-1]["validator_agreed"] is True


def test_a_supplied_rubric_keeps_the_panel_out_of_rubric_authoring(host, enforcer, accounts):
    """
    An integrator that does not want a model writing its rubric supplies one, and
    the panel then rules on admissibility only.
    """
    created = create(host, enforcer, accounts)
    assert created["rubric_authored_by_panel"] is False
    assert created["checklist_a"] == CHECKLIST_A

    rubric = json.loads(enforcer.get_rubric("pact-1", "A"))
    assert rubric["authored_by_panel"] is False
    assert rubric["checklist"] == CHECKLIST_A
    assert rubric["total_points"] == 6


# ---------------------------------------------------------------------------
# Operation 2: evidence adjudication
# ---------------------------------------------------------------------------

def test_adjudication_runs_the_whole_function_on_the_validator(host, enforcer, accounts):
    """
    The validator fetches the evidence itself. Two fetches and two model calls for
    one submission is what distinguishes a validator that redid the work from one
    that read the leader's answer.
    """
    active_pact(host, enforcer, accounts)
    host.web_calls.clear()
    host.llm_calls.clear()

    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    submit(enforcer, accounts, side="A")

    # The fetch helper renders first, so each side's fetch lands on the render
    # channel rather than the raw GET channel.
    assert len(host.rendered) == 2
    assert len(host.llm_calls) == 2


@pytest.mark.parametrize(
    "verdicts,tier,credit,passed",
    [
        (ALL_PRESENT_3, "COMPLETE", 100, True),
        (["PRESENT", "PRESENT", "PARTIAL"], "SUBSTANTIAL", 80, True),
        (TWO_OF_THREE, "PARTIAL", 50, False),
        (["PRESENT", "PARTIAL", "ABSENT"], "PARTIAL", 50, False),
        (ONE_OF_THREE, "MINIMAL", 20, False),
        (["PARTIAL", "ABSENT", "ABSENT"], "MINIMAL", 20, False),
        (NONE_OF_THREE, "NONE", 0, False),
    ],
)
def test_the_tier_is_arithmetic_over_item_verdicts(host, enforcer, accounts, verdicts, tier, credit, passed):
    """
    The panel is never asked for a tier or a score. It marks each rubric item, and
    the contract derives the tier from the point ratio. Expected values are computed
    by an independent reimplementation, so this checks the mapping rather than
    restating it.
    """
    assert tier_of(verdicts) == tier and credit_of(tier) == credit

    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(verdicts))

    out = submit(enforcer, accounts, side="A")
    assert out["tier"] == tier
    assert out["credit"] == credit
    assert out["passed"] is passed
    assert out["rubric_points"] == f"{sum({'PRESENT': 2, 'PARTIAL': 1}.get(v, 0) for v in verdicts)}/6"


def test_a_validator_on_the_other_side_of_the_threshold_fails_the_block(host, enforcer, accounts):
    """
    The binary verdict is the part that decides whether a side keeps its whole
    stake, so it is never given slack. COMPLETE against PARTIAL is a pass against a
    fail, and that must never settle.
    """
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    host.mock_llm(
        SCORING_PATTERN,
        [scoring_reply(ALL_PRESENT_3), scoring_reply(TWO_OF_THREE)],
    )
    with pytest.raises(ConsensusFailure):
        submit(enforcer, accounts, side="A")

    # Nothing was recorded, so the party's attempt survives a failed block.
    ob = json.loads(enforcer.get_obligation("pact-1", "A"))
    assert ob["attempts_used"] == 0
    assert ob["state"] == "OPEN"


def test_tiers_two_steps_apart_fail_even_on_the_same_side_of_the_threshold(host, enforcer, accounts):
    """
    PARTIAL and NONE are both failures, so the binary verdict agrees, but they mean
    half the work and none of it. Two tier steps is a real disagreement about how
    much was delivered, and it decides how much money moves.
    """
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    host.mock_llm(
        SCORING_PATTERN,
        [scoring_reply(TWO_OF_THREE), scoring_reply(NONE_OF_THREE)],
    )
    with pytest.raises(ConsensusFailure):
        submit(enforcer, accounts, side="A")


def test_adjacent_tiers_one_point_apart_are_accepted_within_the_tolerance(host, enforcer, accounts):
    """
    The documented concession. A rubric scored five of six points by one panel and
    four of six by another is genuinely borderline work, and failing consensus on
    every borderline pact would make the primitive unusable. Both readings are
    failures here, so the binary verdict still agrees exactly.
    """
    active_pact(host, enforcer, accounts, options=default_options(tier_tolerance_points=1))
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    host.mock_llm(
        SCORING_PATTERN,
        [
            scoring_reply(["PRESENT", "PARTIAL", "ABSENT"]),   # 3 of 6, PARTIAL
            scoring_reply(["PARTIAL", "PARTIAL", "ABSENT"]),   # 2 of 6, MINIMAL
        ],
    )
    assert tier_of(["PRESENT", "PARTIAL", "ABSENT"]) == "PARTIAL"
    assert tier_of(["PARTIAL", "PARTIAL", "ABSENT"]) == "MINIMAL"

    out = submit(enforcer, accounts, side="A")
    # The leader's tier is what was stored, which is the bounded discretion the
    # tolerance grants: one tier step, and only on a borderline reading.
    assert out["tier"] == "PARTIAL"
    assert out["rubric_points"] == "3/6"


def test_a_zero_tolerance_pact_requires_identical_tiers(host, enforcer, accounts):
    """A steward or creator that wants no leader discretion at all sets the tolerance to zero."""
    active_pact(host, enforcer, accounts, options=default_options(tier_tolerance_points=0))
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    host.mock_llm(
        SCORING_PATTERN,
        [
            scoring_reply(["PRESENT", "PARTIAL", "ABSENT"]),   # 3 of 6, PARTIAL
            scoring_reply(["PARTIAL", "PARTIAL", "ABSENT"]),   # 2 of 6, MINIMAL
        ],
    )
    # The same pair the previous test accepted at tolerance 1.
    with pytest.raises(ConsensusFailure):
        submit(enforcer, accounts, side="A")


def test_the_tolerance_never_lets_a_pass_and_a_fail_agree(host, enforcer, accounts):
    """
    SUBSTANTIAL and PARTIAL are adjacent tiers one point apart, which the tolerance
    would ordinarily admit. They sit either side of the threshold, so the binary
    check refuses them anyway. This is the guarantee the tolerance must never break.
    """
    active_pact(host, enforcer, accounts, options=default_options(tier_tolerance_points=2))
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    host.mock_llm(
        SCORING_PATTERN,
        [
            scoring_reply(["PRESENT", "PRESENT", "PARTIAL"]),  # 5 of 6, SUBSTANTIAL, passes
            scoring_reply(["PRESENT", "PRESENT", "ABSENT"]),   # 4 of 6, PARTIAL, fails
        ],
    )
    with pytest.raises(ConsensusFailure):
        submit(enforcer, accounts, side="A")


def test_two_panels_reading_different_page_content_can_still_agree(host, enforcer, accounts):
    """
    A leader's fetch and a validator's fetch of the same live page are allowed to
    differ. Nothing binds the content, so two panels that reach the same tier from
    slightly different bytes agree, which is what makes real web evidence usable.
    """
    active_pact(host, enforcer, accounts)
    host.mock_web(
        re.escape(EVIDENCE_A) + r"$",
        [
            {"status": 200, "body": BODY_A_COMPLETE + " Page generated at 09:14:02."},
            {"status": 200, "body": BODY_A_COMPLETE + " Page generated at 09:14:07."},
        ],
    )
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    out = submit(enforcer, accounts, side="A")
    assert out["passed"] is True
    assert len(host.rendered) == 2


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def test_a_dead_evidence_url_reverts_cleanly_and_costs_no_attempt(host, enforcer, accounts):
    """
    A 404 is deterministic, so both sides see the same one and agree on the failure.
    The transaction reverts whole, which leaves the party's attempt intact. That is
    the right outcome for a URL that is simply wrong.
    """
    active_pact(host, enforcer, accounts)
    host.mock_web(re.escape(EVIDENCE_A) + r"$", {"status": 404, "body": ""})
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))

    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="A")
    assert "[EXTERNAL]" in revert_message(excinfo)
    assert "404" in revert_message(excinfo)
    assert host.nondet_runs[-1]["validator_agreed"] is True

    ob = json.loads(enforcer.get_obligation("pact-1", "A"))
    assert ob["attempts_used"] == 0
    assert ob["attempts_remaining"] == 3
    # The URL is not burned either, so the party can retry the same page once it works.
    assert enforcer.evidence_already_used("pact-1", "A", EVIDENCE_A) is False


def test_two_sides_hitting_a_transient_failure_agree_on_it(host, enforcer, accounts):
    """
    A 503 on one fetch and a 503 on another are the same event as far as the pact is
    concerned, so the sides agree and the transaction reverts rather than burning a
    consensus round.
    """
    active_pact(host, enforcer, accounts)
    host.mock_web(re.escape(EVIDENCE_A) + r"$", {"status": 503, "body": ""})
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))

    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="A")
    assert "[TRANSIENT]" in revert_message(excinfo)
    assert host.nondet_runs[-1]["validator_agreed"] is True


def test_a_leader_failure_the_validator_does_not_share_fails_the_block(host, enforcer, accounts):
    """
    The leader's fetch 404s and the validator's succeeds. They genuinely disagree, so
    the validator votes no and consensus rotates rather than accepting a failure the
    validator could not reproduce.
    """
    active_pact(host, enforcer, accounts)
    # Three entries, because the fetch helper renders first and only falls back to a
    # raw GET when the render came back too short to read. The leader therefore
    # consumes two entries reaching its 404, and the validator's render succeeds on
    # the third.
    host.mock_web(
        re.escape(EVIDENCE_A) + r"$",
        [
            {"status": 404, "body": ""},
            {"status": 404, "body": ""},
            {"status": 200, "body": BODY_A_COMPLETE},
        ],
    )
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))
    with pytest.raises(ConsensusFailure):
        submit(enforcer, accounts, side="A")


def test_an_empty_page_is_refused_rather_than_scored_as_a_failure(host, enforcer, accounts):
    """
    A page that served nothing readable is not evidence that nothing was delivered.
    Scoring it NONE would convert an infrastructure problem into a forfeited stake.
    """
    active_pact(host, enforcer, accounts)
    host.mock_web(re.escape(EVIDENCE_A) + r"$", {"status": 200, "body": "   "})
    serve_scoring(host, scoring_reply(ALL_PRESENT_3))

    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="A")
    assert "no readable content" in revert_message(excinfo)


# ---------------------------------------------------------------------------
# Model resilience
# ---------------------------------------------------------------------------

def test_a_model_reply_wrapped_in_prose_is_still_parsed(host, enforcer, accounts):
    """Models prepend explanations. Slicing to the outermost braces rescues it."""
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(
        host,
        "Here is my assessment of the evidence.\n```json\n" + scoring_reply(ALL_PRESENT_3) + "\n```\nHope that helps.",
    )
    assert submit(enforcer, accounts, side="A")["tier"] == "COMPLETE"


def test_a_model_reply_with_trailing_commas_is_still_parsed(host, enforcer, accounts):
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(
        host,
        '{"items": [{"n": 1, "verdict": "PRESENT"}, {"n": 2, "verdict": "PRESENT"}, '
        '{"n": 3, "verdict": "PRESENT"},], "score": 100, "note": "all there", "missing": "",}',
    )
    assert submit(enforcer, accounts, side="A")["tier"] == "COMPLETE"


@pytest.mark.parametrize(
    "word,expected",
    [("yes", "PRESENT"), ("satisfied", "PRESENT"), ("partially", "PARTIAL"),
     ("incomplete", "PARTIAL"), ("missing", "ABSENT"), ("not met", "ABSENT")],
)
def test_item_verdict_wording_is_normalized_on_both_sides(host, enforcer, accounts, word, expected):
    """
    A difference in phrasing must never register as a difference in judgment. Both
    sides normalize through the same mapping, so a leader saying "yes" and a
    validator saying "PRESENT" agree.
    """
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    host.mock_llm(
        SCORING_PATTERN,
        [
            scoring_reply([word, word, word], score=credit_of(tier_of([expected] * 3))),
            scoring_reply([expected] * 3),
        ],
    )
    out = submit(enforcer, accounts, side="A")
    assert out["tier"] == tier_of([expected] * 3)


def test_an_unreadable_item_verdict_fails_closed_to_absent(host, enforcer, accounts):
    """An unreadable verdict is not evidence that a deliverable was supplied."""
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(["PRESENT", "mostly there I think", "PRESENT"], score=70))
    out = submit(enforcer, accounts, side="A")
    assert out["panel_item_verdicts"] == ["PRESENT", "ABSENT", "PRESENT"]
    assert out["rubric_points"] == "4/6"


def test_a_short_verdict_list_scores_the_missing_entries_absent(host, enforcer, accounts):
    """
    A panel that answers two of three questions must not thereby raise the ratio on
    the two it did answer.
    """
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(["PRESENT", "PRESENT"], score=67))
    out = submit(enforcer, accounts, side="A")
    assert out["rubric_points"] == "4/6"
    assert out["tier"] == "PARTIAL"


def test_a_panel_contradicting_its_own_item_marks_is_refused(host, enforcer, accounts):
    """
    Every item absent with an overall score of 95 is not a usable reading. This fails
    on the side that produced it, so it never becomes a cross side comparison problem.
    """
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(NONE_OF_THREE, score=95))

    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="A")
    assert "[LLM_ERROR]" in revert_message(excinfo)


def test_a_model_returning_no_json_at_all_is_refused(host, enforcer, accounts):
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, "I am unable to assess this evidence.")
    with pytest.raises(Exception) as excinfo:
        submit(enforcer, accounts, side="A")
    assert "[LLM_ERROR]" in revert_message(excinfo)


def test_a_model_failure_makes_the_sides_disagree_and_forces_rotation(host, enforcer, accounts):
    """
    Agreeing on broken model output is how bad state gets locked in. The leader's
    reply is unparseable and the validator's is fine, so the validator votes no.
    """
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    host.mock_llm(SCORING_PATTERN, ["not json at all", scoring_reply(ALL_PRESENT_3)])
    with pytest.raises(ConsensusFailure):
        submit(enforcer, accounts, side="A")


def test_both_sides_failing_on_model_output_still_disagree(host, enforcer, accounts):
    """
    Two model failures are not an agreement. Deterministic failures match and
    transient ones pair up, but a model failure always votes no, which is what
    forces rotation onto a fresh panel instead of settling on nonsense.
    """
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    host.mock_llm(SCORING_PATTERN, ["not json", "also not json"])
    with pytest.raises(ConsensusFailure):
        submit(enforcer, accounts, side="A")


# ---------------------------------------------------------------------------
# What is never consensus bound
# ---------------------------------------------------------------------------

def test_panel_prose_differs_freely_without_affecting_consensus(host, enforcer, accounts):
    """
    No prose a panel wrote is ever bound, and nothing reads it back. Two panels with
    completely different wording and different advisory scores still agree, because
    only the tier is compared.
    """
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    host.mock_llm(
        SCORING_PATTERN,
        [
            scoring_reply(ALL_PRESENT_3, score=100, note="Everything promised is present.", missing=""),
            scoring_reply(ALL_PRESENT_3, score=88, note="Dataset, schema and digest all located.", missing="nothing"),
        ],
    )
    out = submit(enforcer, accounts, side="A")
    assert out["passed"] is True
    assert out["panel_note"] == "Everything promised is present."
    assert out["panel_score_advisory"] == 100


def test_the_finding_of_record_is_derived_not_authored(host, enforcer, accounts):
    """
    What the contract treats as the finding is built from consensus bound fields and
    on chain data, so every validator renders identical bytes. The sentence the panel
    wrote is kept separately and clearly labelled.
    """
    active_pact(host, enforcer, accounts)
    serve_evidence(host, EVIDENCE_A, BODY_A_COMPLETE)
    serve_scoring(host, scoring_reply(TWO_OF_THREE, note="A rather different sentence entirely."))

    out = submit(enforcer, accounts, side="A")
    expected = (
        "Party A scored 4 of 6 rubric points on the submitted evidence, "
        "which is tier PARTIAL and 50 credit. That did not meet the pact's "
        "pass threshold of 80."
    )
    assert out["finding"] == expected
    assert "rather different sentence" not in out["finding"]

    record = json.loads(enforcer.get_adjudication("pact-1", "A", 1))
    assert record["finding"] == expected
    assert record["panel_note"] == "A rather different sentence entirely."
    assert record["consensus_bound"]["tier"] == "PARTIAL"
    assert record["consensus_bound"]["earned_points"] == 4
