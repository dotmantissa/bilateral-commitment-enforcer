"""
Shared fixtures for the Bilateral Commitment Enforcer suite.

Every test runs the real contract source through the real GenLayer SDK using the
in-process GenVM host in `genvm_host.py`. Storage goes through the SDK's own slot
engine, nondeterministic blocks go through the real `gl.vm.run_nondet_unsafe`, and
validator closures are genuinely executed against leader output they did not
produce. Nothing about the contract is stubbed.

The only substituted surfaces are the two the node itself owns, the web fetch and
the model call, which is exactly what `direct_vm.mock_web` and `direct_vm.mock_llm`
substitute in GenLayer's own tooling. Handing those two channels different answers
on the leader's call and the validator's call is how real validator disagreement
is reproduced offline.
"""

from __future__ import annotations

import json
import re
import sys

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from genvm_host import bootstrap, ConsensusFailure, NoRouteError  # noqa: E402

ENFORCER_SOURCE = ROOT / "contracts" / "bilateral_commitment_enforcer.py"
RELEASE_SOURCE = ROOT / "contracts" / "commitment_gated_release.py"

# A fixed point in time. Every test that cares about the clock warps explicitly.
T0 = 1_800_000_000  # 2027-01-15T08:00:00Z

DAY = 24 * 3600

ONE = 10**18  # one whole token in wei, so stakes read naturally

ZERO = "0x" + "00" * 20


# ---------------------------------------------------------------------------
# A realistic pact
#
# Two commitments that are genuinely interdependent, specific enough to be
# adjudicable, and sized so that the deliverables split cleanly. The dataset side
# is the one that goes first in every chained test.
# ---------------------------------------------------------------------------

COMMITMENT_A = (
    "Publish the anonymised Q3 lending dataset as a CSV at a public URL, together "
    "with a schema document describing every column, and a SHA-256 checksum for the "
    "CSV file."
)
COMMITMENT_B = (
    "Publish a written integration report at a public URL showing the dataset loaded "
    "into the risk model, including the resulting default curve and a list of every "
    "row rejected during loading."
)

CHECKLIST_A = [
    "An anonymised Q3 lending dataset published as a CSV at a public URL",
    "A schema document describing every column of the CSV",
    "A SHA-256 checksum for the published CSV file",
]
CHECKLIST_B = [
    "A written integration report published at a public URL",
    "The resulting default curve from the risk model",
    "A list of every row rejected during loading",
]

EVIDENCE_A = "https://evidence.example/dataset-q3"
EVIDENCE_B = "https://evidence.example/integration-report"

# Evidence bodies long enough to clear the fetch helper's readability floor, and
# written so a human could check them against the checklists above.
BODY_A_COMPLETE = (
    "Q3 Lending Dataset Release. The anonymised Q3 lending dataset is published here "
    "as lending-q3.csv, 41820 rows. The schema document below describes all fourteen "
    "columns including loan_id, originated_at, principal_cents, term_months, and "
    "default_flag. SHA-256 checksum of lending-q3.csv is "
    "9f2c4a1be8d35607c4f1a9e0b7d2358e4c6a0f19b3d7e582a4c91f06d8b3e7a5."
)
BODY_A_PARTIAL = (
    "Q3 Lending Dataset Release. The anonymised Q3 lending dataset is published here "
    "as lending-q3.csv, 41820 rows. A schema document covering all fourteen columns "
    "is included below. We have not yet generated a checksum for the file and will "
    "add one in a later revision of this page."
)
BODY_B_COMPLETE = (
    "Integration Report. The Q3 lending dataset was loaded into the risk model on "
    "4 October. The resulting default curve is plotted below and tabulated by vintage "
    "month, rising from 0.8 percent at month three to 4.1 percent at month eighteen. "
    "During loading, 217 rows were rejected and every one is listed in the rejection "
    "appendix at the end of this report with its loan_id and the reason."
)


# ---------------------------------------------------------------------------
# Panel reply builders
#
# These produce the JSON a model returns. Tests compose them to drive agreement,
# disagreement, and malformed output.
# ---------------------------------------------------------------------------

def admissibility_reply(
    a_ok: bool = True,
    b_ok: bool = True,
    a_defect: str = "none",
    b_defect: str = "none",
    a_items: list | None = None,
    b_items: list | None = None,
    a_reason: str = "Names concrete artefacts reachable at a public URL.",
    b_reason: str = "Names a concrete published report with checkable contents.",
) -> str:
    """A well formed admissibility reply, as JSON text."""
    return json.dumps(
        {
            "a": {
                "adjudicable": a_ok,
                "defect": a_defect,
                "reason": a_reason,
                "deliverables": (CHECKLIST_A if a_items is None else a_items) if a_ok else [],
            },
            "b": {
                "adjudicable": b_ok,
                "defect": b_defect,
                "reason": b_reason,
                "deliverables": (CHECKLIST_B if b_items is None else b_items) if b_ok else [],
            },
        }
    )


def scoring_reply(
    verdicts: list,
    score: int | None = None,
    note: str = "The evidence shows the published artefacts.",
    missing: str = "",
    substantiated: bool | None = None,
) -> str:
    """
    A well formed scoring reply.

    `score` defaults to something coherent with the verdicts, because the contract
    rejects a panel whose overall reading contradicts its own item marks.
    """
    if score is None:
        points = sum({"PRESENT": 2, "PARTIAL": 1}.get(v, 0) for v in verdicts)
        total = max(1, len(verdicts)) * 2
        score = round(points * 100 / total)
    body: dict = {
        "items": [{"n": i + 1, "verdict": v, "why": "as seen"} for i, v in enumerate(verdicts)],
        "score": score,
        "missing": missing,
        "note": note,
    }
    if substantiated is not None:
        body["substantiated"] = substantiated
    return json.dumps(body)


ALL_PRESENT_3 = ["PRESENT", "PRESENT", "PRESENT"]
TWO_OF_THREE = ["PRESENT", "PRESENT", "ABSENT"]
ONE_OF_THREE = ["PRESENT", "ABSENT", "ABSENT"]
NONE_OF_THREE = ["ABSENT", "ABSENT", "ABSENT"]


# ---------------------------------------------------------------------------
# Independent reimplementations
#
# Deliberately written out here rather than imported from the contract, so a test
# that checks a derivation is checking the contract rather than agreeing with it.
# ---------------------------------------------------------------------------

def tier_of(verdicts: list) -> str:
    """Re-derive a tier from item verdicts the way an outside reviewer would."""
    earned = sum({"PRESENT": 2, "PARTIAL": 1}.get(str(v), 0) for v in verdicts)
    total = max(1, len(verdicts)) * 2
    if earned == total:
        return "COMPLETE"
    if earned * 100 >= total * 80:
        return "SUBSTANTIAL"
    if earned * 100 >= total * 40:
        return "PARTIAL"
    if earned > 0:
        return "MINIMAL"
    return "NONE"


def credit_of(tier: str) -> int:
    """Canonical credit for a tier."""
    return {"COMPLETE": 100, "SUBSTANTIAL": 80, "PARTIAL": 50, "MINIMAL": 20, "NONE": 0}[tier]


def settle_of(stake_a: int, stake_b: int, eff_a: int, eff_b: int, sink: bool = False) -> dict:
    """
    Re-derive a settlement the way an outside reviewer would.

    An independent implementation of the contract's `_compute_settlement`, so the
    settlement tests check the arithmetic rather than restate it.
    """
    forfeit_a = stake_a * (100 - eff_a) // 100
    forfeit_b = stake_b * (100 - eff_b) // 100
    both = forfeit_a > 0 and forfeit_b > 0
    if both and sink:
        return {
            "payout_a": stake_a - forfeit_a,
            "payout_b": stake_b - forfeit_b,
            "sink": forfeit_a + forfeit_b,
            "forfeit_a": forfeit_a,
            "forfeit_b": forfeit_b,
        }
    return {
        "payout_a": stake_a - forfeit_a + forfeit_b,
        "payout_b": stake_b - forfeit_b + forfeit_a,
        "sink": 0,
        "forfeit_a": forfeit_a,
        "forfeit_b": forfeit_b,
    }


def terms_digest_of(
    pact_id: str,
    party_a: str,
    party_b: str,
    stake_a: int,
    stake_b: int,
    threshold: int,
    dependency: str,
    attempts: int,
    challenge_window: int,
    tolerance: int,
    sink: str,
    commitment_a: str,
    commitment_b: str,
    window_a: int,
    window_b: int,
    checklist_a: list,
    checklist_b: list,
) -> str:
    """
    Recompute a pact's terms digest the way an outside reviewer would.

    An independent implementation of the contract's `_compute_terms_digest`, down to
    the canonicalization and the Keccak-256, so the consent test checks the
    commitment rather than agreeing with it.
    """
    from Crypto.Hash import keccak

    def fold(text) -> str:
        lowered = re.sub(r"\s+", " ", str(text)).strip().lower()
        return re.sub(r"[^a-z0-9]+", " ", lowered).strip()

    def digest(*parts) -> str:
        hasher = keccak.new(digest_bits=256)
        hasher.update("\x1f".join(str(p) for p in parts).encode("utf-8"))
        return hasher.hexdigest()

    def checklist_digest(items: list) -> str:
        return digest("checklist", str(len(items)), *[fold(i) for i in items])

    return digest(
        "pact-terms-v1",
        fold(pact_id),
        party_a,
        party_b,
        str(stake_a),
        str(stake_b),
        str(threshold),
        dependency,
        str(attempts),
        str(challenge_window),
        str(tolerance),
        sink,
        digest("commitment", "A", fold(commitment_a)),
        digest("commitment", "B", fold(commitment_b)),
        str(window_a),
        str(window_b),
        checklist_digest(checklist_a),
        checklist_digest(checklist_b),
    )


# ---------------------------------------------------------------------------
# Host fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def genvm():
    """The GenVM host. One per session, since the SDK imports are process wide."""
    return bootstrap()


@pytest.fixture
def host(genvm):
    """A clean host for each test: fresh storage, no routes, no call log."""
    genvm.reset()
    genvm.warp(T0)
    return genvm


@pytest.fixture
def accounts(host):
    """Named test addresses."""
    return {
        "deployer": host.new_address(),
        "alice": host.new_address(),
        "bob": host.new_address(),
        "carol": host.new_address(),
        "sink": host.new_address(),
        "outsider": host.new_address(),
    }


@pytest.fixture
def enforcer(host, accounts):
    """A deployed enforcer registry with no pacts."""
    return host.deploy(
        ENFORCER_SOURCE,
        "BilateralCommitmentEnforcer",
        args=["Bilateral Commitment Registry"],
        sender=accounts["deployer"],
    )


# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------

ADMISSIBILITY_PATTERN = r"independent panel reviewing a two party"
SCORING_PATTERN = r"deciding whether party [AB]\s+of a two party pact performed"


def serve_admissibility(host, reply=None) -> None:
    """Register the admissibility reply the panel will return."""
    host.mock_llm(ADMISSIBILITY_PATTERN, reply if reply is not None else admissibility_reply())


def serve_scoring(host, reply, side: str | None = None) -> None:
    """
    Register the scoring reply the panel will return.

    Passing `side` scopes the route to one party's adjudication, which is how a
    test gives party A and party B different outcomes in the same pact.
    """
    pattern = SCORING_PATTERN if side is None else rf"deciding whether party {side}\s+of a two party pact performed"
    host.mock_llm(pattern, reply)


def serve_evidence(host, url: str = EVIDENCE_A, body: str = BODY_A_COMPLETE) -> None:
    """Serve an evidence document at its URL."""
    host.mock_web(re.escape(url) + r"$", {"status": 200, "body": body})


# ---------------------------------------------------------------------------
# Pact helpers
# ---------------------------------------------------------------------------

def default_options(**overrides) -> dict:
    """
    The options bag used by most tests.

    Checklists are supplied explicitly by default, so that a test about settlement
    is not also a test about whether a panel authored three items or four. The
    tests that care about panel authoring leave them out on purpose.
    """
    options = {
        "checklist_a": CHECKLIST_A,
        "checklist_b": CHECKLIST_B,
        "pass_threshold": 80,
        "max_attempts": 3,
        "challenge_window": 0,
        "funding_window": 7 * DAY,
        "tier_tolerance_points": 1,
    }
    options.update(overrides)
    return options


def create(
    host,
    enforcer,
    accounts,
    pact_id: str = "pact-1",
    dependency: str = "none",
    stake_a: int = 10 * ONE,
    stake_b: int = 10 * ONE,
    window_a: int = 7 * DAY,
    window_b: int = 7 * DAY,
    commitment_a: str = COMMITMENT_A,
    commitment_b: str = COMMITMENT_B,
    options: dict | None = None,
    sender=None,
    party_b=None,
    serve: bool = True,
) -> dict:
    """Create a pact, serving the admissibility route unless the test did it already."""
    if serve:
        serve_admissibility(host)
    return json.loads(
        enforcer.create_pact(
            pact_id,
            "Dataset for integration report",
            party_b or accounts["bob"],
            commitment_a,
            commitment_b,
            str(stake_b),
            dependency,
            window_a,
            window_b,
            json.dumps(default_options() if options is None else options),
            sender=sender or accounts["alice"],
            value=stake_a,
        )
    )


def accept(enforcer, accounts, pact_id: str = "pact-1", stake_b: int = 10 * ONE, digest=None, sender=None) -> dict:
    """Accept a pact as party B, reading the terms digest off chain by default."""
    return json.loads(
        enforcer.accept_pact(
            pact_id,
            digest if digest is not None else enforcer.terms_digest(pact_id),
            sender=sender or accounts["bob"],
            value=stake_b,
        )
    )


def active_pact(host, enforcer, accounts, **kwargs) -> dict:
    """A created and accepted pact, ready for evidence."""
    stake_b = kwargs.get("stake_b", 10 * ONE)
    pact_id = kwargs.get("pact_id", "pact-1")
    created = create(host, enforcer, accounts, **kwargs)
    accepted = accept(enforcer, accounts, pact_id=pact_id, stake_b=stake_b)
    return {"created": created, "accepted": accepted}


def submit(enforcer, accounts, pact_id: str = "pact-1", side: str = "A", url: str | None = None) -> dict:
    """Submit evidence as one side, defaulting to that side's usual URL."""
    sender = accounts["alice"] if side == "A" else accounts["bob"]
    target = url if url is not None else (EVIDENCE_A if side == "A" else EVIDENCE_B)
    return json.loads(enforcer.submit_evidence(pact_id, target, sender=sender))


def resolve(enforcer, accounts, pact_id: str = "pact-1", sender=None) -> dict:
    """Resolve a pact, by default as an uninvolved third party."""
    return json.loads(enforcer.resolve_pact(pact_id, sender=sender or accounts["outsider"]))


def transfers(host) -> dict:
    """
    Every value transfer the contract queued, keyed by recipient address.

    `emit_transfer` reaches the node as a posted message, so this is where a
    settlement's actual payouts show up.
    """
    out: dict = {}
    for message in host.posted_messages:
        out[message["address"]] = out.get(message["address"], 0) + int(message["value"])
    return out


def revert_message(excinfo) -> str:
    """The revert reason from a caught contract error."""
    exc = excinfo.value
    return str(getattr(exc, "message", None) or exc)
