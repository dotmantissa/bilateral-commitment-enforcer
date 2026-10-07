# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""
Commitment Gated Release
========================

A reference consumer for the Bilateral Commitment Enforcer, and a working
contract in its own right.

What it is for
--------------
The enforcer settles money between two parties. Plenty of real workflows need
something else to happen when a commitment lands: a dataset becomes readable, a
licence key is handed over, a repository is unarchived, a downstream contract is
unpaused. That action usually belongs to a third contract, which needs a
trustworthy answer to one question: has this side of the pact actually performed?

This contract is that pattern, written out. An owner registers a payload against
a pact and names the condition that unlocks it. Recipients call `claim`, and the
payload is returned only if the enforcer says the condition is met. Nothing about
the gate is simulated: `claim` makes a real synchronous view call into the
deployed enforcer and acts on what comes back.

Why it is a separate contract
-----------------------------
Because that is how builders will integrate the primitive, and a gate that has
never been called across a contract boundary is a gate nobody has tested. The
enforcer's view methods are the integration surface, so this contract exercises
the three that matter:

    is_fulfilled(pact_id, side)       one direction performed
    both_fulfilled(pact_id)           the whole pact performed
    require_mutual_fulfilment(id)     the same, reverting with a readable reason

Three gate modes follow directly:

    SIDE_A      unlock when party A's commitment is confirmed
    SIDE_B      unlock when party B's commitment is confirmed
    MUTUAL      unlock only when both are confirmed

The one way dependency case is the interesting one. A pact declared
`b_after_a` means party B's clock does not start until A is confirmed, so a
release gated on `SIDE_A` opens at exactly the moment B's obligation begins. That
is how "the data is released once the NDA is confirmed, and the counterparty then
has fourteen days to publish" is expressed: this contract holds the data, the
enforcer holds the clock.

What this contract does not do
------------------------------
It does not re-adjudicate, it does not score, and it does not hold stakes. It
asks the enforcer and obeys. Every judgment stays in the primitive, which is the
point of the separation: a consumer should not need to understand consensus to
rely on it.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

import json
import re

from genlayer import *


GATE_SIDE_A = "SIDE_A"
GATE_SIDE_B = "SIDE_B"
GATE_MUTUAL = "MUTUAL"
GATE_MODES = (GATE_SIDE_A, GATE_SIDE_B, GATE_MUTUAL)

MAX_KEY = 128
MAX_LABEL = 300
MAX_PAYLOAD = 8000
MAX_RECIPIENTS = 64

ERROR_EXPECTED = "[EXPECTED]"


def _collapse(text) -> str:
    """Collapse whitespace runs and trim. Matches the enforcer's own normalization."""
    return re.sub(r"\s+", " ", str(text)).strip()


@allow_storage
@dataclass
class Release:
    """
    A payload held behind a pact gate.

    `claims` counts successful claims rather than attempts, so a recipient blocked
    by a gate that is not yet open can come back later without having spent
    anything.
    """
    release_key: str
    label: str
    pact_id: str
    gate: str
    payload: str
    owner: Address
    open_to_all: bool
    created_at: u64
    claims: u256
    revoked: bool


class CommitmentGatedRelease(gl.Contract):
    """
    Holds payloads that unlock when a Bilateral Commitment Enforcer pact says the
    named commitment has been performed.
    """

    enforcer: Address
    owner: Address
    created_at: u64

    releases: TreeMap[str, Release]
    release_keys: DynArray[str]

    # Keyed f"{release_key}\x1f{address}".
    allowed: TreeMap[str, bool]
    claimed: TreeMap[str, bool]

    total_claims: u256

    def __init__(self, enforcer_address: str):
        self.enforcer = Address(_collapse(enforcer_address))
        self.owner = gl.message.sender_address
        self.created_at = u64(self._now())
        self.total_claims = u256(0)

    # -- internals ---------------------------------------------------------

    def _now(self) -> int:
        """Transaction time as a Unix second, pinned by GenVM across validators."""
        try:
            return int(datetime.now(timezone.utc).timestamp())
        except Exception:
            return 0

    def _require(self, condition: bool, message: str) -> None:
        if not condition:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} {message}")

    def _release(self, release_key: str) -> Release:
        key = _collapse(release_key)
        self._require(key in self.releases, f"No release with key '{key}'")
        return self.releases[key]

    def _gate_open(self, release: Release) -> tuple:
        """
        Ask the enforcer whether this release's gate is open.

        A real synchronous cross-contract view call. The reverting variant is used
        for the mutual gate so that the enforcer's own reason ("Party B is OPEN,
        not FULFILLED") reaches this caller instead of being flattened to a
        boolean, which is what makes a blocked claim diagnosable.
        """
        enforcer = gl.get_contract_at(self.enforcer)

        if release.gate == GATE_MUTUAL:
            try:
                enforcer.view().require_mutual_fulfilment(str(release.pact_id))
                return (True, "both parties are confirmed as fulfilled")
            except Exception as exc:
                detail = _collapse(getattr(exc, "message", None) or exc)
                return (False, detail or "the enforcer reports the pact is not mutually fulfilled")

        side = "A" if release.gate == GATE_SIDE_A else "B"
        try:
            ok = bool(enforcer.view().is_fulfilled(str(release.pact_id), side))
        except Exception as exc:
            detail = _collapse(getattr(exc, "message", None) or exc)
            self._require(False, f"The enforcer could not answer for pact '{release.pact_id}': {detail}")
            raise
        if ok:
            return (True, f"party {side} is confirmed as fulfilled")
        return (False, f"party {side} is not confirmed as fulfilled")

    # -- writes ------------------------------------------------------------

    @gl.public.write
    def register_release(
        self,
        release_key: str,
        label: str,
        pact_id: str,
        gate: str,
        payload: str,
        open_to_all: bool,
    ) -> str:
        """
        Register a payload behind a pact gate.

        The pact must already exist on the enforcer, which is checked here rather
        than at claim time so that a typo in a pact id fails for the person who
        made it instead of for a recipient weeks later.

        `open_to_all` false means only addresses added through `allow_recipient`
        may claim.
        """
        self._require(gl.message.sender_address == self.owner, "Only the owner may register a release")

        key = _collapse(release_key)
        self._require(bool(key), "release_key is required")
        self._require(len(key) <= MAX_KEY, f"release_key exceeds {MAX_KEY} characters")
        self._require(key not in self.releases, f"Release '{key}' already exists")

        clean_label = _collapse(label)[:MAX_LABEL]
        self._require(bool(clean_label), "label is required")

        self._require(gate in GATE_MODES, f"gate must be one of {GATE_MODES}")

        body = str(payload)
        self._require(bool(_collapse(body)), "payload is required")
        self._require(len(body) <= MAX_PAYLOAD, f"payload exceeds {MAX_PAYLOAD} characters")

        pact = _collapse(pact_id)
        self._require(bool(pact), "pact_id is required")
        enforcer = gl.get_contract_at(self.enforcer)
        try:
            exists = bool(enforcer.view().pact_exists(pact))
        except Exception as exc:
            detail = _collapse(getattr(exc, "message", None) or exc)
            self._require(False, f"Could not reach the enforcer at {self.enforcer}: {detail}")
            raise
        self._require(exists, f"The enforcer has no pact '{pact}'")

        self.releases[key] = Release(
            release_key=key,
            label=clean_label,
            pact_id=pact,
            gate=gate,
            payload=body,
            owner=gl.message.sender_address,
            open_to_all=bool(open_to_all),
            created_at=u64(self._now()),
            claims=u256(0),
            revoked=False,
        )
        self.release_keys.append(key)

        return json.dumps(
            {
                "release_key": key,
                "label": clean_label,
                "pact_id": pact,
                "gate": gate,
                "open_to_all": bool(open_to_all),
                "enforcer": str(self.enforcer),
            }
        )

    @gl.public.write
    def allow_recipient(self, release_key: str, recipient: str) -> str:
        """Add one address to a closed release's allow list."""
        release = self._release(release_key)
        self._require(gl.message.sender_address == release.owner, "Only the release owner may add recipients")
        self._require(not bool(release.revoked), f"Release '{release.release_key}' is revoked")

        try:
            target = Address(_collapse(recipient))
        except Exception:
            self._require(False, f"recipient is not a valid address: {recipient}")
            raise

        entry = f"{release.release_key}\x1f{target}"
        self._require(entry not in self.allowed, f"{target} is already allowed")
        self.allowed[entry] = True
        return json.dumps({"release_key": str(release.release_key), "recipient": str(target), "allowed": True})

    @gl.public.write
    def revoke_release(self, release_key: str) -> str:
        """Close a release permanently. Already issued payloads are not recalled."""
        release = self._release(release_key)
        self._require(gl.message.sender_address == release.owner, "Only the release owner may revoke")
        self._require(not bool(release.revoked), "Already revoked")
        release.revoked = True
        self.releases[release.release_key] = release
        return json.dumps({"release_key": str(release.release_key), "revoked": True})

    @gl.public.write
    def claim(self, release_key: str) -> str:
        """
        Claim a payload, if the enforcer says the gate is open.

        This is the whole point of the contract. The gate is evaluated against the
        enforcer's live view of the pact at the moment of the call, so a release
        gated on a commitment that has not been adjudicated yet is refused with the
        enforcer's own reason attached.
        """
        release = self._release(release_key)
        caller = gl.message.sender_address

        self._require(not bool(release.revoked), f"Release '{release.release_key}' is revoked")

        if not bool(release.open_to_all):
            self._require(
                f"{release.release_key}\x1f{caller}" in self.allowed,
                "This release is restricted and you are not on its allow list",
            )

        claim_key = f"{release.release_key}\x1f{caller}"
        self._require(claim_key not in self.claimed, "You have already claimed this release")

        open_now, reason = self._gate_open(release)
        self._require(
            open_now,
            f"The gate on '{release.release_key}' is closed: {reason}",
        )

        self.claimed[claim_key] = True
        release.claims = u256(int(release.claims) + 1)
        self.releases[release.release_key] = release
        self.total_claims = u256(int(self.total_claims) + 1)

        return json.dumps(
            {
                "release_key": str(release.release_key),
                "label": str(release.label),
                "pact_id": str(release.pact_id),
                "gate": str(release.gate),
                "gate_reason": reason,
                "claimed_by": str(caller),
                "payload": str(release.payload),
                "claims": int(release.claims),
            }
        )

    # -- views -------------------------------------------------------------

    @gl.public.view
    def get_info(self) -> str:
        """Which enforcer this consumer is bound to, and its aggregate counters."""
        return json.dumps(
            {
                "enforcer": str(self.enforcer),
                "owner": str(self.owner),
                "created_at": int(self.created_at),
                "release_count": len(self.release_keys),
                "total_claims": str(int(self.total_claims)),
                "gate_modes": list(GATE_MODES),
            }
        )

    @gl.public.view
    def get_release(self, release_key: str) -> str:
        """A release's metadata and its live gate status. Never the payload."""
        release = self._release(release_key)
        open_now, reason = self._gate_open(release)
        return json.dumps(
            {
                "release_key": str(release.release_key),
                "label": str(release.label),
                "pact_id": str(release.pact_id),
                "gate": str(release.gate),
                "owner": str(release.owner),
                "open_to_all": bool(release.open_to_all),
                "revoked": bool(release.revoked),
                "created_at": int(release.created_at),
                "claims": int(release.claims),
                "gate_open": open_now,
                "gate_reason": reason,
                "payload_length": len(str(release.payload)),
            }
        )

    @gl.public.view
    def gate_status(self, release_key: str) -> str:
        """
        Whether a release's gate is open right now, and why.

        A read only probe of the cross-contract gate, so a frontend can show the
        state without anyone sending a transaction that would be refused.
        """
        release = self._release(release_key)
        open_now, reason = self._gate_open(release)
        return json.dumps(
            {
                "release_key": str(release.release_key),
                "pact_id": str(release.pact_id),
                "gate": str(release.gate),
                "open": open_now,
                "reason": reason,
            }
        )

    @gl.public.view
    def has_claimed(self, release_key: str, recipient: str) -> bool:
        """Whether an address has already claimed a release."""
        release = self._release(release_key)
        return f"{release.release_key}\x1f{Address(_collapse(recipient))}" in self.claimed

    @gl.public.view
    def is_recipient_allowed(self, release_key: str, recipient: str) -> bool:
        """Whether an address may claim: true for an open release, allow list otherwise."""
        release = self._release(release_key)
        if bool(release.open_to_all):
            return True
        return f"{release.release_key}\x1f{Address(_collapse(recipient))}" in self.allowed

    @gl.public.view
    def get_release_count(self) -> int:
        """How many releases this contract holds."""
        return len(self.release_keys)

    @gl.public.view
    def get_release_key_at(self, index: int) -> str:
        """The release key at a registration ordered index."""
        self._require(0 <= int(index) < len(self.release_keys), "index out of range")
        return str(self.release_keys[int(index)])
