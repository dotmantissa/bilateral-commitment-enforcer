# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""
Bilateral Commitment Enforcer
=============================

Purpose
-------
Commercial relationships run on mutual, interdependent obligations. "I ship the
dataset by Friday if you wire the payment by Thursday." "I publish the article
once you hand over the research." "I complete the audit once you freeze the
codebase." A conventional smart contract can release a payment when a condition
is met, which handles one side. It cannot handle both sides at once, because the
other side's obligation is prose, the evidence that it was performed is a web
page, and whether that page discharges the promise is a judgment.

This contract holds both parties' stakes simultaneously and adjudicates each
party's own stated commitment. Each party's commitment is written in natural
language when the pact is created. When a party believes it has performed, it
submits a publicly reachable evidence URL: a deployed contract, a published
document, a shipped artefact, a commit. GenLayer validators independently fetch
that evidence and rule on it, producing a pass or fail verdict and a partial
credit score for work that was mostly but not entirely done.

Two independent escrows side by side would not be this contract. The feature
that makes it one instrument is dependency chaining. If party B's obligation
does not begin until party A has delivered, that is declared when the pact is
created, and B's deadline clock does not start until A's fulfilment has been
confirmed by consensus. Real sequential workflows follow: data released after an
NDA is confirmed, payment after a milestone lands, a code freeze after an audit
deposit. Settlement then resolves all four outcomes, mutual fulfilment, A in
breach, B in breach, and mutual breach, with proportional stake transfers driven
by the fulfilment scores.

Why this needs GenLayer
-----------------------
Every hard step here is a reading, not a computation.

"Publish a migration guide covering the v2 breaking changes, with runnable
examples for each" is a promise. `https://docs.example/migrating-to-v2` is a web
page. Deciding whether the page discharges the promise means fetching it,
understanding what the promise asked for, and checking the page against it. An
EVM contract cannot do any of the three. An off-chain oracle can, but then one
server decides who keeps the money, and a bilateral escrow whose adjudicator is
a single party is just that party's escrow.

GenLayer's Optimistic Democracy runs the reading on many validators at once.
Each validator fetches the evidence itself, receives the identical rubric from
contract storage, and reaches its own verdict. The equivalence principle decides
whether those verdicts agree closely enough to move money. No validator's
reading is authoritative on its own, and a validator that disagrees forces the
appeal path instead of silently letting the leader settle the pact.

The dependency chain inherits that guarantee for free. B's clock starts when A's
fulfilment is confirmed, and that confirmation is consensus bound, so the moment
B's obligation becomes live is itself a consensus fact rather than one party's
assertion.

Consensus model
---------------
Three nondeterministic operations, each with a purpose built leader and
validator pair through `gl.vm.run_nondet_unsafe`. No operation trusts the
leader's answer; every validator redoes the work and compares outcomes.

1. Admissibility and rubric extraction (`create_pact`)

   Leader: reads both commitments and decides whether each is specific enough to
   be adjudicated against public evidence at all. For each admissible
   commitment it also breaks the promise into its discrete deliverables, which
   becomes the checklist every later adjudication scores against.

   Validator: reads the same two commitments and decides the same questions
   independently.

   Agreement requires, for each side, the admissibility boolean, the defect
   class when inadmissible, and the number of checklist items. All three are
   numbers or fixed enum values.

   The checklist *text* is deliberately not compared. Two honest panels asked to
   enumerate the deliverables of one promise write "ship the dataset" and
   "provide the data file", which are the same requirement in different words,
   and no majority ever forms on prose. What makes the rubric binding is consent
   rather than validator agreement, which the pact lifecycle enforces:
   `create_pact` leaves the pact PROPOSED with the rubric visible on chain,
   `accept_pact` makes party B pass the exact `terms_digest` that covers the
   rubric text, and `withdraw_proposal` lets party A walk away if it dislikes
   what the panel wrote. Neither side has a single wei at risk until both have
   consented to the exact text they will be judged by. A creator that does not
   want a panel authoring its rubric passes the checklists in explicitly, and
   the panel then rules on admissibility only.

2. Evidence adjudication (`submit_evidence`)

   Leader: fetches the evidence URL, then scores the stored checklist item by
   item against what it fetched. PRESENT is two points, PARTIAL one, ABSENT
   zero. Deterministic code turns the point ratio into a credit tier.

   Validator: reruns that entire function. Its own fetch, its own reading, its
   own item scores, its own tier. It does not inspect the shape of the leader's
   reply and it does not ask a model whether the leader looked reasonable.

   Agreement requires the exact credit tier, which is what sets the money.
   Adjacent tiers are rejected even when both fail and the point totals differ
   by only one. Point totals may differ within the same tier because all such
   readings produce the same canonical credit and settlement. The deprecated
   `tier_tolerance_points` term is retained for compatibility, but never relaxes
   this rule, regardless of the value supplied by a creator or steward.

3. Challenge re-adjudication (`challenge_fulfilment`)

   Leader: is given the original evidence, the stored checklist and the
   challenger's written grounds, and decides whether the grounds are
   substantiated, and if they are, re-scores the checklist.

   Validator: reruns it. Agreement requires the substantiated boolean, and the
   revised tier when substantiated.

Partial credit is tiered, and why
---------------------------------
A fulfilment score that sets a payout cannot be a raw number a panel wrote. Ask
five honest panels to score the same evidence out of a hundred and they return
seventy three, sixty eight, eighty, seventy five and sixty. Every one of those
readings is defensible, so no exact match forms, and whichever panel happened to
be leader would set the payout alone. Quantizing afterwards does not fix it, it
moves the disagreement to the band boundary, where a leader on seventy one and a
validator on sixty nine land in different bands while reading the evidence
identically.

So credit is not a number a panel writes. It is one of five tiers:

    COMPLETE      100   every deliverable present
    SUBSTANTIAL    80   performed with minor, non material gaps
    PARTIAL        50   a material part delivered, a material part missing
    MINIMAL        20   token effort, the substance absent
    NONE            0   nothing responsive to the commitment

and even the tier is not asked for directly. The panel scores the stored
checklist item by item, and `_tier_for_points` turns the point ratio into the
tier deterministically. Two panels that both find three of five deliverables
present reach the same tier by arithmetic rather than by agreeing on an
adjective. The credit that moves money is then a lookup on the tier, with no
leader discretion in it at all.

The binary pass or fail verdict is likewise never asked for. It is
`credit >= pass_threshold`, computed on chain from the agreed tier, so the
verdict and the score can never contradict each other.

Settlement
----------
One formula covers every outcome. For each side, with `credit` being the
confirmed partial credit and `stake` that side's own locked stake:

    effective = 100 if the side is FULFILLED or EXCUSED else credit
    forfeit   = stake * (100 - effective) / 100
    payout    = stake - forfeit + the other side's forfeit

A side that cleared the threshold forfeits nothing. A side that delivered
seventy percent of its deliverables forfeits thirty percent of its stake rather
than all of it, which is what partial credit is for. A side excused because its
predecessor never delivered forfeits nothing and collects the predecessor's
forfeit as damages. The four outcomes the pact can reach are all this formula:

    MUTUAL_FULFILMENT   both forfeits zero, each side withdraws its own stake
    A_BREACH            A forfeits by shortfall, B collects it
    B_BREACH            the mirror
    MUTUAL_BREACH       both forfeit; the forfeits cross, or both go to the
                        declared sink

`payout_a + payout_b + sink == stake_a + stake_b` holds for every reachable
state. The contract asserts it before it moves anything, and the test suite
asserts it across the whole outcome space.

Mutual breach deserves its own note. With the forfeits crossing and equal
stakes, two sides that both did nothing get their money back, which is the
honest result of a symmetric mutual breach and is where contract law lands too.
It does leave mutual non performance costless, so the pact may declare a
`mutual_breach_sink`, and when both sides breach the forfeits go there instead
of netting out. That is opt in, it still conserves value, and it is the only
deterrent available to a two party instrument with no protocol treasury.

Dependency chaining
-------------------
`dependency` is declared once at creation and is one of `none`, `b_after_a` or
`a_after_b`. Mutual dependency is refused, because it deadlocks.

Obligation state is never stored as a status field that a write has to remember
to update. The contract stores facts, which are the activation time, the
deadline, the attempts used, the confirmed credit and the confirmation time, and
`_derive_states` computes both sides' live states from those facts and the
clock. The independent side is computed first, so there is no recursion and no
pair of fields that can disagree with each other.

    WAITING     the clock has not started, because the predecessor has not been
                confirmed yet. Evidence submission is refused, which is the
                whole point of declaring a dependency.
    OPEN        the clock is running and attempts remain
    FULFILLED   a confirmed adjudication cleared the pass threshold
    BREACHED    the deadline passed, or the attempts ran out, without clearing it
    EXCUSED     the predecessor breached, so this obligation never became live

`accept_pact` opens the independent side or sides. When a predecessor is
confirmed FULFILLED, that same transaction writes the dependent side's
activation and deadline, so the clock starts from a consensus bound fact. When a
predecessor breaches, the dependent side derives as EXCUSED with no transaction
needed from anyone.

Replay protection
-----------------
`max_attempts` lets a party resubmit after a panel tells it what is missing,
which is how real review works. Without replay protection it would also be a
free re-roll: submit the identical URL five times and keep the luckiest reading.
So each pact and side records a digest of every normalized evidence URL it has
already had adjudicated, and refuses a repeat. The digest is taken over the URL
in deterministic code, never over the fetched content, because the content a
leader fetches and the content a validator fetches are allowed to differ.

What is deterministic and what is not
-------------------------------------
Deterministic, and therefore identical on every validator: every deadline, every
state derivation, the point to tier mapping, the tier to credit mapping, the pass
or fail verdict, the whole settlement arithmetic, replay protection, consent
digests, and the human readable finding that `_render_finding` builds from
consensus bound fields.

Nondeterministic, and therefore reached only through an equivalence principle:
fetching an evidence URL, deciding whether a commitment is adjudicable, breaking
a commitment into deliverables, scoring those deliverables against evidence, and
ruling on challenge grounds.

No prose a panel wrote is ever consensus bound, and no prose a panel wrote ever
feeds a later decision. Advisory prose is recorded, clearly labelled as the
leader's own words, for the humans reading the record.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

import json
import re
import typing

from genlayer import *


# ---------------------------------------------------------------------------
# Error classification
#
# Validators have to know how to compare a failure, not only a success. A 404 is
# deterministic and both sides must see the same one. A 503 is not, and two sides
# that both hit one agree. A model that returned nonsense must make the sides
# disagree, because agreeing on broken output is how bad state gets locked in.
# ---------------------------------------------------------------------------

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"


# ---------------------------------------------------------------------------
# Credit tiers
# ---------------------------------------------------------------------------

TIER_COMPLETE = "COMPLETE"
TIER_SUBSTANTIAL = "SUBSTANTIAL"
TIER_PARTIAL = "PARTIAL"
TIER_MINIMAL = "MINIMAL"
TIER_NONE = "NONE"

TIER_CREDIT = {
    TIER_COMPLETE: 100,
    TIER_SUBSTANTIAL: 80,
    TIER_PARTIAL: 50,
    TIER_MINIMAL: 20,
    TIER_NONE: 0,
}

# Ordered worst to best for the public tier listing.
TIER_ORDER = [TIER_NONE, TIER_MINIMAL, TIER_PARTIAL, TIER_SUBSTANTIAL, TIER_COMPLETE]

# What a panel may say about one checklist item, and what it is worth.
ITEM_PRESENT = "PRESENT"
ITEM_PARTIAL = "PARTIAL"
ITEM_ABSENT = "ABSENT"

ITEM_POINTS = {ITEM_PRESENT: 2, ITEM_PARTIAL: 1, ITEM_ABSENT: 0}


# ---------------------------------------------------------------------------
# Obligation states, pact states, outcomes, dependency modes
# ---------------------------------------------------------------------------

STATE_WAITING = "WAITING"
STATE_OPEN = "OPEN"
STATE_FULFILLED = "FULFILLED"
STATE_BREACHED = "BREACHED"
STATE_EXCUSED = "EXCUSED"

TERMINAL_STATES = (STATE_FULFILLED, STATE_BREACHED, STATE_EXCUSED)

PACT_PROPOSED = "PROPOSED"
PACT_ACTIVE = "ACTIVE"
PACT_SETTLED = "SETTLED"
PACT_WITHDRAWN = "WITHDRAWN"

OUTCOME_MUTUAL_FULFILMENT = "MUTUAL_FULFILMENT"
OUTCOME_A_BREACH = "A_BREACH"
OUTCOME_B_BREACH = "B_BREACH"
OUTCOME_MUTUAL_BREACH = "MUTUAL_BREACH"
OUTCOME_RELEASED = "RELEASED"
OUTCOME_UNFUNDED = "WITHDRAWN_UNFUNDED"

DEP_NONE = "none"
DEP_B_AFTER_A = "b_after_a"
DEP_A_AFTER_B = "a_after_b"
DEPENDENCY_MODES = (DEP_NONE, DEP_B_AFTER_A, DEP_A_AFTER_B)

SIDE_A = "A"
SIDE_B = "B"

# Why a commitment cannot be adjudicated. A fixed taxonomy, because this value is
# consensus bound and free prose never is.
DEFECT_NONE = "none"
DEFECT_UNSPECIFIC = "unspecific"
DEFECT_NOT_PUBLICLY_VERIFIABLE = "not_publicly_verifiable"
DEFECT_SUBJECTIVE_ONLY = "subjective_only"
DEFECT_SELF_REFERENTIAL = "self_referential"
DEFECT_CLASSES = (
    DEFECT_NONE,
    DEFECT_UNSPECIFIC,
    DEFECT_NOT_PUBLICLY_VERIFIABLE,
    DEFECT_SUBJECTIVE_ONLY,
    DEFECT_SELF_REFERENTIAL,
)

ADJUDICATION_SUBMISSION = "SUBMISSION"
ADJUDICATION_CHALLENGE = "CHALLENGE"


# ---------------------------------------------------------------------------
# Limits
#
# Bounds exist so that one pact cannot make a later transaction exceed the
# compute limit, and so that a prompt cannot be stuffed with a megabyte of text.
# ---------------------------------------------------------------------------

MAX_PACT_ID = 128
MAX_TITLE = 300
MAX_COMMITMENT = 6000
MAX_URL = 2048
MAX_GROUNDS = 2000
MAX_CHECKLIST_ITEMS = 12
MIN_CHECKLIST_ITEMS = 1
MAX_CHECKLIST_ITEM = 400
MAX_EVIDENCE_CHARS = 40000
MAX_ATTEMPTS_CAP = 10
MAX_WINDOW_SECONDS = 365 * 24 * 3600
MIN_WINDOW_SECONDS = 60
MAX_FUNDING_WINDOW = 90 * 24 * 3600
MAX_CHALLENGE_WINDOW = 30 * 24 * 3600
MAX_ADVISORY_TEXT = 1200

ZERO_ADDRESS = "0x" + "00" * 20


# ---------------------------------------------------------------------------
# Deterministic text helpers
#
# Everything in this block runs identically on every validator. These functions
# are what make a digest comparable and a prompt reproducible, so none of them
# may ever consult the clock, the sender or a nondeterministic source.
# ---------------------------------------------------------------------------

def _collapse(text: typing.Any) -> str:
    """Collapse all whitespace runs to single spaces and trim."""
    return re.sub(r"\s+", " ", str(text)).strip()


def _fold(text: typing.Any) -> str:
    """
    Canonical comparison key: whitespace collapsed, case folded, punctuation
    reduced to single spaces. Two renderings of the same sentence that differ
    only in spacing, case or punctuation fold to the same key.
    """
    lowered = _collapse(text).lower()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _digest(*parts: typing.Any) -> str:
    """
    Keccak-256 over the unit separator joined parts.

    The separator matters. Joining on the empty string would let
    ("ab", "c") and ("a", "bc") collide, and a commitment digest that collides is
    a commitment two parties did not both agree to. 0x1f cannot occur in any of
    the folded keys fed in here.
    """
    hasher = Keccak256()
    hasher.update("\x1f".join(str(part) for part in parts).encode("utf-8"))
    return hasher.hexdigest()


def _normalize_url(url: typing.Any) -> str:
    """
    Canonical form of an evidence URL, for replay protection.

    Case folds the scheme and host but not the path, since paths are case
    sensitive on most servers and `/Report.pdf` is not `/report.pdf`. Drops a
    trailing slash and a bare fragment, because neither changes which document is
    fetched. Deliberately does not reorder or strip query parameters: on many
    hosts the query selects the document.
    """
    raw = _collapse(url)
    raw = raw.split("#", 1)[0]
    match = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*://)([^/?]*)(.*)$", raw)
    if match is None:
        return raw.rstrip("/")
    scheme, host, rest = match.group(1).lower(), match.group(2).lower(), match.group(3)
    combined = scheme + host + rest
    if combined.endswith("/") and not combined.endswith("://"):
        combined = combined[:-1]
    return combined


def _clean_item(text: typing.Any) -> str:
    """One checklist item, collapsed and length bounded."""
    return _collapse(text)[:MAX_CHECKLIST_ITEM]


def _checklist_digest(items: list) -> str:
    """
    Digest over a checklist's folded items, order preserving.

    Order is preserved rather than sorted because item scores are reported
    positionally, so a reordered checklist is a different rubric even when it
    holds the same requirements.
    """
    return _digest("checklist", str(len(items)), *[_fold(item) for item in items])


# ---------------------------------------------------------------------------
# Tier arithmetic
#
# The point total a panel produces becomes money through these two functions and
# nothing else. Both are pure, both are deterministic, and both run on chain
# rather than inside a nondeterministic block, so the mapping is not something a
# leader can influence.
# ---------------------------------------------------------------------------

def _tier_for_points(earned: int, items: int) -> str:
    """
    Turn a checklist point total into a credit tier.

    `items` is the number of checklist entries, so the maximum is twice that.
    The thresholds are deliberately wide, because the whole reason credit is
    tiered is that narrow bands put two honest panels on opposite sides of a
    boundary.

        every point            COMPLETE
        at least 80 percent    SUBSTANTIAL
        at least 40 percent    PARTIAL
        any point at all       MINIMAL
        no points              NONE
    """
    total = max(1, int(items)) * ITEM_POINTS[ITEM_PRESENT]
    earned = max(0, min(int(earned), total))
    if earned == total:
        return TIER_COMPLETE
    if earned * 100 >= total * 80:
        return TIER_SUBSTANTIAL
    if earned * 100 >= total * 40:
        return TIER_PARTIAL
    if earned > 0:
        return TIER_MINIMAL
    return TIER_NONE


def _credit_for_tier(tier: str) -> int:
    """Canonical credit for a tier. Unknown tiers score zero, never a default pass."""
    return int(TIER_CREDIT.get(str(tier), 0))


def _score_items(verdicts: list, items: int) -> int:
    """
    Total the points for a list of per item verdicts.

    A verdict list shorter than the checklist scores the missing entries as
    ABSENT rather than dropping them, so a panel that answers four of five
    questions cannot thereby raise the ratio on the four it did answer.
    """
    earned = 0
    for index in range(int(items)):
        verdict = verdicts[index] if index < len(verdicts) else ITEM_ABSENT
        earned += ITEM_POINTS.get(str(verdict), 0)
    return earned


# ---------------------------------------------------------------------------
# The derived finding
#
# A panel also writes a sentence explaining itself, and that sentence is recorded
# for human readers and is never read back by anything. What the contract treats
# as the finding of record is built here, from consensus bound fields and data
# already on chain, so any two validators render byte identical text.
#
# The lesson is inherited rather than guessed. A sibling primitive measured five
# independent panels given an identical prompt and identical inputs: all five
# reached the same verdict on the same ground, and all five wrote a different
# rule. Prose that two honest panels will not write the same way cannot be
# consensus bound, and prose a leader alone chooses cannot be allowed to bind
# anything later. So it binds nothing.
# ---------------------------------------------------------------------------

def _render_finding(
    side: str,
    tier: str,
    credit: int,
    passed: bool,
    earned: int,
    items: int,
    threshold: int,
    kind: str,
) -> str:
    """
    The finding of record for one adjudication.

    The tier, credit and pass/fail are consensus bound. The point total is the
    leader's advisory reading, which may differ from a validator's within the
    agreed tier. Rendering is deterministic on the accepted result and stored
    terms; this string never drives a later decision or settlement.
    """
    total = max(1, int(items)) * ITEM_POINTS[ITEM_PRESENT]
    verdict = "met" if passed else "did not meet"
    origin = "on re-adjudication after a challenge" if kind == ADJUDICATION_CHALLENGE else "on the submitted evidence"
    return (
        f"Party {side} scored {int(earned)} of {total} rubric points {origin}, "
        f"which is tier {tier} and {int(credit)} credit. That {verdict} the "
        f"pact's pass threshold of {int(threshold)}."
    )


# ---------------------------------------------------------------------------
# Model output parsing
#
# Models return approximately the asked for shape. Every field is therefore
# coerced rather than trusted, key aliases are accepted, and anything that cannot
# be rescued raises `[LLM_ERROR]` so that the sides disagree and consensus
# rotates onto a different panel instead of locking in nonsense.
# ---------------------------------------------------------------------------

def _as_dict(raw: typing.Any) -> dict:
    """
    Coerce a model reply to a dict.

    `response_format="json"` usually returns a parsed dict already, but a plain
    string still arrives often enough that the fallback has to be real: slice to
    the outermost braces, drop trailing commas, then parse.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        raise gl.vm.UserError(f"{ERROR_LLM} panel returned {type(raw).__name__}, expected an object")
    first, last = raw.find("{"), raw.rfind("}")
    if first < 0 or last <= first:
        raise gl.vm.UserError(f"{ERROR_LLM} panel reply contains no JSON object")
    body = re.sub(r",(\s*[}\]])", r"\1", raw[first : last + 1])
    try:
        parsed = json.loads(body)
    except Exception:
        raise gl.vm.UserError(f"{ERROR_LLM} panel reply is not parseable JSON")
    if not isinstance(parsed, dict):
        raise gl.vm.UserError(f"{ERROR_LLM} panel reply parsed to {type(parsed).__name__}, expected an object")
    return parsed


def _pick(source: dict, *names: str) -> typing.Any:
    """First present, non-null value among the given key aliases."""
    for name in names:
        if name in source and source[name] is not None:
            return source[name]
    return None


def _as_bool(value: typing.Any, field: str) -> bool:
    """
    Coerce a model boolean.

    Accepts real booleans and the words models substitute for them. Refuses
    anything else rather than defaulting, because defaulting a missing
    `adjudicable` to true would admit exactly the commitments this gate exists to
    refuse.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in ("true", "yes", "y", "1", "pass", "passed", "substantiated"):
            return True
        if token in ("false", "no", "n", "0", "fail", "failed", "unsubstantiated"):
            return False
    raise gl.vm.UserError(f"{ERROR_LLM} field '{field}' is not a boolean: {value!r}")


def _as_int(value: typing.Any, field: str, low: int, high: int) -> int:
    """Coerce and clamp a model integer. Handles ints, floats and numeric strings."""
    try:
        number = int(round(float(str(value).strip())))
    except Exception:
        raise gl.vm.UserError(f"{ERROR_LLM} field '{field}' is not numeric: {value!r}")
    return max(low, min(high, number))


def _normalize_defect(value: typing.Any) -> str:
    """
    Map a model's defect wording onto the fixed taxonomy.

    This value is consensus bound, so two panels that mean the same thing must
    produce the same token. The mapping runs on both sides, so a difference in
    phrasing never registers as a difference in judgment.
    """
    key = _fold(value)
    if not key or key in ("none", "no defect", "admissible", "n a", "na", "null"):
        return DEFECT_NONE
    if "self referential" in key or "circular" in key or "refers to itself" in key:
        return DEFECT_SELF_REFERENTIAL
    if "public" in key or "private" in key or "not verifiable" in key or "unverifiable" in key:
        return DEFECT_NOT_PUBLICLY_VERIFIABLE
    if "subjective" in key or "taste" in key or "opinion" in key or "best effort" in key:
        return DEFECT_SUBJECTIVE_ONLY
    if "unspecific" in key or "vague" in key or "specific" in key or "ambiguous" in key or "unclear" in key:
        return DEFECT_UNSPECIFIC
    return DEFECT_UNSPECIFIC


def _normalize_item_verdict(value: typing.Any) -> str:
    """
    Map a model's per item wording onto PRESENT, PARTIAL or ABSENT.

    Anything unrecognised becomes ABSENT. Failing closed is correct here: an
    unreadable verdict is not evidence that a deliverable was supplied.
    """
    key = _fold(value)
    if not key:
        return ITEM_ABSENT
    if key in ("present", "yes", "y", "satisfied", "met", "complete", "done", "true", "full"):
        return ITEM_PRESENT
    if key in ("partial", "partially", "incomplete", "some", "半"):
        return ITEM_PARTIAL
    if key in ("absent", "no", "n", "missing", "unmet", "none", "false", "not met"):
        return ITEM_ABSENT
    if "partial" in key or "incomplete" in key:
        return ITEM_PARTIAL
    if "present" in key or "satisf" in key or key.startswith("met"):
        return ITEM_PRESENT
    return ITEM_ABSENT


def _advisory(value: typing.Any) -> str:
    """
    Store a panel's prose.

    Bounded and collapsed. Recorded for human readers only: nothing in this
    contract ever reads it back, compares it, or feeds it to a later panel.
    """
    return _collapse(value)[:MAX_ADVISORY_TEXT]


def _parse_item_verdicts(raw: typing.Any, items: int) -> list:
    """
    Extract one verdict per checklist item, positionally.

    Accepts a bare list of verdict words and a list of objects carrying a verdict
    field, which are the two shapes models actually produce. Entries beyond the
    checklist length are dropped and entries missing from the end score ABSENT,
    so the vector is always exactly `items` long.
    """
    if isinstance(raw, dict):
        raw = _pick(raw, "items", "verdicts", "results", "checklist") or []
    if not isinstance(raw, list):
        raise gl.vm.UserError(f"{ERROR_LLM} item verdicts are not a list: {type(raw).__name__}")
    verdicts = []
    for entry in raw[: int(items)]:
        if isinstance(entry, dict):
            entry = _pick(entry, "verdict", "status", "result", "assessment", "value")
        verdicts.append(_normalize_item_verdict(entry))
    while len(verdicts) < int(items):
        verdicts.append(ITEM_ABSENT)
    return verdicts


def _parse_checklist(raw: typing.Any) -> list:
    """
    Extract an authored checklist from a model reply.

    Accepts a list of strings and a list of objects carrying a text field. Blank
    entries are dropped and duplicates are removed on the folded key, because two
    identically worded deliverables would otherwise double the weight of one
    requirement.
    """
    if isinstance(raw, dict):
        raw = _pick(raw, "deliverables", "checklist", "items", "requirements") or []
    if not isinstance(raw, list):
        raise gl.vm.UserError(f"{ERROR_LLM} checklist is not a list: {type(raw).__name__}")
    items: list = []
    seen: set = set()
    for entry in raw:
        if isinstance(entry, dict):
            entry = _pick(entry, "deliverable", "text", "item", "requirement", "description")
        cleaned = _clean_item(entry)
        if not cleaned:
            continue
        key = _fold(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(cleaned)
        if len(items) >= MAX_CHECKLIST_ITEMS:
            break
    return items


# ---------------------------------------------------------------------------
# Validator error handling
# ---------------------------------------------------------------------------

def _compare_errors(leader_message: str, validator_message: str) -> bool:
    """
    Decide whether two failures count as the same failure.

    Deterministic classes must match exactly, because both sides saw the same
    thing and any difference is real. Transient failures agree when both sides
    hit one, since a 503 on one fetch and a timeout on another are the same
    event as far as the pact is concerned. Model failures never agree, which is
    what forces rotation onto a fresh panel.
    """
    for prefix in (ERROR_EXPECTED, ERROR_EXTERNAL):
        if validator_message.startswith(prefix) or leader_message.startswith(prefix):
            return validator_message == leader_message
    if validator_message.startswith(ERROR_TRANSIENT) and leader_message.startswith(ERROR_TRANSIENT):
        return True
    return False


def _handle_leader_error(leaders_res: typing.Any, rerun: typing.Callable) -> bool:
    """
    The validator's response when the leader's block raised rather than returned.

    The validator reruns the work. If its own run succeeds where the leader's
    failed, the two genuinely disagree and it votes no. If its own run fails too,
    the failures are compared by class.
    """
    leader_message = str(getattr(leaders_res, "message", "") or "")
    try:
        rerun()
        return False
    except gl.vm.UserError as exc:
        validator_message = str(getattr(exc, "message", None) or exc)
        return _compare_errors(leader_message, validator_message)
    except Exception:
        return False


def _unwrap(result: typing.Any) -> dict:
    """
    Normalize what `run_nondet_unsafe` hands back into a plain dict.

    A consensus failure arrives here as a non-return result, and raising
    `[EXPECTED]` keeps it a clean revert with no state change rather than an
    unrecoverable VM error.
    """
    if isinstance(result, gl.vm.Return):
        payload = result.calldata
    elif hasattr(result, "calldata"):
        payload = result.calldata
    else:
        payload = result
    if isinstance(payload, dict):
        return payload
    raise gl.vm.UserError(f"{ERROR_EXPECTED} Validators did not agree on this reading")


# ---------------------------------------------------------------------------
# Evidence fetching
# ---------------------------------------------------------------------------

def _fetch_evidence(url: str) -> str:
    """
    Fetch an evidence document as text.

    `render` is tried first, because most real evidence is a page rather than a
    file and the rendered text of a commit view, a docs page or a block explorer
    is what a human would read. A raw GET is the fallback for plain files and for
    hosts the renderer cannot drive.

    Status codes are classified, not swallowed. A 4xx is deterministic, so both
    sides see the same one and the transaction reverts cleanly with the party's
    attempt intact, which is the right outcome for a URL that is simply wrong. A
    5xx is transient and both sides agreeing on that is correct.
    """
    text = ""
    try:
        rendered = gl.nondet.web.render(url, mode="text")
        text = str(rendered or "")
    except Exception:
        text = ""

    if len(_collapse(text)) < 40:
        response = gl.nondet.web.get(url)
        status = int(getattr(response, "status", 0) or 0)
        if 400 <= status < 500:
            raise gl.vm.UserError(f"{ERROR_EXTERNAL} evidence URL returned {status}")
        if status >= 500:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} evidence host returned {status}")
        if status and status not in range(200, 400):
            raise gl.vm.UserError(f"{ERROR_EXTERNAL} evidence URL returned {status}")
        body = getattr(response, "body", b"") or b""
        if isinstance(body, str):
            body = body.encode("utf-8")
        text = bytes(body).decode("utf-8", errors="replace")

    stripped = re.sub(r"<[^>]{1,400}>", " ", text)
    cleaned = _collapse(stripped)
    if len(cleaned) < 20:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} evidence URL served no readable content")
    return cleaned[:MAX_EVIDENCE_CHARS]


# ---------------------------------------------------------------------------
# Stored records
#
# Checklists are held as JSON text rather than as nested storage collections.
# A `DynArray` inside a stored dataclass works, but a checklist is always read
# and written whole, it is included verbatim in a consent digest, and it has to
# survive being handed into a prompt unchanged, so one canonical JSON string is
# both cheaper and harder to get subtly wrong.
# ---------------------------------------------------------------------------

@allow_storage
@dataclass
class Pact:
    """
    A bilateral pact: two commitments, two stakes, one settlement.

    `terms_digest` commits to every term that binds the parties, which is what
    party B passes back when it accepts. Any difference between what B reviewed
    and what is on chain changes the digest and the acceptance reverts, so B can
    never be bound to a commitment, a stake, a deadline or a rubric it did not
    see.

    `dependency` is fixed at creation and never changes. It is the difference
    between this contract and two escrows sitting next to each other.
    """
    pact_id: str
    title: str
    party_a: Address
    party_b: Address
    created_by: Address
    stake_a_wei: u256
    stake_b_wei: u256
    pass_threshold: u32
    dependency: str
    max_attempts: u32
    challenge_window: u64
    tier_tolerance_points: u32  # Deprecated compatibility term; never affects consensus.
    mutual_breach_sink: Address
    status: str
    created_at: u64
    funding_deadline: u64
    activated_at: u64
    settled_at: u64
    terms_digest: str
    rubric_authored: bool


@allow_storage
@dataclass
class Obligation:
    """
    One side of a pact.

    Only facts are stored. There is no status field, because a stored status is a
    second source of truth that some write path will eventually forget to update.
    `_derive_states` computes the live state from these facts and the clock every
    time it is asked.

    `activated_at` and `deadline_at` are zero while the side is waiting on its
    predecessor. They are written in the same transaction that confirms the
    predecessor's fulfilment, so the clock starts from a consensus bound fact
    rather than from a poke by an interested party.

    `best_credit` is the high water mark across attempts, not the latest. A party
    told what it was missing, which resubmits and happens to score worse, should
    not be worse off than if it had never tried. The best reading of the work it
    actually delivered is what its partial credit reflects.
    """
    pact_id: str
    side: str
    party: Address
    commitment: str
    commitment_digest: str
    stake_wei: u256
    window_seconds: u64
    depends_on: str
    checklist_json: str
    checklist_items: u32
    checklist_digest: str
    activated_at: u64
    deadline_at: u64
    attempts_used: u32
    best_tier: str
    best_credit: u32
    best_attempt: u32
    best_evidence_url: str
    confirmed_at: u64
    challenge_closes_at: u64
    challenge_used: bool
    challenge_substantiated: bool
    released: bool


@allow_storage
@dataclass
class Adjudication:
    """
    One panel reading of one side's evidence.

    `tier` and, for a challenge, `substantiated` are the
    consensus bound fields: every validator that accepted this transaction
    fetched the evidence itself, scored the same stored rubric, and reached the
    same tier.

    `finding` is rendered deterministically from the accepted result and on chain
    data, including the leader's advisory point total. It never drives settlement.

    `earned_points`, `panel_note`, `panel_missing`, `panel_score` and `item_verdicts_json` are the
    leader's own words and numbers, recorded for human readers. They are not
    bound, two validators may differ on all of them, and nothing in this contract
    reads them back.
    """
    pact_id: str
    side: str
    attempt: u32
    kind: str
    evidence_url: str
    url_digest: str
    tier: str
    credit: u32
    earned_points: u32
    total_points: u32
    passed: bool
    finding: str
    item_verdicts_json: str
    panel_score: u32
    panel_note: str
    panel_missing: str
    grounds: str
    substantiated: bool
    adjudicated_at: u64
    adjudicated_by: Address


@allow_storage
@dataclass
class Settlement:
    """
    The settled outcome of a pact and the arithmetic that produced it.

    Every number here is recomputable by anyone from the obligations and the
    pact, which is the point: a party that disputes its payout can check the
    formula rather than trust the record. `payout_a + payout_b + sink_wei` always
    equals `stake_a + stake_b`, and `resolve_pact` refuses to move anything if it
    does not.
    """
    pact_id: str
    outcome: str
    state_a: str
    state_b: str
    credit_a: u32
    credit_b: u32
    forfeit_a_wei: u256
    forfeit_b_wei: u256
    payout_a_wei: u256
    payout_b_wei: u256
    sink_wei: u256
    settled_at: u64
    resolved_by: Address
    summary: str



# ---------------------------------------------------------------------------
# The scoring prompt
#
# Module level rather than a method, because it is called from inside a
# cloudpickled nondeterministic closure. A method would drag the whole contract
# class into that closure; a plain function pickles as itself.
# ---------------------------------------------------------------------------

def _adjudication_prompt(
    side: str,
    commitment: str,
    checklist: list,
    evidence: str,
    url: str,
    grounds: str,
) -> str:
    """
    The scoring question put to the panel.

    The panel is never asked for a score or a tier. It is asked to mark each
    stored rubric item PRESENT, PARTIAL or ABSENT against the evidence it
    fetched, and the tier is arithmetic the contract does afterwards. That is
    the whole reason two panels agree: marking "is there a changelog in this
    page" is a question with one answer, and "how complete is this work out of
    a hundred" is not.

    The rubric handed in is the one stored on chain and consented to by both
    parties. Every validator scores the identical list, so the point totals
    are comparable in a way that two independently authored rubrics never
    would be.
    """
    numbered = "\n".join(f"  {index + 1}. {item}" for index, item in enumerate(checklist))

    challenge_block = ""
    if grounds:
        challenge_block = f"""
THIS IS A CHALLENGE. The counterparty says this evidence was accepted wrongly and
gives these grounds:

{grounds}

Decide separately whether the grounds are substantiated. Substantiated means the
grounds identify a real deficiency in the evidence against the rubric above, one
that your own item by item scoring confirms. Grounds that merely disagree with the
earlier reading, restate a preference, or raise something the rubric does not ask
for are NOT substantiated. Score the items on what you actually see, whether or
not you find the grounds substantiated.
"""

    return f"""You are one member of an independent panel deciding whether party {side}
of a two party pact performed what it promised. Score only against the rubric
below. The rubric was fixed and agreed by both parties before any work started,
so do not add requirements to it and do not excuse requirements in it.

PARTY {side} PROMISED:
{commitment}

THE AGREED RUBRIC, which is what you score:
{numbered}

EVIDENCE SUBMITTED, fetched from {url}:
{evidence}
{challenge_block}
Mark every rubric item with exactly one of:

  PRESENT   the evidence shows this deliverable, in full
  PARTIAL   the evidence shows a real but incomplete version of it
  ABSENT    the evidence does not show it, or shows something else

Decision rules, applied in this order:

  1. Judge only what is in the evidence above. If the evidence does not show a
 deliverable, it is ABSENT, however likely it is to exist elsewhere.
  2. A deliverable the evidence mentions but does not actually contain is ABSENT,
 not PARTIAL. A promise of a thing is not the thing.
  3. PARTIAL is for a deliverable that is genuinely there but short: three of the
 five examples promised, a document covering part of the stated scope.
  4. Do not mark an item PRESENT because the rest of the work is good, and do not
 mark it ABSENT because the rest is poor. Each item stands alone.
  5. Quality beyond what the rubric asks for earns nothing. The rubric is the
 whole question.

Return exactly {len(checklist)} verdicts, in the rubric's order.

Reply with JSON exactly in this shape and nothing else:

{{"items": [{{"n": 1, "verdict": "PRESENT", "why": "<short>"}}, ...],
 "score": <your own 0 to 100 reading of overall completeness>,
 "missing": "<what is still owed, one or two sentences, empty if nothing>",
 "note": "<one sentence summarising what the evidence shows>"{', "substantiated": true' if grounds else ''}}}"""


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

class BilateralCommitmentEnforcer(gl.Contract):
    """
    A registry of bilateral pacts, each holding both sides' stakes and settling
    on consensus adjudicated fulfilment.

    One deployment serves any number of unrelated pacts. Nothing is global except
    the stewardship of the defaults, and the steward cannot touch a pact that has
    already been created: every term that decides money is copied onto the pact at
    creation, so changing a default later cannot alter a live pact. That is
    deliberate, and it is what lets integrators treat a deployed registry as
    something they can rely on rather than something whose rules can move under
    them.
    """

    registry_name: str
    steward: Address
    created_at: u64

    # Registry defaults. A zero argument at creation means "take the default",
    # and the resolved value is then copied onto the pact and frozen there.
    default_pass_threshold: u32
    default_max_attempts: u32
    default_challenge_window: u64
    default_funding_window: u64
    default_tier_tolerance: u32

    pacts: TreeMap[str, Pact]
    pact_ids: DynArray[str]

    # Keyed f"{pact_id}\x1f{side}".
    obligations: TreeMap[str, Obligation]

    # Keyed f"{pact_id}\x1f{side}\x1f{attempt}".
    adjudications: TreeMap[str, Adjudication]

    # Replay protection, keyed f"{pact_id}\x1f{side}\x1f{url_digest}".
    evidence_seen: TreeMap[str, bool]

    settlements: TreeMap[str, Settlement]

    # Unanimous teardown consent, keyed f"{pact_id}\x1f{side}".
    release_consent: TreeMap[str, bool]

    # Per party index. Two flat maps rather than a map of arrays, so a lookup is
    # O(log n) and no entry has to be rewritten whole to append to it.
    party_pact_count: TreeMap[str, u256]
    party_pact_at: TreeMap[str, str]

    total_locked_wei: u256
    total_settled_wei: u256
    pacts_settled: u256

    def __init__(self, registry_name: str):
        self.registry_name = _collapse(registry_name)[:MAX_TITLE] or "Bilateral Commitment Registry"
        self.steward = gl.message.sender_address
        self.created_at = u64(self._now())
        self.default_pass_threshold = u32(80)
        self.default_max_attempts = u32(3)
        self.default_challenge_window = u64(48 * 3600)
        self.default_funding_window = u64(7 * 24 * 3600)
        self.default_tier_tolerance = u32(0)
        self.total_locked_wei = u256(0)
        self.total_settled_wei = u256(0)
        self.pacts_settled = u256(0)

    # -- clock -------------------------------------------------------------

    def _now(self) -> int:
        """
        The transaction time, as a Unix second.

        GenVM pins `datetime.now()` to the transaction datetime so that a leader
        and every validator read the same instant, which is what lets a deadline
        be a deterministic fact rather than a race. Two fallbacks exist because a
        deadline that silently reads zero would make every obligation look
        breached: the message's own datetime first, then the pact's creation time
        as a fail closed floor.
        """
        try:
            return int(datetime.now(timezone.utc).timestamp())
        except Exception:
            pass
        try:
            stamp = str(gl.message_raw.get("datetime", ""))
            text = stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp
            return int(datetime.fromisoformat(text).timestamp())
        except Exception:
            return int(self.created_at)

    # -- guards ------------------------------------------------------------

    def _require(self, condition: bool, message: str) -> None:
        """Revert with an `[EXPECTED]` business rule failure when the condition fails."""
        if not condition:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} {message}")

    def _require_steward(self) -> None:
        self._require(gl.message.sender_address == self.steward, "Only the steward may do this")

    def _pact(self, pact_id: str) -> Pact:
        key = _collapse(pact_id)
        self._require(key in self.pacts, f"No pact with id '{key}'")
        return self.pacts[key]

    def _obligation(self, pact_id: str, side: str) -> Obligation:
        key = f"{_collapse(pact_id)}\x1f{side}"
        self._require(key in self.obligations, f"No obligation for side {side}")
        return self.obligations[key]

    def _ob_key(self, pact_id: str, side: str) -> str:
        return f"{_collapse(pact_id)}\x1f{side}"

    def _other_side(self, side: str) -> str:
        return SIDE_B if side == SIDE_A else SIDE_A

    def _side_of(self, pact: Pact, caller: Address) -> str:
        """Which side of the pact the caller is, reverting if it is neither."""
        if caller == pact.party_a:
            return SIDE_A
        if caller == pact.party_b:
            return SIDE_B
        self._require(False, "Caller is not a party to this pact")
        return ""

    def _index_party(self, party: Address, pact_id: str) -> None:
        """Append a pact to a party's index."""
        key = str(party)
        count = int(self.party_pact_count.get(key, u256(0)))
        self.party_pact_at[f"{key}\x1f{count}"] = _collapse(pact_id)
        self.party_pact_count[key] = u256(count + 1)

    # -- state derivation --------------------------------------------------
    #
    # The single source of truth for what each side of a pact currently is. Every
    # view, every guard and the settlement all route through here, so there is no
    # way for a stored status and a derived status to drift apart.

    def _state_of(self, ob: Obligation, pact: Pact, predecessor_state: str, now: int) -> str:
        """
        Derive one side's live state.

        `predecessor_state` is the already derived state of the side this one
        depends on, or the empty string when it depends on nothing. Passing it in
        rather than looking it up is what keeps this function non recursive: a
        dependency only ever points one way, so the independent side is always
        computable first.
        """
        if int(ob.confirmed_at) > 0:
            return STATE_FULFILLED

        if ob.depends_on:
            # The predecessor decides whether this obligation ever becomes live.
            if predecessor_state in (STATE_BREACHED, STATE_EXCUSED):
                return STATE_EXCUSED
            if predecessor_state != STATE_FULFILLED:
                return STATE_WAITING
            # The predecessor is fulfilled, so activation was written. If it was
            # not, the clock has not started and this side still waits.
            if int(ob.activated_at) == 0:
                return STATE_WAITING

        if int(ob.deadline_at) == 0:
            return STATE_WAITING
        if now > int(ob.deadline_at):
            return STATE_BREACHED
        if int(ob.attempts_used) >= int(pact.max_attempts):
            # Out of attempts with the threshold uncleared. There is no route left
            # to fulfilment, so this is terminal now rather than at the deadline.
            # Settling early is strictly better for both parties than waiting out
            # a clock whose outcome is already fixed.
            return STATE_BREACHED
        return STATE_OPEN

    def _derive_states(self, pact: Pact, now: int) -> tuple:
        """
        Both sides' live states, independent side first.

        Returns `(state_a, state_b)`.
        """
        ob_a = self.obligations[self._ob_key(pact.pact_id, SIDE_A)]
        ob_b = self.obligations[self._ob_key(pact.pact_id, SIDE_B)]

        if pact.dependency == DEP_B_AFTER_A:
            state_a = self._state_of(ob_a, pact, "", now)
            state_b = self._state_of(ob_b, pact, state_a, now)
        elif pact.dependency == DEP_A_AFTER_B:
            state_b = self._state_of(ob_b, pact, "", now)
            state_a = self._state_of(ob_a, pact, state_b, now)
        else:
            state_a = self._state_of(ob_a, pact, "", now)
            state_b = self._state_of(ob_b, pact, "", now)
        return (state_a, state_b)

    def _challenge_open(self, ob: Obligation, state: str, now: int) -> bool:
        """
        Whether a confirmed fulfilment can still be challenged.

        Only a fulfilment is challengeable, only once, and only inside the window
        the pact declared. A pact with a zero challenge window settles the instant
        both sides are terminal, which is the right default for parties that do
        not want a dispute period.
        """
        if state != STATE_FULFILLED:
            return False
        if bool(ob.challenge_used):
            return False
        # A pact that declared no challenge window closes `challenge_closes_at` on
        # the confirmation instant itself, so the window is empty rather than
        # momentarily open. Comparing against the confirmation rather than against
        # zero also closes the window a spent challenge leaves behind.
        if int(ob.challenge_closes_at) <= int(ob.confirmed_at):
            return False
        return now <= int(ob.challenge_closes_at)

    def _resolvable(self, pact: Pact, now: int) -> tuple:
        """
        Whether the pact can be settled now, and why not if it cannot.

        Returns `(resolvable, reason, state_a, state_b)`.
        """
        if pact.status == PACT_SETTLED:
            return (False, "Pact is already settled", "", "")
        if pact.status == PACT_WITHDRAWN:
            return (False, "Pact was withdrawn before it was funded", "", "")
        if pact.status != PACT_ACTIVE:
            return (False, "Pact is not active", "", "")

        state_a, state_b = self._derive_states(pact, now)
        ob_a = self.obligations[self._ob_key(pact.pact_id, SIDE_A)]
        ob_b = self.obligations[self._ob_key(pact.pact_id, SIDE_B)]

        if state_a not in TERMINAL_STATES:
            return (False, f"Party A is still {state_a}", state_a, state_b)
        if state_b not in TERMINAL_STATES:
            return (False, f"Party B is still {state_b}", state_a, state_b)

        # A fulfilment that can still be overturned is not a settled fact, so
        # paying out against it would make the challenge path meaningless. Both
        # parties can waive this together through `agree_release`.
        if self._challenge_open(ob_a, state_a, now):
            return (False, "Party A's fulfilment is still open to challenge", state_a, state_b)
        if self._challenge_open(ob_b, state_b, now):
            return (False, "Party B's fulfilment is still open to challenge", state_a, state_b)

        return (True, "", state_a, state_b)

    # -- settlement arithmetic ---------------------------------------------

    def _effective_credit(self, state: str, credit: int) -> int:
        """
        The credit a side is settled on.

        A side that cleared the threshold forfeits nothing, so its effective
        credit is full regardless of the tier that cleared it: passing at
        SUBSTANTIAL is passing, and docking it twenty percent would punish
        performance the pact itself declared acceptable. A side excused because
        its predecessor never delivered is likewise whole, because it was never
        given the chance to perform. Everything else settles on what it actually
        scored.
        """
        if state in (STATE_FULFILLED, STATE_EXCUSED):
            return 100
        return max(0, min(100, int(credit)))

    def _compute_settlement(
        self,
        pact: Pact,
        state_a: str,
        state_b: str,
        credit_a: int,
        credit_b: int,
        released: bool,
    ) -> dict:
        """
        The whole settlement, as arithmetic.

        One formula, four outcomes:

            effective = 100 if FULFILLED or EXCUSED else the confirmed credit
            forfeit   = stake * (100 - effective) / 100
            payout    = stake - own forfeit + the other side's forfeit

        Integer division floors each forfeit, so the remainder stays with the side
        that staked it rather than vanishing. The conservation check at the end is
        not decoration: it is the invariant that makes this contract safe to hold
        both stakes, and `resolve_pact` refuses to transfer anything if it fails.
        """
        stake_a = int(pact.stake_a_wei)
        stake_b = int(pact.stake_b_wei)

        if released:
            # Unanimous teardown. Both sides whole, by consent of everyone with a
            # claim, which is the only party whose agreement is needed.
            effective_a, effective_b = 100, 100
        else:
            effective_a = self._effective_credit(state_a, credit_a)
            effective_b = self._effective_credit(state_b, credit_b)

        forfeit_a = stake_a * (100 - effective_a) // 100
        forfeit_b = stake_b * (100 - effective_b) // 100

        breach_a = forfeit_a > 0
        breach_b = forfeit_b > 0

        sink = 0
        sink_address = str(pact.mutual_breach_sink)
        if breach_a and breach_b and sink_address != ZERO_ADDRESS:
            # Both at fault and a sink was declared, so the forfeits leave the
            # pact instead of crossing and netting back to the parties. This is
            # the only deterrent a two party instrument has against both sides
            # quietly agreeing not to perform.
            sink = forfeit_a + forfeit_b
            payout_a = stake_a - forfeit_a
            payout_b = stake_b - forfeit_b
        else:
            payout_a = stake_a - forfeit_a + forfeit_b
            payout_b = stake_b - forfeit_b + forfeit_a

        if released:
            outcome = OUTCOME_RELEASED
        elif not breach_a and not breach_b:
            outcome = OUTCOME_MUTUAL_FULFILMENT
        elif breach_a and breach_b:
            outcome = OUTCOME_MUTUAL_BREACH
        elif breach_a:
            outcome = OUTCOME_A_BREACH
        else:
            outcome = OUTCOME_B_BREACH

        total_in = stake_a + stake_b
        total_out = payout_a + payout_b + sink
        self._require(
            total_out == total_in,
            f"Settlement does not conserve value: {total_out} out of {total_in} in",
        )

        return {
            "outcome": outcome,
            "effective_a": effective_a,
            "effective_b": effective_b,
            "forfeit_a": forfeit_a,
            "forfeit_b": forfeit_b,
            "payout_a": payout_a,
            "payout_b": payout_b,
            "sink": sink,
        }

    def _render_summary(self, pact: Pact, calc: dict, state_a: str, state_b: str) -> str:
        """
        A one line, deterministic account of how the pact settled.

        Built from the settled arithmetic and the derived states only, so every
        validator renders identical bytes and the record reads the same to
        everyone who looks at it later.
        """
        outcome = calc["outcome"]
        if outcome == OUTCOME_RELEASED:
            return (
                f"Both parties released pact '{pact.pact_id}' by agreement. "
                f"Each stake returned in full."
            )
        if outcome == OUTCOME_MUTUAL_FULFILMENT:
            return (
                f"Both parties fulfilled pact '{pact.pact_id}'. "
                f"Each stake returned in full."
            )
        if outcome == OUTCOME_MUTUAL_BREACH:
            destination = "to the declared sink" if calc["sink"] > 0 else "across to the other party"
            return (
                f"Both parties breached pact '{pact.pact_id}'. Party A settled at "
                f"{calc['effective_a']} credit in state {state_a} and party B at "
                f"{calc['effective_b']} credit in state {state_b}. Both shortfalls "
                f"paid {destination}."
            )
        breaching, performing = (SIDE_A, SIDE_B) if outcome == OUTCOME_A_BREACH else (SIDE_B, SIDE_A)
        short_state = state_a if breaching == SIDE_A else state_b
        short_credit = calc["effective_a"] if breaching == SIDE_A else calc["effective_b"]
        forfeit = calc["forfeit_a"] if breaching == SIDE_A else calc["forfeit_b"]
        return (
            f"Party {breaching} breached pact '{pact.pact_id}' in state {short_state} "
            f"at {short_credit} credit, forfeiting {forfeit} wei of its stake to "
            f"party {performing}."
        )

    # -- nondeterministic operation 1: admissibility and rubric ------------

    def _adjudicability_prompt(self, commitment_a: str, commitment_b: str, authored: bool) -> str:
        """
        The admissibility question put to the panel.

        Two things are asked. Whether each commitment can be adjudicated against
        public evidence at all, and, when the creator did not supply its own
        rubric, what the commitment's discrete deliverables are.

        The instructions are written as decision rules rather than as a request
        for an opinion, because what has to agree across validators is a boolean
        and a count, and panels agree on those only when the question has one
        reading. The defect taxonomy is enumerated in full and the panel is told
        to pick from it, since a free text reason could never be compared.
        """
        rubric_clause = ""
        if authored:
            rubric_clause = f"""
For each commitment that is ADJUDICABLE, break it into its discrete
deliverables. A deliverable is one thing a reader could look for in the evidence
and say present or absent. Rules for the list:

  - Between {MIN_CHECKLIST_ITEMS} and {MAX_CHECKLIST_ITEMS} entries.
  - Split on substance, never on grammar. "Publish a guide with runnable
    examples" is two deliverables, the guide and the examples. "Publish a clear,
    well organised guide" is one, because clear and well organised are not
    separately checkable things.
  - Each entry states what must exist, not how well it must be done.
  - Do not invent requirements the commitment does not state.
  - Do not include the deadline as a deliverable. Timing is enforced on chain.
"""

        return f"""You are one member of an independent panel reviewing a two party
commercial pact before either party stakes any funds. You are not judging whether
anyone performed. Nothing has been delivered yet. You are judging only whether
each promise is the kind of promise that could later be checked against public
evidence.

PARTY A'S COMMITMENT:
{commitment_a}

PARTY B'S COMMITMENT:
{commitment_b}

For each commitment, decide ADJUDICABLE or INADMISSIBLE.

ADJUDICABLE means all three of these hold:
  1. It names at least one concrete thing that must exist or happen.
  2. A person given a public URL could decide whether that thing is there.
  3. Deciding it does not require private information, a party's internal
     records, or a judgment of taste.

INADMISSIBLE otherwise. When a commitment is INADMISSIBLE, choose exactly one
defect from this closed list:

  unspecific               names no concrete deliverable, or is so vague that two
                           honest readers would look for different things
  not_publicly_verifiable  the deliverable is real and concrete, but checking it
                           needs private data, internal systems or credentials
  subjective_only          turns entirely on taste, effort or satisfaction, with
                           nothing a reader could point at
  self_referential         defines performance by reference to the pact, the
                           other party's approval, or itself

When a commitment is ADJUDICABLE its defect is exactly: none

Treat these as INADMISSIBLE: "use best efforts", "act in good faith",
"deliver to my satisfaction", "be reasonable about timing", "work hard on it".
Treat these as ADJUDICABLE: "publish X at a public URL", "deploy contract Y to
network Z", "merge pull request N", "release version V with changelog".
{rubric_clause}
Reply with JSON exactly in this shape and nothing else:

{{"a": {{"adjudicable": true, "defect": "none", "reason": "<one sentence>",
        "deliverables": ["<deliverable>", "..."]}},
 "b": {{"adjudicable": true, "defect": "none", "reason": "<one sentence>",
        "deliverables": ["<deliverable>", "..."]}}}}

Use an empty deliverables list for a commitment you marked INADMISSIBLE."""

    def _assess_commitments(
        self,
        commitment_a: str,
        commitment_b: str,
        supplied_a: list,
        supplied_b: list,
    ) -> dict:
        """
        Run the admissibility and rubric operation under consensus.

        What agreement requires, per side: the `adjudicable` boolean, the
        normalized `defect` class, and the number of checklist items. Numbers and
        fixed enum values, all three.

        What agreement deliberately does not require: the checklist text. Two
        honest panels enumerating the deliverables of one promise write "ship the
        dataset" and "provide the data file". Those are one requirement in two
        wordings, and no majority ever forms on prose. The rubric becomes binding
        through consent instead: it is stored while the pact is still PROPOSED, it
        is covered by the `terms_digest` that party B must pass to accept, and
        party A can withdraw if it dislikes what the panel wrote. Neither side has
        anything at risk until both have consented to the exact text.

        When the creator supplied its own checklists the panel authors nothing and
        rules on admissibility only, which removes the model from the rubric
        entirely for integrators who want that.
        """
        authored = not supplied_a or not supplied_b
        prompt = self._adjudicability_prompt(commitment_a, commitment_b, authored)
        # Plain values only. These closures are cloudpickled exactly as they are
        # on chain, so capturing anything that reaches storage would fail here the
        # same way it fails on a validator.
        fixed_a = [_clean_item(item) for item in supplied_a]
        fixed_b = [_clean_item(item) for item in supplied_b]

        def leader_fn() -> dict:
            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            reply = _as_dict(raw)

            out: dict = {}
            for side, key, supplied in ((SIDE_A, "a", fixed_a), (SIDE_B, "b", fixed_b)):
                block = reply.get(key)
                if not isinstance(block, dict):
                    raise gl.vm.UserError(f"{ERROR_LLM} panel gave no verdict for side {side}")

                adjudicable = _as_bool(
                    _pick(block, "adjudicable", "admissible", "verifiable", "ok"),
                    f"{key}.adjudicable",
                )
                defect = _normalize_defect(_pick(block, "defect", "defect_class", "reason_class"))

                # A panel that says admissible while naming a defect, or
                # inadmissible while naming none, has contradicted itself. The
                # defect is the bound field, so the boolean is reconciled to it
                # rather than the other way round.
                if adjudicable and defect != DEFECT_NONE:
                    adjudicable = False
                if not adjudicable and defect == DEFECT_NONE:
                    defect = DEFECT_UNSPECIFIC

                items = list(supplied)
                if adjudicable and not items:
                    items = _parse_checklist(_pick(block, "deliverables", "checklist", "items"))
                    if not items:
                        raise gl.vm.UserError(
                            f"{ERROR_LLM} side {side} was called adjudicable with no deliverables"
                        )
                if not adjudicable:
                    items = []

                out[side] = {
                    "adjudicable": adjudicable,
                    "defect": defect,
                    "reason": _advisory(_pick(block, "reason", "rationale", "explanation")),
                    "items": items[:MAX_CHECKLIST_ITEMS],
                }
            return out

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return _handle_leader_error(leaders_res, leader_fn)

            leader = leaders_res.calldata
            if not isinstance(leader, dict):
                return False
            mine = leader_fn()

            for side in (SIDE_A, SIDE_B):
                theirs = leader.get(side)
                ours = mine.get(side)
                if not isinstance(theirs, dict) or not isinstance(ours, dict):
                    return False
                # The admissibility verdict gates whether the pact may exist at
                # all, so it must be agreed outright.
                if bool(theirs.get("adjudicable")) != bool(ours.get("adjudicable")):
                    return False
                # The defect is the ground of a refusal and is recorded as such,
                # so two panels refusing for unrelated reasons are not agreeing.
                if str(theirs.get("defect", "")) != str(ours.get("defect", "")):
                    return False
                # Item count, not item text. A count that differs means the two
                # panels read the promise as asking for a different number of
                # things, which is a real disagreement about what was promised.
                if len(theirs.get("items") or []) != len(ours.get("items") or []):
                    return False
            return True

        return _unwrap(gl.vm.run_nondet_unsafe(leader_fn, validator_fn))

    # -- nondeterministic operation 2: evidence adjudication ---------------

    def _adjudicate(
        self,
        side: str,
        commitment: str,
        checklist: list,
        url: str,
        threshold: int,
        grounds: str = "",
    ) -> dict:
        """
        Run an adjudication under consensus.

        The leader fetches the evidence and scores the stored rubric. The validator
        reruns the whole function: its own fetch, its own reading, its own item
        verdicts, its own point total, its own tier. It never inspects the shape of
        the leader's reply and it never asks a model whether the leader looked
        reasonable, because neither of those verifies the answer that moves money.

        What agreement requires:

          - the pass or fail side of the threshold, always, strictly. This is the
            binary verdict, and it decides whether a side keeps its whole stake.
          - the exact credit tier, always. Adjacent failing tiers forfeit different
            stake amounts, so even one point of cross-tier disagreement must fail.
            No pact option or steward default can relax this requirement.
          - for a challenge, the `substantiated` boolean.

        What agreement never requires: the point total within the agreed tier,
        the per item verdict vector, the panel's own 0 to 100 reading, or any prose.
        These are recorded as the leader's advisory reading and never drive money.
        """
        item_count = len(checklist)
        self._require(item_count > 0, "This side has no rubric to score")
        prompt_items = list(checklist)
        clean_url = url
        clean_grounds = grounds
        side_label = side
        commitment_text = commitment
        pass_mark = int(threshold)
        is_challenge = bool(grounds)

        def leader_fn() -> dict:
            evidence = _fetch_evidence(clean_url)
            prompt = _adjudication_prompt(
                side_label, commitment_text, prompt_items, evidence, clean_url, clean_grounds
            )
            reply = _as_dict(gl.nondet.exec_prompt(prompt, response_format="json"))

            verdicts = _parse_item_verdicts(
                _pick(reply, "items", "verdicts", "results") or [], item_count
            )
            earned = _score_items(verdicts, item_count)
            tier = _tier_for_points(earned, item_count)
            credit = _credit_for_tier(tier)

            panel_score = _as_int(_pick(reply, "score", "completeness", "rating") or 0, "score", 0, 100)
            # A panel whose own two outputs contradict each other has not produced
            # a usable reading, so this fails loudly on the side that produced it
            # rather than becoming a cross side comparison problem. The band is
            # deliberately generous: it catches "every item absent, score 95", not
            # ordinary looseness.
            if abs(panel_score - credit) > 45:
                raise gl.vm.UserError(
                    f"{ERROR_LLM} panel scored {panel_score} overall while marking items at {credit}"
                )

            substantiated = False
            if is_challenge:
                substantiated = _as_bool(
                    _pick(reply, "substantiated", "upheld", "valid", "grounds_substantiated"),
                    "substantiated",
                )

            return {
                "tier": tier,
                "credit": credit,
                "earned": earned,
                "items": item_count,
                "passed": credit >= pass_mark,
                "verdicts": verdicts,
                "panel_score": panel_score,
                "note": _advisory(_pick(reply, "note", "summary", "assessment")),
                "missing": _advisory(_pick(reply, "missing", "outstanding", "gaps")),
                "substantiated": substantiated,
            }

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return _handle_leader_error(leaders_res, leader_fn)

            leader = leaders_res.calldata
            if not isinstance(leader, dict):
                return False
            mine = leader_fn()

            leader_tier = str(leader.get("tier", ""))
            our_tier = str(mine["tier"])
            # Independently derived exact tier equality binds every payout-driving
            # credit. Point proximity and a shared failure verdict are insufficient.
            if leader_tier not in TIER_CREDIT or leader_tier != our_tier:
                return False

            # The binary verdict is the part that decides whether a side keeps its
            # whole stake, so it is never given any slack.
            if bool(leader.get("passed")) != bool(mine["passed"]):
                return False

            if is_challenge and bool(leader.get("substantiated")) != bool(mine["substantiated"]):
                return False

            return True

        return _unwrap(gl.vm.run_nondet_unsafe(leader_fn, validator_fn))

    # -- helpers for creation ----------------------------------------------

    def _parse_wei(self, raw: typing.Any, field: str) -> int:
        """
        Parse a wei amount given as a decimal string.

        Amounts cross the ABI as strings rather than as integers because a stake in
        wei routinely exceeds what JSON and JavaScript tooling carry losslessly,
        and a stake that is silently rounded is a stake that breaks conservation.
        """
        text = _collapse(raw).replace("_", "")
        self._require(bool(re.fullmatch(r"[0-9]+", text)), f"{field} must be a decimal wei string")
        return int(text)

    def _resolve_option(self, options: dict, key: str, default: int, low: int, high: int) -> int:
        """
        One optional numeric term, falling back to the registry default.

        A term absent from the options bag takes the registry default as it stands
        right now, and the resolved value is then frozen onto the pact. A steward
        that changes a default later cannot move a pact that already exists.
        """
        if key not in options or options[key] is None:
            value = int(default)
        else:
            try:
                value = int(options[key])
            except Exception:
                self._require(False, f"Option '{key}' is not an integer")
                value = int(default)
        self._require(low <= value <= high, f"Option '{key}' must be between {low} and {high}")
        return value

    def _parse_options(self, options_json: str) -> dict:
        """Parse the optional terms bag, accepting an empty string as no options."""
        text = _collapse(options_json)
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            self._require(False, "options_json is not valid JSON")
            return {}
        self._require(isinstance(parsed, dict), "options_json must be a JSON object")
        return parsed

    def _supplied_checklist(self, options: dict, key: str) -> list:
        """
        A creator supplied rubric, validated.

        Supplying one removes the model from rubric authoring entirely, which some
        integrators will want. The bounds are the same ones a panel authored list
        has to satisfy, so a supplied rubric cannot be used to smuggle in a
        hundred items and make every later adjudication exceed the compute limit.
        """
        raw = options.get(key)
        if raw is None:
            return []
        self._require(isinstance(raw, list), f"Option '{key}' must be a list of strings")
        items: list = []
        seen: set = set()
        for entry in raw:
            cleaned = _clean_item(entry)
            self._require(bool(cleaned), f"Option '{key}' contains an empty entry")
            folded = _fold(cleaned)
            self._require(folded not in seen, f"Option '{key}' repeats '{cleaned}'")
            seen.add(folded)
            items.append(cleaned)
        self._require(
            len(items) <= MAX_CHECKLIST_ITEMS,
            f"Option '{key}' has more than {MAX_CHECKLIST_ITEMS} entries",
        )
        return items

    def _compute_terms_digest(self, pact: Pact, ob_a: Obligation, ob_b: Obligation) -> str:
        """
        The commitment of record for everything that binds the two parties.

        Party B passes this back when it accepts, so it covers every term whose
        change would mean B is agreeing to something else: both parties, both
        commitments, both stakes, both deadline windows, the dependency direction,
        the pass threshold, the attempt allowance, the tolerance, the challenge
        window, the sink, and crucially both rubrics. A rubric B never saw cannot
        bind B, and including the checklist digests here is what makes that true
        rather than merely stated.
        """
        return _digest(
            "pact-terms-v1",
            _fold(pact.pact_id),
            str(pact.party_a),
            str(pact.party_b),
            str(int(pact.stake_a_wei)),
            str(int(pact.stake_b_wei)),
            str(int(pact.pass_threshold)),
            pact.dependency,
            str(int(pact.max_attempts)),
            str(int(pact.challenge_window)),
            str(int(pact.tier_tolerance_points)),
            str(pact.mutual_breach_sink),
            ob_a.commitment_digest,
            ob_b.commitment_digest,
            str(int(ob_a.window_seconds)),
            str(int(ob_b.window_seconds)),
            ob_a.checklist_digest,
            ob_b.checklist_digest,
        )

    # -- create ------------------------------------------------------------

    @gl.public.write.payable
    def create_pact(
        self,
        pact_id: str,
        title: str,
        party_b: str,
        commitment_a: str,
        commitment_b: str,
        stake_b_wei: str,
        dependency: str,
        window_a_seconds: int,
        window_b_seconds: int,
        options_json: str,
    ) -> str:
        """
        Propose a pact and lock party A's stake.

        The caller is party A, and the value sent with the call is party A's stake.
        Party B's stake is declared here but is not locked until B accepts, so a
        pact sits PROPOSED until both sides have put value in. That is what makes
        the commitments consensual rather than imposed: B's acceptance is its
        assent to both texts, both rubrics and every term.

        `dependency` is one of:

            none         both clocks start when the pact activates
            b_after_a    party B's clock starts when party A's fulfilment is
                         confirmed by consensus
            a_after_b    the mirror

        `options_json` carries the optional terms. Any key left out takes the
        registry default as it stands now and is then frozen onto the pact:

            pass_threshold          1 to 100, the credit a side must reach to pass
            max_attempts            1 to 10, evidence submissions per side
            challenge_window         seconds a counterparty has to challenge a
                                    confirmed fulfilment, 0 to disable
            funding_window          seconds party B has to accept
            tier_tolerance_points   deprecated compatibility term (0 to 4);
                                    exact tier agreement is always required
            mutual_breach_sink      address that receives both forfeits when both
                                    sides breach, omitted to let them cross
            checklist_a             explicit rubric for party A, omit to have the
                                    panel author it
            checklist_b             the same for party B

        Returns the admissibility verdict, the stored rubrics and the
        `terms_digest` party B will need.
        """
        key = _collapse(pact_id)
        self._require(bool(key), "pact_id is required")
        self._require(len(key) <= MAX_PACT_ID, f"pact_id exceeds {MAX_PACT_ID} characters")
        self._require(key not in self.pacts, f"Pact '{key}' already exists")

        clean_title = _collapse(title)[:MAX_TITLE]
        self._require(bool(clean_title), "title is required")

        text_a = _collapse(commitment_a)
        text_b = _collapse(commitment_b)
        for label, text in (("commitment_a", text_a), ("commitment_b", text_b)):
            self._require(bool(text), f"{label} is required")
            self._require(len(text) <= MAX_COMMITMENT, f"{label} exceeds {MAX_COMMITMENT} characters")
        self._require(len(text_a) >= 20, "commitment_a is too short to adjudicate")
        self._require(len(text_b) >= 20, "commitment_b is too short to adjudicate")

        address_a = gl.message.sender_address
        try:
            address_b = Address(_collapse(party_b))
        except Exception:
            self._require(False, f"party_b is not a valid address: {party_b}")
            raise
        self._require(address_b != address_a, "A pact needs two different parties")
        self._require(str(address_b) != ZERO_ADDRESS, "party_b cannot be the zero address")

        self._require(dependency in DEPENDENCY_MODES, f"dependency must be one of {DEPENDENCY_MODES}")

        for label, window in (("window_a_seconds", window_a_seconds), ("window_b_seconds", window_b_seconds)):
            self._require(
                MIN_WINDOW_SECONDS <= int(window) <= MAX_WINDOW_SECONDS,
                f"{label} must be between {MIN_WINDOW_SECONDS} and {MAX_WINDOW_SECONDS} seconds",
            )

        stake_a = int(gl.message.value)
        stake_b = self._parse_wei(stake_b_wei, "stake_b_wei")
        # A commitment enforcer with nothing at stake enforces nothing, and a zero
        # stake would also make every forfeit zero and every outcome identical.
        self._require(stake_a > 0, "Party A must send its stake with this call")
        self._require(stake_b > 0, "stake_b_wei must be greater than zero")

        options = self._parse_options(options_json)
        threshold = self._resolve_option(options, "pass_threshold", int(self.default_pass_threshold), 1, 100)
        attempts = self._resolve_option(options, "max_attempts", int(self.default_max_attempts), 1, MAX_ATTEMPTS_CAP)
        challenge_window = self._resolve_option(
            options, "challenge_window", int(self.default_challenge_window), 0, MAX_CHALLENGE_WINDOW
        )
        funding_window = self._resolve_option(
            options, "funding_window", int(self.default_funding_window), MIN_WINDOW_SECONDS, MAX_FUNDING_WINDOW
        )
        tolerance = self._resolve_option(
            options, "tier_tolerance_points", int(self.default_tier_tolerance), 0, 4
        )

        sink_raw = _collapse(options.get("mutual_breach_sink") or ZERO_ADDRESS)
        try:
            sink = Address(sink_raw)
        except Exception:
            self._require(False, f"mutual_breach_sink is not a valid address: {sink_raw}")
            raise
        self._require(
            sink != address_a and sink != address_b,
            "mutual_breach_sink cannot be one of the parties, which would defeat its purpose",
        )

        supplied_a = self._supplied_checklist(options, "checklist_a")
        supplied_b = self._supplied_checklist(options, "checklist_b")

        # Consensus: is each promise adjudicable, and what are its deliverables.
        verdict = self._assess_commitments(text_a, text_b, supplied_a, supplied_b)
        side_a = verdict.get(SIDE_A) or {}
        side_b = verdict.get(SIDE_B) or {}

        # Refusal happens here, in deterministic code, on the consensus bound
        # verdict. Raising inside the nondeterministic block instead would make the
        # revert message depend on prose, and two panels would then disagree on a
        # failure they actually agreed about.
        for label, block in (("Party A's commitment", side_a), ("Party B's commitment", side_b)):
            if not bool(block.get("adjudicable")):
                self._require(
                    False,
                    f"{label} cannot be adjudicated against public evidence "
                    f"(defect: {block.get('defect', DEFECT_UNSPECIFIC)}). Rewrite it to name a "
                    f"concrete deliverable that a reader could check at a public URL.",
                )

        items_a = [_clean_item(item) for item in (side_a.get("items") or [])]
        items_b = [_clean_item(item) for item in (side_b.get("items") or [])]
        self._require(len(items_a) >= MIN_CHECKLIST_ITEMS, "Party A's commitment produced no rubric")
        self._require(len(items_b) >= MIN_CHECKLIST_ITEMS, "Party B's commitment produced no rubric")

        now = self._now()
        pact = Pact(
            pact_id=key,
            title=clean_title,
            party_a=address_a,
            party_b=address_b,
            created_by=address_a,
            stake_a_wei=u256(stake_a),
            stake_b_wei=u256(stake_b),
            pass_threshold=u32(threshold),
            dependency=dependency,
            max_attempts=u32(attempts),
            challenge_window=u64(challenge_window),
            tier_tolerance_points=u32(tolerance),
            mutual_breach_sink=sink,
            status=PACT_PROPOSED,
            created_at=u64(now),
            funding_deadline=u64(now + funding_window),
            activated_at=u64(0),
            settled_at=u64(0),
            terms_digest="",
            rubric_authored=bool(not supplied_a or not supplied_b),
        )

        ob_a = self._new_obligation(key, SIDE_A, address_a, text_a, stake_a, int(window_a_seconds), items_a, dependency)
        ob_b = self._new_obligation(key, SIDE_B, address_b, text_b, stake_b, int(window_b_seconds), items_b, dependency)

        pact.terms_digest = self._compute_terms_digest(pact, ob_a, ob_b)

        self.pacts[key] = pact
        self.obligations[self._ob_key(key, SIDE_A)] = ob_a
        self.obligations[self._ob_key(key, SIDE_B)] = ob_b
        self.pact_ids.append(key)
        self._index_party(address_a, key)
        self._index_party(address_b, key)
        self.total_locked_wei = u256(int(self.total_locked_wei) + stake_a)

        return json.dumps(
            {
                "pact_id": key,
                "status": PACT_PROPOSED,
                "terms_digest": pact.terms_digest,
                "party_a": str(address_a),
                "party_b": str(address_b),
                "stake_a_wei": str(stake_a),
                "stake_b_wei": str(stake_b),
                "dependency": dependency,
                "pass_threshold": threshold,
                "max_attempts": attempts,
                "challenge_window": challenge_window,
                "tier_tolerance_points": tolerance,
                "mutual_breach_sink": str(sink),
                "funding_deadline": int(pact.funding_deadline),
                "rubric_authored_by_panel": bool(pact.rubric_authored),
                "checklist_a": items_a,
                "checklist_b": items_b,
                "admissibility": {
                    "a": {"defect": side_a.get("defect"), "panel_reason": side_a.get("reason")},
                    "b": {"defect": side_b.get("defect"), "panel_reason": side_b.get("reason")},
                },
                "next_step": (
                    "Party B calls accept_pact with this terms_digest and its stake as the "
                    "transaction value. Party A may call withdraw_proposal until then."
                ),
            }
        )

    def _new_obligation(
        self,
        pact_id: str,
        side: str,
        party: Address,
        commitment: str,
        stake: int,
        window: int,
        items: list,
        dependency: str,
    ) -> Obligation:
        """Build one side's obligation record, with its clock not yet started."""
        depends_on = ""
        if dependency == DEP_B_AFTER_A and side == SIDE_B:
            depends_on = SIDE_A
        elif dependency == DEP_A_AFTER_B and side == SIDE_A:
            depends_on = SIDE_B

        return Obligation(
            pact_id=pact_id,
            side=side,
            party=party,
            commitment=commitment,
            commitment_digest=_digest("commitment", side, _fold(commitment)),
            stake_wei=u256(stake),
            window_seconds=u64(window),
            depends_on=depends_on,
            checklist_json=json.dumps(items),
            checklist_items=u32(len(items)),
            checklist_digest=_checklist_digest(items),
            activated_at=u64(0),
            deadline_at=u64(0),
            attempts_used=u32(0),
            best_tier="",
            best_credit=u32(0),
            best_attempt=u32(0),
            best_evidence_url="",
            confirmed_at=u64(0),
            challenge_closes_at=u64(0),
            challenge_used=False,
            challenge_substantiated=False,
            released=False,
        )

    # -- accept and withdraw -----------------------------------------------

    @gl.public.write.payable
    def accept_pact(self, pact_id: str, terms_digest: str) -> str:
        """
        Accept a proposed pact, lock party B's stake, and start the clocks.

        `terms_digest` is mandatory and must match exactly. It is not a
        convenience: it is the mechanism by which party B is bound only to terms it
        actually reviewed. The digest covers both commitments, both stakes, both
        windows, the dependency, the threshold, the attempt allowance, the
        tolerance, the challenge window, the sink and both rubrics, so there is no
        term that could differ between what B read and what B is held to while the
        digest still matches.

        Activation is where the dependency first does work. A pact with no
        dependency starts both clocks now. A chained pact starts only the
        predecessor's clock and leaves the dependent side WAITING with no deadline
        at all, so the dependent party cannot be in breach of an obligation that
        has not begun.
        """
        pact = self._pact(pact_id)
        key = pact.pact_id

        self._require(pact.status == PACT_PROPOSED, f"Pact '{key}' is {pact.status}, not {PACT_PROPOSED}")
        self._require(
            gl.message.sender_address == pact.party_b,
            "Only the named party B may accept this pact",
        )

        now = self._now()
        self._require(
            now <= int(pact.funding_deadline),
            f"The funding window for '{key}' closed at {int(pact.funding_deadline)}",
        )

        supplied = _collapse(terms_digest).lower().removeprefix("0x")
        self._require(
            supplied == pact.terms_digest,
            "terms_digest does not match the pact on chain. Read terms_digest from "
            "the contract and accept only the terms you have reviewed.",
        )

        stake_b = int(pact.stake_b_wei)
        self._require(
            int(gl.message.value) == stake_b,
            f"Party B must send exactly {stake_b} wei, the stake declared at creation",
        )

        ob_a = self.obligations[self._ob_key(key, SIDE_A)]
        ob_b = self.obligations[self._ob_key(key, SIDE_B)]

        for ob in (ob_a, ob_b):
            if not ob.depends_on:
                ob.activated_at = u64(now)
                ob.deadline_at = u64(now + int(ob.window_seconds))

        pact.status = PACT_ACTIVE
        pact.activated_at = u64(now)

        self.pacts[key] = pact
        self.obligations[self._ob_key(key, SIDE_A)] = ob_a
        self.obligations[self._ob_key(key, SIDE_B)] = ob_b
        self.total_locked_wei = u256(int(self.total_locked_wei) + stake_b)

        state_a, state_b = self._derive_states(pact, now)
        return json.dumps(
            {
                "pact_id": key,
                "status": PACT_ACTIVE,
                "activated_at": now,
                "dependency": pact.dependency,
                "a": {
                    "state": state_a,
                    "deadline_at": int(ob_a.deadline_at),
                    "clock_started": int(ob_a.activated_at) > 0,
                },
                "b": {
                    "state": state_b,
                    "deadline_at": int(ob_b.deadline_at),
                    "clock_started": int(ob_b.activated_at) > 0,
                },
                "total_locked_wei": str(int(pact.stake_a_wei) + stake_b),
            }
        )

    @gl.public.write
    def withdraw_proposal(self, pact_id: str) -> str:
        """
        Withdraw a pact party B has not accepted, returning party A's stake.

        This is the other half of consent. Party A sees the panel authored rubric
        only after `create_pact` has run, so without a way out, A would be bound to
        a rubric it had no chance to read. Withdrawal is open to party A for as long
        as the pact is PROPOSED, and to anyone once the funding window has closed,
        so a stake cannot be stranded by an unresponsive creator.
        """
        pact = self._pact(pact_id)
        key = pact.pact_id
        self._require(pact.status == PACT_PROPOSED, f"Pact '{key}' is {pact.status}, not {PACT_PROPOSED}")

        now = self._now()
        caller = gl.message.sender_address
        expired = now > int(pact.funding_deadline)
        self._require(
            caller == pact.party_a or expired,
            "Only party A may withdraw before the funding window closes",
        )

        refund = int(pact.stake_a_wei)
        pact.status = PACT_WITHDRAWN
        pact.settled_at = u64(now)
        self.pacts[key] = pact

        settlement = Settlement(
            pact_id=key,
            outcome=OUTCOME_UNFUNDED,
            state_a=STATE_EXCUSED,
            state_b=STATE_EXCUSED,
            credit_a=u32(0),
            credit_b=u32(0),
            forfeit_a_wei=u256(0),
            forfeit_b_wei=u256(0),
            payout_a_wei=u256(refund),
            payout_b_wei=u256(0),
            sink_wei=u256(0),
            settled_at=u64(now),
            resolved_by=caller,
            summary=(
                f"Pact '{key}' was withdrawn before party B accepted. "
                f"Party A's stake of {refund} wei was returned in full."
            ),
        )
        self.settlements[key] = settlement
        self.total_locked_wei = u256(max(0, int(self.total_locked_wei) - refund))

        if refund > 0:
            gl.get_contract_at(pact.party_a).emit_transfer(value=u256(refund), on="finalized")

        return json.dumps(
            {
                "pact_id": key,
                "status": PACT_WITHDRAWN,
                "outcome": OUTCOME_UNFUNDED,
                "refunded_to": str(pact.party_a),
                "refund_wei": str(refund),
                "withdrawn_by": str(caller),
                "reason": "funding window expired" if expired and caller != pact.party_a else "withdrawn by party A",
            }
        )

    # -- submit evidence ---------------------------------------------------

    @gl.public.write
    def submit_evidence(self, pact_id: str, evidence_url: str) -> str:
        """
        Submit evidence that the caller's own commitment is performed, and have it
        adjudicated.

        The caller must be a party, the pact must be active, the caller's own
        obligation must be OPEN, and the URL must not be one this side has already
        had adjudicated. Adjudication happens inline: by the time this call returns,
        validators have independently fetched the evidence, scored the agreed
        rubric, and either confirmed the fulfilment or recorded the shortfall.

        Clearing the threshold is what starts a dependent counterparty's clock, and
        that happens in this same transaction, so the moment the next obligation
        becomes live is a consensus fact rather than anyone's assertion.

        A side that falls short keeps its attempts. Resubmitting after a panel says
        what is missing is how review actually works, and the recorded credit is the
        best reading across attempts, so trying again can never leave a party worse
        off than not trying.
        """
        pact = self._pact(pact_id)
        key = pact.pact_id
        self._require(pact.status == PACT_ACTIVE, f"Pact '{key}' is {pact.status}, not {PACT_ACTIVE}")

        caller = gl.message.sender_address
        side = self._side_of(pact, caller)
        other = self._other_side(side)

        now = self._now()
        state_a, state_b = self._derive_states(pact, now)
        my_state = state_a if side == SIDE_A else state_b

        if my_state == STATE_WAITING:
            self._require(
                False,
                f"Party {side}'s obligation has not started. It depends on party "
                f"{other}, whose fulfilment is not yet confirmed.",
            )
        self._require(my_state != STATE_FULFILLED, f"Party {side} has already been confirmed as fulfilled")
        self._require(
            my_state != STATE_EXCUSED,
            f"Party {side} is excused because party {other} breached first, so there is nothing to perform",
        )
        self._require(my_state == STATE_OPEN, f"Party {side} is {my_state} and can no longer submit evidence")

        ob = self.obligations[self._ob_key(key, side)]
        self._require(
            int(ob.attempts_used) < int(pact.max_attempts),
            f"Party {side} has used all {int(pact.max_attempts)} attempts",
        )

        url = _collapse(evidence_url)
        self._require(bool(url), "evidence_url is required")
        self._require(len(url) <= MAX_URL, f"evidence_url exceeds {MAX_URL} characters")

        # One canonical form drives both the validation and the replay index. Checking
        # the raw string instead would make the two disagree: `HTTPS://host/path` is a
        # perfectly valid URL that a case sensitive pattern refuses, while the
        # normalizer already folds its scheme, so the same page would be refused here
        # and recognised as a repeat there.
        canonical = _normalize_url(url)
        self._require(
            bool(re.match(r"^https?://[^\s/$.?#][^\s]*$", canonical)),
            "evidence_url must be an http or https URL that validators can reach",
        )

        # Replay protection. Without it, `max_attempts` is a free re-roll: submit
        # the same URL repeatedly and keep whichever reading happens to pass. The
        # digest is over the normalized URL, computed here in deterministic code,
        # never over fetched content, because a leader's fetch and a validator's
        # fetch of the same page are allowed to differ.
        url_digest = _digest("evidence", canonical)
        seen_key = f"{key}\x1f{side}\x1f{url_digest}"
        self._require(
            seen_key not in self.evidence_seen,
            "This evidence URL has already been adjudicated for this side. Submit "
            "different evidence rather than the same page again.",
        )

        checklist = json.loads(ob.checklist_json)
        threshold = int(pact.pass_threshold)

        result = self._adjudicate(
            side,
            str(ob.commitment),
            checklist,
            url,
            threshold,
        )

        attempt = int(ob.attempts_used) + 1
        tier = str(result["tier"])
        credit = _credit_for_tier(tier)
        earned = int(result.get("earned", 0))
        items = len(checklist)
        passed = credit >= threshold

        record = Adjudication(
            pact_id=key,
            side=side,
            attempt=u32(attempt),
            kind=ADJUDICATION_SUBMISSION,
            evidence_url=url[:MAX_URL],
            url_digest=url_digest,
            tier=tier,
            credit=u32(credit),
            earned_points=u32(earned),
            total_points=u32(items * ITEM_POINTS[ITEM_PRESENT]),
            passed=passed,
            finding=_render_finding(
                side, tier, credit, passed, earned, items, threshold, ADJUDICATION_SUBMISSION
            ),
            item_verdicts_json=json.dumps(list(result.get("verdicts") or [])),
            panel_score=u32(int(result.get("panel_score", 0))),
            panel_note=_advisory(result.get("note")),
            panel_missing=_advisory(result.get("missing")),
            grounds="",
            substantiated=False,
            adjudicated_at=u64(now),
            adjudicated_by=caller,
        )
        self.adjudications[f"{key}\x1f{side}\x1f{attempt}"] = record
        self.evidence_seen[seen_key] = True

        ob.attempts_used = u32(attempt)
        # Best across attempts, not latest. A party that resubmits on a panel's
        # advice and happens to score worse should not be punished for having tried.
        if credit > int(ob.best_credit) or int(ob.best_attempt) == 0:
            ob.best_credit = u32(credit)
            ob.best_tier = tier
            ob.best_attempt = u32(attempt)
            ob.best_evidence_url = url[:MAX_URL]

        activated_other = False
        if passed:
            ob.confirmed_at = u64(now)
            ob.challenge_closes_at = u64(now + int(pact.challenge_window))
            self.obligations[self._ob_key(key, side)] = ob
            activated_other = self._start_dependent_clock(pact, side, now)
        else:
            self.obligations[self._ob_key(key, side)] = ob

        state_a, state_b = self._derive_states(pact, self._now())
        resolvable, reason, _, _ = self._resolvable(pact, self._now())

        return json.dumps(
            {
                "pact_id": key,
                "side": side,
                "attempt": attempt,
                "attempts_remaining": max(0, int(pact.max_attempts) - attempt),
                "tier": tier,
                "credit": credit,
                "rubric_points": f"{earned}/{items * ITEM_POINTS[ITEM_PRESENT]}",
                "pass_threshold": threshold,
                "passed": passed,
                "finding": str(record.finding),
                "best_credit": int(ob.best_credit),
                "best_tier": str(ob.best_tier),
                "challenge_closes_at": int(ob.challenge_closes_at),
                "counterparty_clock_started": activated_other,
                "state_a": state_a,
                "state_b": state_b,
                "resolvable": resolvable,
                "resolve_blocked_by": reason,
                "panel_item_verdicts": list(result.get("verdicts") or []),
                "panel_note": _advisory(result.get("note")),
                "panel_missing": _advisory(result.get("missing")),
                "panel_score_advisory": int(result.get("panel_score", 0)),
            }
        )

    def _start_dependent_clock(self, pact: Pact, confirmed_side: str, now: int) -> bool:
        """
        Start the dependent side's clock, if this confirmation is what it waited on.

        Called in the same transaction that confirms a fulfilment, so the dependent
        obligation's deadline is anchored to a consensus bound fact. Idempotent: a
        side whose clock is already running is left alone, which matters because a
        challenge can re-confirm a fulfilment that was already confirmed once and
        must not silently extend the counterparty's deadline.
        """
        other = self._other_side(confirmed_side)
        other_key = self._ob_key(pact.pact_id, other)
        other_ob = self.obligations[other_key]

        if other_ob.depends_on != confirmed_side:
            return False
        if int(other_ob.activated_at) > 0:
            return False

        other_ob.activated_at = u64(now)
        other_ob.deadline_at = u64(now + int(other_ob.window_seconds))
        self.obligations[other_key] = other_ob
        return True

    # -- challenge ---------------------------------------------------------

    @gl.public.write
    def challenge_fulfilment(self, pact_id: str, side: str, grounds: str) -> str:
        """
        Challenge a counterparty's confirmed fulfilment.

        Resubmission lets a party that fell short argue its own case with better
        evidence. This is the other direction: the party that stands to lose money
        because the counterparty was credited gets one chance to say the evidence
        does not support the credit, and to say why.

        The panel is given the original evidence, the same agreed rubric, and the
        written grounds, and decides two things: whether the grounds are
        substantiated, and what the rubric scores on a fresh reading. The revised
        score is adopted only when the grounds are substantiated. That is what makes
        a frivolous challenge cost something without needing a bond: a challenger
        whose grounds are not substantiated has spent its single challenge and the
        original credit stands untouched.

        A revision can lower a fulfilment below the threshold, which withdraws the
        confirmation. It does not retract a dependent counterparty's clock, which
        has already started. Unwinding a deadline the other party has been working
        against would punish the party that relied on the confirmation in good
        faith, so the clock stands and the overturned side simply loses its own
        credit.

        An overturned side returns to OPEN if its own deadline has not passed and it
        has an attempt left, so it can cure the defect the challenge identified. That
        is the same courtesy the resubmission path extends to a side that fell short
        on its first try, and withholding it here would turn a challenge into a way
        to end a pact rather than a way to correct it. A side with nothing left,
        either out of time or out of attempts, goes straight to BREACHED.
        """
        pact = self._pact(pact_id)
        key = pact.pact_id
        self._require(pact.status == PACT_ACTIVE, f"Pact '{key}' is {pact.status}, not {PACT_ACTIVE}")

        target = _collapse(side).upper()
        self._require(target in (SIDE_A, SIDE_B), "side must be 'A' or 'B'")

        caller = gl.message.sender_address
        caller_side = self._side_of(pact, caller)
        self._require(
            caller_side != target,
            "A party cannot challenge its own fulfilment. Submit better evidence instead.",
        )

        text = _collapse(grounds)
        self._require(bool(text), "grounds are required, so the panel knows what to examine")
        self._require(len(text) >= 20, "grounds are too short to examine")
        self._require(len(text) <= MAX_GROUNDS, f"grounds exceed {MAX_GROUNDS} characters")

        now = self._now()
        state_a, state_b = self._derive_states(pact, now)
        target_state = state_a if target == SIDE_A else state_b
        self._require(
            target_state == STATE_FULFILLED,
            f"Party {target} is {target_state}, and only a confirmed fulfilment can be challenged",
        )

        ob = self.obligations[self._ob_key(key, target)]
        self._require(not bool(ob.challenge_used), f"Party {target}'s fulfilment has already been challenged once")
        self._require(
            int(pact.challenge_window) > 0,
            "This pact declared no challenge window, so fulfilments are final on confirmation",
        )
        self._require(
            now <= int(ob.challenge_closes_at),
            f"The challenge window for party {target} closed at {int(ob.challenge_closes_at)}",
        )

        original_url = str(ob.best_evidence_url)
        self._require(bool(original_url), "There is no evidence on record to re-examine")

        checklist = json.loads(ob.checklist_json)
        threshold = int(pact.pass_threshold)
        original_credit = int(ob.best_credit)
        original_tier = str(ob.best_tier)

        result = self._adjudicate(
            target,
            str(ob.commitment),
            checklist,
            original_url,
            threshold,
            grounds=text,
        )

        substantiated = bool(result.get("substantiated"))
        revised_tier = str(result["tier"])
        revised_credit = _credit_for_tier(revised_tier)
        earned = int(result.get("earned", 0))
        items = len(checklist)

        attempt = int(ob.attempts_used) + 1
        adopted = substantiated
        final_tier = revised_tier if adopted else original_tier
        final_credit = revised_credit if adopted else original_credit
        final_passed = final_credit >= threshold

        record = Adjudication(
            pact_id=key,
            side=target,
            attempt=u32(attempt),
            kind=ADJUDICATION_CHALLENGE,
            evidence_url=original_url,
            url_digest=_digest("evidence", _normalize_url(original_url)),
            tier=revised_tier,
            credit=u32(revised_credit),
            earned_points=u32(earned),
            total_points=u32(items * ITEM_POINTS[ITEM_PRESENT]),
            passed=revised_credit >= threshold,
            finding=_render_finding(
                target,
                revised_tier,
                revised_credit,
                revised_credit >= threshold,
                earned,
                items,
                threshold,
                ADJUDICATION_CHALLENGE,
            ),
            item_verdicts_json=json.dumps(list(result.get("verdicts") or [])),
            panel_score=u32(int(result.get("panel_score", 0))),
            panel_note=_advisory(result.get("note")),
            panel_missing=_advisory(result.get("missing")),
            grounds=text,
            substantiated=substantiated,
            adjudicated_at=u64(now),
            adjudicated_by=caller,
        )
        self.adjudications[f"{key}\x1f{target}\x1f{attempt}"] = record

        ob.attempts_used = u32(attempt)
        ob.challenge_used = True
        ob.challenge_substantiated = substantiated
        if adopted:
            ob.best_tier = final_tier
            ob.best_credit = u32(final_credit)
            ob.best_attempt = u32(attempt)
            # A substantiated challenge that drops the credit below the threshold
            # withdraws the confirmation, which is the point of the mechanism.
            ob.confirmed_at = u64(now) if final_passed else u64(0)
        # The window is closed either way, since the single challenge is spent.
        ob.challenge_closes_at = u64(now)
        self.obligations[self._ob_key(key, target)] = ob

        state_a, state_b = self._derive_states(pact, self._now())
        resolvable, reason, _, _ = self._resolvable(pact, self._now())

        return json.dumps(
            {
                "pact_id": key,
                "challenged_side": target,
                "challenger": str(caller),
                "substantiated": substantiated,
                "revision_adopted": adopted,
                "original_tier": original_tier,
                "original_credit": original_credit,
                "revised_tier": revised_tier,
                "revised_credit": revised_credit,
                "final_tier": final_tier,
                "final_credit": final_credit,
                "fulfilment_withdrawn": adopted and not final_passed,
                "rubric_points": f"{earned}/{items * ITEM_POINTS[ITEM_PRESENT]}",
                "finding": str(record.finding),
                "state_a": state_a,
                "state_b": state_b,
                "resolvable": resolvable,
                "resolve_blocked_by": reason,
                "panel_item_verdicts": list(result.get("verdicts") or []),
                "panel_note": _advisory(result.get("note")),
                "outcome_note": (
                    "The grounds were substantiated and the revised score replaces the original."
                    if adopted
                    else "The grounds were not substantiated. The original score stands and the "
                    "challenger has spent its single challenge."
                ),
            }
        )

    # -- mutual release ----------------------------------------------------

    @gl.public.write
    def agree_release(self, pact_id: str) -> str:
        """
        Consent to tearing up the pact, with both stakes returned in full.

        Needs both parties. Once both have called it the pact settles immediately at
        par, which skips any open challenge window. That is safe by construction
        rather than by policy: the only two people with a claim on the money are the
        two parties, and both of them have said so.

        It is also the deliberate escape hatch for the case the adjudication path
        cannot serve, where circumstances change and both sides would rather walk
        away than argue about evidence. A side that is owed money will not consent,
        so there is no way to use this to escape a breach.
        """
        pact = self._pact(pact_id)
        key = pact.pact_id
        self._require(pact.status == PACT_ACTIVE, f"Pact '{key}' is {pact.status}, not {PACT_ACTIVE}")

        caller = gl.message.sender_address
        side = self._side_of(pact, caller)
        consent_key = f"{key}\x1f{side}"
        self._require(consent_key not in self.release_consent, f"Party {side} has already consented to release")
        self.release_consent[consent_key] = True

        other = self._other_side(side)
        both = f"{key}\x1f{other}" in self.release_consent

        if not both:
            return json.dumps(
                {
                    "pact_id": key,
                    "consented": side,
                    "awaiting": other,
                    "settled": False,
                    "note": f"Party {other} must also call agree_release before the pact is torn up.",
                }
            )

        now = self._now()
        state_a, state_b = self._derive_states(pact, now)
        settled = self._settle(pact, state_a, state_b, released=True, resolver=caller)
        settled["consented"] = side
        settled["settled"] = True
        return json.dumps(settled)

    # -- resolve -----------------------------------------------------------

    @gl.public.write
    def resolve_pact(self, pact_id: str) -> str:
        """
        Settle a pact whose two sides have both reached a terminal state, and move
        the stakes.

        Callable by anyone. Settlement is pure arithmetic over consensus bound
        facts, so there is nothing for a caller to influence and no reason to
        restrict who may trigger it. Leaving it open means a party cannot strand the
        counterparty's money by simply never calling.

        Refuses while either side can still act: an OPEN obligation inside its
        deadline, a WAITING obligation whose predecessor has not resolved, or a
        confirmed fulfilment still inside its challenge window.
        """
        pact = self._pact(pact_id)
        key = pact.pact_id
        now = self._now()

        resolvable, reason, state_a, state_b = self._resolvable(pact, now)
        self._require(resolvable, f"Pact '{key}' cannot be resolved yet: {reason}")

        settled = self._settle(pact, state_a, state_b, released=False, resolver=gl.message.sender_address)
        return json.dumps(settled)

    def _settle(self, pact: Pact, state_a: str, state_b: str, released: bool, resolver: Address) -> dict:
        """
        Run the settlement and move the money.

        The order matters. The arithmetic is computed and its conservation checked
        before any state is written and before any transfer is queued, so a pact
        whose settlement would not conserve value reverts whole rather than paying
        one side and failing on the other.
        """
        key = pact.pact_id
        now = self._now()

        ob_a = self.obligations[self._ob_key(key, SIDE_A)]
        ob_b = self.obligations[self._ob_key(key, SIDE_B)]

        credit_a = int(ob_a.best_credit)
        credit_b = int(ob_b.best_credit)

        calc = self._compute_settlement(pact, state_a, state_b, credit_a, credit_b, released)

        pact.status = PACT_SETTLED
        pact.settled_at = u64(now)
        self.pacts[key] = pact

        ob_a.released = True
        ob_b.released = True
        self.obligations[self._ob_key(key, SIDE_A)] = ob_a
        self.obligations[self._ob_key(key, SIDE_B)] = ob_b

        summary = self._render_summary(pact, calc, state_a, state_b)
        settlement = Settlement(
            pact_id=key,
            outcome=calc["outcome"],
            state_a=state_a,
            state_b=state_b,
            credit_a=u32(calc["effective_a"]),
            credit_b=u32(calc["effective_b"]),
            forfeit_a_wei=u256(calc["forfeit_a"]),
            forfeit_b_wei=u256(calc["forfeit_b"]),
            payout_a_wei=u256(calc["payout_a"]),
            payout_b_wei=u256(calc["payout_b"]),
            sink_wei=u256(calc["sink"]),
            settled_at=u64(now),
            resolved_by=resolver,
            summary=summary,
        )
        self.settlements[key] = settlement

        total = int(pact.stake_a_wei) + int(pact.stake_b_wei)
        self.total_locked_wei = u256(max(0, int(self.total_locked_wei) - total))
        self.total_settled_wei = u256(int(self.total_settled_wei) + total)
        self.pacts_settled = u256(int(self.pacts_settled) + 1)

        # `emit_transfer` refuses a zero value, so every leg is guarded. A payout of
        # zero is a real outcome: a side that delivered nothing against a sink
        # bearing pact walks away with nothing.
        if calc["payout_a"] > 0:
            gl.get_contract_at(pact.party_a).emit_transfer(value=u256(calc["payout_a"]), on="finalized")
        if calc["payout_b"] > 0:
            gl.get_contract_at(pact.party_b).emit_transfer(value=u256(calc["payout_b"]), on="finalized")
        if calc["sink"] > 0:
            gl.get_contract_at(pact.mutual_breach_sink).emit_transfer(value=u256(calc["sink"]), on="finalized")

        return {
            "pact_id": key,
            "status": PACT_SETTLED,
            "outcome": calc["outcome"],
            "summary": summary,
            "state_a": state_a,
            "state_b": state_b,
            "credit_a": calc["effective_a"],
            "credit_b": calc["effective_b"],
            "scored_credit_a": credit_a,
            "scored_credit_b": credit_b,
            "tier_a": str(ob_a.best_tier),
            "tier_b": str(ob_b.best_tier),
            "stake_a_wei": str(int(pact.stake_a_wei)),
            "stake_b_wei": str(int(pact.stake_b_wei)),
            "forfeit_a_wei": str(calc["forfeit_a"]),
            "forfeit_b_wei": str(calc["forfeit_b"]),
            "payout_a_wei": str(calc["payout_a"]),
            "payout_b_wei": str(calc["payout_b"]),
            "sink_wei": str(calc["sink"]),
            "sink_address": str(pact.mutual_breach_sink),
            "conserves_value": calc["payout_a"] + calc["payout_b"] + calc["sink"] == total,
            "settled_at": now,
            "resolved_by": str(resolver),
        }

    # -- steward controls --------------------------------------------------

    @gl.public.write
    def set_defaults(self, defaults_json: str) -> str:
        """
        Change the registry defaults for pacts created from now on.

        Deliberately cannot touch a pact that already exists. Every term that
        decides money is copied onto the pact at creation, so a steward has no
        authority over a live pact at all. That is what makes a deployed registry
        something an integrator can build against rather than something whose rules
        can move under it.
        """
        self._require_steward()
        options = self._parse_options(defaults_json)
        self._require(bool(options), "defaults_json must name at least one default")

        allowed = (
            "pass_threshold",
            "max_attempts",
            "challenge_window",
            "funding_window",
            "tier_tolerance_points",
        )
        for name in options:
            self._require(name in allowed, f"Unknown default '{name}'. Allowed: {allowed}")

        self.default_pass_threshold = u32(
            self._resolve_option(options, "pass_threshold", int(self.default_pass_threshold), 1, 100)
        )
        self.default_max_attempts = u32(
            self._resolve_option(options, "max_attempts", int(self.default_max_attempts), 1, MAX_ATTEMPTS_CAP)
        )
        self.default_challenge_window = u64(
            self._resolve_option(options, "challenge_window", int(self.default_challenge_window), 0, MAX_CHALLENGE_WINDOW)
        )
        self.default_funding_window = u64(
            self._resolve_option(
                options, "funding_window", int(self.default_funding_window), MIN_WINDOW_SECONDS, MAX_FUNDING_WINDOW
            )
        )
        self.default_tier_tolerance = u32(
            self._resolve_option(options, "tier_tolerance_points", int(self.default_tier_tolerance), 0, 4)
        )

        return json.dumps(
            {
                "pass_threshold": int(self.default_pass_threshold),
                "max_attempts": int(self.default_max_attempts),
                "challenge_window": int(self.default_challenge_window),
                "funding_window": int(self.default_funding_window),
                "tier_tolerance_points": int(self.default_tier_tolerance),
                "applies_to": "pacts created after this transaction only",
            }
        )

    @gl.public.write
    def transfer_stewardship(self, new_steward: str) -> str:
        """Hand the registry defaults to another address. No power over live pacts moves."""
        self._require_steward()
        try:
            target = Address(_collapse(new_steward))
        except Exception:
            self._require(False, f"new_steward is not a valid address: {new_steward}")
            raise
        self._require(str(target) != ZERO_ADDRESS, "new_steward cannot be the zero address")
        previous = self.steward
        self.steward = target
        return json.dumps({"previous_steward": str(previous), "steward": str(target)})

    # -- views -------------------------------------------------------------

    @gl.public.view
    def get_registry_info(self) -> str:
        """Registry wide metadata, defaults and aggregate counters."""
        return json.dumps(
            {
                "registry_name": str(self.registry_name),
                "steward": str(self.steward),
                "created_at": int(self.created_at),
                "pact_count": len(self.pact_ids),
                "pacts_settled": str(int(self.pacts_settled)),
                "total_locked_wei": str(int(self.total_locked_wei)),
                "total_settled_wei": str(int(self.total_settled_wei)),
                "defaults": {
                    "pass_threshold": int(self.default_pass_threshold),
                    "max_attempts": int(self.default_max_attempts),
                    "challenge_window": int(self.default_challenge_window),
                    "funding_window": int(self.default_funding_window),
                    "tier_tolerance_points": int(self.default_tier_tolerance),
                },
                "tiers": {name: TIER_CREDIT[name] for name in TIER_ORDER},
                "dependency_modes": list(DEPENDENCY_MODES),
            }
        )

    @gl.public.view
    def pact_exists(self, pact_id: str) -> bool:
        """Whether a pact id is registered."""
        return _collapse(pact_id) in self.pacts

    @gl.public.view
    def get_pact_count(self) -> int:
        """How many pacts this registry holds."""
        return len(self.pact_ids)

    @gl.public.view
    def get_pact_id_at(self, index: int) -> str:
        """The pact id at a creation ordered index."""
        self._require(0 <= int(index) < len(self.pact_ids), "index out of range")
        return str(self.pact_ids[int(index)])

    @gl.public.view
    def party_pact_count_of(self, party: str) -> int:
        """How many pacts an address is a party to."""
        return int(self.party_pact_count.get(str(Address(_collapse(party))), u256(0)))

    @gl.public.view
    def party_pact_id_at(self, party: str, index: int) -> str:
        """One pact id from an address's own index."""
        key = str(Address(_collapse(party)))
        count = int(self.party_pact_count.get(key, u256(0)))
        self._require(0 <= int(index) < count, "index out of range for this party")
        return str(self.party_pact_at[f"{key}\x1f{int(index)}"])

    @gl.public.view
    def terms_digest(self, pact_id: str) -> str:
        """
        The digest party B must pass to `accept_pact`.

        Read this, read the pact and both rubrics, satisfy yourself that they are
        what you agreed, and only then accept. The digest is what makes acceptance a
        statement about specific terms rather than a blank assent.
        """
        return str(self._pact(pact_id).terms_digest)

    @gl.public.view
    def get_pact(self, pact_id: str) -> str:
        """The full pact record, with both sides' live derived states."""
        pact = self._pact(pact_id)
        now = self._now()
        state_a, state_b = ("", "")
        if pact.status in (PACT_ACTIVE, PACT_SETTLED):
            state_a, state_b = self._derive_states(pact, now)
        return json.dumps(
            {
                "pact_id": str(pact.pact_id),
                "title": str(pact.title),
                "status": str(pact.status),
                "party_a": str(pact.party_a),
                "party_b": str(pact.party_b),
                "created_by": str(pact.created_by),
                "stake_a_wei": str(int(pact.stake_a_wei)),
                "stake_b_wei": str(int(pact.stake_b_wei)),
                "pass_threshold": int(pact.pass_threshold),
                "dependency": str(pact.dependency),
                "max_attempts": int(pact.max_attempts),
                "challenge_window": int(pact.challenge_window),
                "tier_tolerance_points": int(pact.tier_tolerance_points),
                "mutual_breach_sink": str(pact.mutual_breach_sink),
                "created_at": int(pact.created_at),
                "funding_deadline": int(pact.funding_deadline),
                "activated_at": int(pact.activated_at),
                "settled_at": int(pact.settled_at),
                "terms_digest": str(pact.terms_digest),
                "rubric_authored_by_panel": bool(pact.rubric_authored),
                "state_a": state_a,
                "state_b": state_b,
                "observed_at": now,
            }
        )

    @gl.public.view
    def get_obligation(self, pact_id: str, side: str) -> str:
        """
        One side's obligation, including its rubric and its live derived state.

        `state` here is computed on read from the stored facts and the clock, which
        is why a deadline that has quietly passed shows as BREACHED without anyone
        having sent a transaction to say so.
        """
        pact = self._pact(pact_id)
        target = _collapse(side).upper()
        self._require(target in (SIDE_A, SIDE_B), "side must be 'A' or 'B'")
        ob = self._obligation(pact.pact_id, target)
        now = self._now()

        state = ""
        challenge_open = False
        if pact.status in (PACT_ACTIVE, PACT_SETTLED):
            state_a, state_b = self._derive_states(pact, now)
            state = state_a if target == SIDE_A else state_b
            challenge_open = self._challenge_open(ob, state, now)

        return json.dumps(
            {
                "pact_id": str(ob.pact_id),
                "side": str(ob.side),
                "party": str(ob.party),
                "commitment": str(ob.commitment),
                "commitment_digest": str(ob.commitment_digest),
                "stake_wei": str(int(ob.stake_wei)),
                "window_seconds": int(ob.window_seconds),
                "depends_on": str(ob.depends_on),
                "checklist": json.loads(ob.checklist_json),
                "checklist_items": int(ob.checklist_items),
                "checklist_digest": str(ob.checklist_digest),
                "activated_at": int(ob.activated_at),
                "deadline_at": int(ob.deadline_at),
                "clock_started": int(ob.activated_at) > 0,
                "seconds_remaining": max(0, int(ob.deadline_at) - now) if int(ob.deadline_at) else 0,
                "attempts_used": int(ob.attempts_used),
                "attempts_remaining": max(0, int(pact.max_attempts) - int(ob.attempts_used)),
                "best_tier": str(ob.best_tier),
                "best_credit": int(ob.best_credit),
                "best_attempt": int(ob.best_attempt),
                "best_evidence_url": str(ob.best_evidence_url),
                "confirmed_at": int(ob.confirmed_at),
                "challenge_closes_at": int(ob.challenge_closes_at),
                "challenge_used": bool(ob.challenge_used),
                "challenge_substantiated": bool(ob.challenge_substantiated),
                "state": state,
                "challenge_open": challenge_open,
                "observed_at": now,
            }
        )

    @gl.public.view
    def obligation_state(self, pact_id: str, side: str) -> str:
        """
        Just one side's live state, for callers that need the cheapest possible read.

        One of WAITING, OPEN, FULFILLED, BREACHED or EXCUSED.
        """
        pact = self._pact(pact_id)
        target = _collapse(side).upper()
        self._require(target in (SIDE_A, SIDE_B), "side must be 'A' or 'B'")
        self._require(pact.status in (PACT_ACTIVE, PACT_SETTLED), f"Pact is {pact.status}, not yet active")
        state_a, state_b = self._derive_states(pact, self._now())
        return state_a if target == SIDE_A else state_b

    @gl.public.view
    def is_fulfilled(self, pact_id: str, side: str) -> bool:
        """
        Whether one side's commitment is currently confirmed as fulfilled.

        The gate another contract calls when it only cares about one direction, for
        example releasing a dataset once the NDA side is confirmed, without waiting
        for the whole pact to settle.
        """
        pact = self._pact(pact_id)
        target = _collapse(side).upper()
        self._require(target in (SIDE_A, SIDE_B), "side must be 'A' or 'B'")
        if pact.status not in (PACT_ACTIVE, PACT_SETTLED):
            return False
        state_a, state_b = self._derive_states(pact, self._now())
        return (state_a if target == SIDE_A else state_b) == STATE_FULFILLED

    @gl.public.view
    def both_fulfilled(self, pact_id: str) -> bool:
        """Whether both sides are currently confirmed as fulfilled."""
        pact = self._pact(pact_id)
        if pact.status not in (PACT_ACTIVE, PACT_SETTLED):
            return False
        state_a, state_b = self._derive_states(pact, self._now())
        return state_a == STATE_FULFILLED and state_b == STATE_FULFILLED

    @gl.public.view
    def require_mutual_fulfilment(self, pact_id: str) -> bool:
        """
        Assert that both sides are fulfilled, reverting with the reason if not.

        The reverting counterpart to `both_fulfilled`, for consumers that would
        rather propagate a readable reason than branch on a bare boolean. A
        cross-contract view call that reverts surfaces this message to the caller.
        """
        pact = self._pact(pact_id)
        self._require(
            pact.status in (PACT_ACTIVE, PACT_SETTLED),
            f"Pact '{pact.pact_id}' is {pact.status} and has no fulfilment to report",
        )
        state_a, state_b = self._derive_states(pact, self._now())
        self._require(state_a == STATE_FULFILLED, f"Party A is {state_a}, not {STATE_FULFILLED}")
        self._require(state_b == STATE_FULFILLED, f"Party B is {state_b}, not {STATE_FULFILLED}")
        return True

    @gl.public.view
    def is_resolvable(self, pact_id: str) -> str:
        """Whether the pact can be settled now, and what is blocking it if not."""
        pact = self._pact(pact_id)
        now = self._now()
        resolvable, reason, state_a, state_b = self._resolvable(pact, now)
        return json.dumps(
            {
                "pact_id": str(pact.pact_id),
                "resolvable": resolvable,
                "blocked_by": reason,
                "status": str(pact.status),
                "state_a": state_a,
                "state_b": state_b,
                "observed_at": now,
            }
        )

    @gl.public.view
    def settlement_preview(self, pact_id: str) -> str:
        """
        The settlement this pact would reach if it were resolved right now.

        Runs exactly the arithmetic `resolve_pact` runs, against the states derived
        at this instant, and moves nothing. Parties use it to see where they stand
        mid pact, and integrators use it to show a counterfactual without sending a
        transaction. The conservation check runs here too, so a preview that would
        not balance surfaces as a revert rather than as a misleading number.
        """
        pact = self._pact(pact_id)
        now = self._now()

        if pact.status == PACT_SETTLED:
            return self.get_settlement(pact.pact_id)
        self._require(pact.status == PACT_ACTIVE, f"Pact is {pact.status}, so there is nothing to preview")

        state_a, state_b = self._derive_states(pact, now)
        ob_a = self.obligations[self._ob_key(pact.pact_id, SIDE_A)]
        ob_b = self.obligations[self._ob_key(pact.pact_id, SIDE_B)]
        calc = self._compute_settlement(
            pact, state_a, state_b, int(ob_a.best_credit), int(ob_b.best_credit), False
        )
        resolvable, reason, _, _ = self._resolvable(pact, now)

        return json.dumps(
            {
                "pact_id": str(pact.pact_id),
                "hypothetical": True,
                "resolvable_now": resolvable,
                "blocked_by": reason,
                "outcome": calc["outcome"],
                "state_a": state_a,
                "state_b": state_b,
                "credit_a": calc["effective_a"],
                "credit_b": calc["effective_b"],
                "forfeit_a_wei": str(calc["forfeit_a"]),
                "forfeit_b_wei": str(calc["forfeit_b"]),
                "payout_a_wei": str(calc["payout_a"]),
                "payout_b_wei": str(calc["payout_b"]),
                "sink_wei": str(calc["sink"]),
                "summary": self._render_summary(pact, calc, state_a, state_b),
                "observed_at": now,
            }
        )

    @gl.public.view
    def get_settlement(self, pact_id: str) -> str:
        """The settled record of a pact, including the full arithmetic."""
        key = _collapse(pact_id)
        self._require(key in self.settlements, f"Pact '{key}' has not settled")
        s = self.settlements[key]
        payout_a, payout_b, sink = int(s.payout_a_wei), int(s.payout_b_wei), int(s.sink_wei)
        return json.dumps(
            {
                "pact_id": str(s.pact_id),
                "outcome": str(s.outcome),
                "summary": str(s.summary),
                "state_a": str(s.state_a),
                "state_b": str(s.state_b),
                "credit_a": int(s.credit_a),
                "credit_b": int(s.credit_b),
                "forfeit_a_wei": str(int(s.forfeit_a_wei)),
                "forfeit_b_wei": str(int(s.forfeit_b_wei)),
                "payout_a_wei": str(payout_a),
                "payout_b_wei": str(payout_b),
                "sink_wei": str(sink),
                "total_out_wei": str(payout_a + payout_b + sink),
                "settled_at": int(s.settled_at),
                "resolved_by": str(s.resolved_by),
            }
        )

    @gl.public.view
    def get_adjudication(self, pact_id: str, side: str, attempt: int) -> str:
        """
        One panel reading, in full.

        `tier` and `substantiated` are the independently agreed fields; credit and
        pass/fail derive from that tier. Everything prefixed `panel_`, including
        `panel_earned_points`, is advisory and never drives settlement.
        """
        key = _collapse(pact_id)
        target = _collapse(side).upper()
        self._require(target in (SIDE_A, SIDE_B), "side must be 'A' or 'B'")
        record_key = f"{key}\x1f{target}\x1f{int(attempt)}"
        self._require(record_key in self.adjudications, f"No attempt {int(attempt)} for side {target}")
        a = self.adjudications[record_key]
        return json.dumps(
            {
                "pact_id": str(a.pact_id),
                "side": str(a.side),
                "attempt": int(a.attempt),
                "kind": str(a.kind),
                "evidence_url": str(a.evidence_url),
                "url_digest": str(a.url_digest),
                "consensus_bound": {
                    "tier": str(a.tier),
                    "credit": int(a.credit),
                    "total_points": int(a.total_points),
                    "passed": bool(a.passed),
                    "substantiated": bool(a.substantiated),
                },
                "finding": str(a.finding),
                "grounds": str(a.grounds),
                "panel_earned_points": int(a.earned_points),
                "panel_item_verdicts": json.loads(a.item_verdicts_json or "[]"),
                "panel_score_advisory": int(a.panel_score),
                "panel_note": str(a.panel_note),
                "panel_missing": str(a.panel_missing),
                "adjudicated_at": int(a.adjudicated_at),
                "adjudicated_by": str(a.adjudicated_by),
            }
        )

    @gl.public.view
    def list_adjudications(self, pact_id: str, side: str) -> str:
        """Every panel reading for one side, in attempt order."""
        pact = self._pact(pact_id)
        target = _collapse(side).upper()
        self._require(target in (SIDE_A, SIDE_B), "side must be 'A' or 'B'")
        ob = self._obligation(pact.pact_id, target)

        entries = []
        for attempt in range(1, int(ob.attempts_used) + 1):
            record_key = f"{pact.pact_id}\x1f{target}\x1f{attempt}"
            if record_key not in self.adjudications:
                continue
            a = self.adjudications[record_key]
            entries.append(
                {
                    "attempt": int(a.attempt),
                    "kind": str(a.kind),
                    "tier": str(a.tier),
                    "credit": int(a.credit),
                    "earned_points": int(a.earned_points),
                    "total_points": int(a.total_points),
                    "passed": bool(a.passed),
                    "substantiated": bool(a.substantiated),
                    "evidence_url": str(a.evidence_url),
                    "finding": str(a.finding),
                    "adjudicated_at": int(a.adjudicated_at),
                }
            )
        return json.dumps(
            {
                "pact_id": str(pact.pact_id),
                "side": target,
                "attempts_used": int(ob.attempts_used),
                "best_attempt": int(ob.best_attempt),
                "best_tier": str(ob.best_tier),
                "best_credit": int(ob.best_credit),
                "adjudications": entries,
            }
        )

    @gl.public.view
    def evidence_already_used(self, pact_id: str, side: str, evidence_url: str) -> bool:
        """
        Whether this side has already had this URL adjudicated.

        Check before submitting. Replay protection is what stops `max_attempts`
        becoming a free re-roll of the same page, so a repeat submission reverts, and
        knowing that in advance saves a wasted transaction.
        """
        pact = self._pact(pact_id)
        target = _collapse(side).upper()
        self._require(target in (SIDE_A, SIDE_B), "side must be 'A' or 'B'")
        digest = _digest("evidence", _normalize_url(evidence_url))
        return f"{pact.pact_id}\x1f{target}\x1f{digest}" in self.evidence_seen

    @gl.public.view
    def release_consent_of(self, pact_id: str, side: str) -> bool:
        """Whether one side has consented to a mutual release."""
        pact = self._pact(pact_id)
        target = _collapse(side).upper()
        self._require(target in (SIDE_A, SIDE_B), "side must be 'A' or 'B'")
        return f"{pact.pact_id}\x1f{target}" in self.release_consent

    @gl.public.view
    def get_rubric(self, pact_id: str, side: str) -> str:
        """
        The rubric one side will be scored against, and its digest.

        Read this before accepting a pact. It is the whole question every later
        panel answers, it is covered by `terms_digest`, and it cannot change after
        the pact is created.
        """
        pact = self._pact(pact_id)
        target = _collapse(side).upper()
        self._require(target in (SIDE_A, SIDE_B), "side must be 'A' or 'B'")
        ob = self._obligation(pact.pact_id, target)
        items = json.loads(ob.checklist_json)
        return json.dumps(
            {
                "pact_id": str(pact.pact_id),
                "side": target,
                "commitment": str(ob.commitment),
                "checklist": items,
                "checklist_items": len(items),
                "checklist_digest": str(ob.checklist_digest),
                "authored_by_panel": bool(pact.rubric_authored),
                "total_points": len(items) * ITEM_POINTS[ITEM_PRESENT],
                "pass_threshold": int(pact.pass_threshold),
                "tiers": {name: TIER_CREDIT[name] for name in TIER_ORDER},
                "note": (
                    "Every validator scores this exact list. A tier is the point ratio, "
                    "not a judgment call, and the credit is a lookup on the tier."
                ),
            }
        )
