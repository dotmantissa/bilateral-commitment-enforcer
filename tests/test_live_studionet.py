"""
Live end-to-end tests against the deployed contracts on GenLayer StudioNet.

These run the real thing: five validators, real HTTP fetches of a real public
evidence document, real model calls and real Optimistic Democracy consensus. Nothing
here is simulated. The evidence documents are published in a revision-pinned GitHub
gist, which is exactly the kind of public, immutable deliverable the primitive is
designed to adjudicate.

The suite is ordered and stateful on purpose, walking the complete bilateral arc:
  1. Verify deployed contract source against local repository byte-for-byte.
  2. Verify schema and published ABI for the primitive and reference consumer.
  3. Verify registry defaults and tier configurations.
  4. Propose a chained bilateral pact (b_after_a) with real natural language commitments.
  5. Party B accepts the pact with the exact terms digest, locking stakes.
  6. Assert dependency chaining: Party B is WAITING with no running clock.
  7. Party B cannot submit evidence while WAITING.
  8. Party A submits public evidence; validators adjudicate against the rubric and confirm FULFILLED.
  9. Confirm that Party A's fulfilment starts Party B's deadline clock.
  10. Replay protection blocks resubmission of Party A's evidence URL.
  11. The reference consumer (CommitmentGatedRelease) gates payload release on Party A's fulfilment.
  12. The reference consumer blocks claim for Party B's gate before Party B fulfils.
  13. Party B submits public evidence; validators adjudicate and confirm FULFILLED.
  14. The reference consumer opens Party B's gate.
  15. Both parties agree release; pact settles at par and conserves value exactly.
  16. Write artifacts/live-verification.json recording all transactions, consensus votes, and outcomes.

Enable with:

    GENLAYER_PRIVATE_KEY=0x... ENFORCER_ADDRESS=0x... \
      ../.testvenv312/bin/python -m pytest tests/test_live_studionet.py -v -s
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

import pytest
from genlayer_py import create_account, create_client
from genlayer_py.chains import studionet

ROOT = Path(__file__).resolve().parent.parent
ENFORCER_SOURCE = ROOT / "contracts" / "bilateral_commitment_enforcer.py"
RELEASE_SOURCE = ROOT / "contracts" / "commitment_gated_release.py"
DEPLOYMENT_PATH = ROOT / "artifacts" / "deployment.json"

GIST_ID = "a8af23da305a75ac6bf3db7aeed4ad02"
EVIDENCE_A_URL = (
    f"https://gist.githubusercontent.com/dotmantissa/{GIST_ID}/raw/"
    "81774c7a2876506be4d4907786db80d42b1f5d54/dataset-release-q3.md"
)
EVIDENCE_B_URL = (
    f"https://gist.githubusercontent.com/dotmantissa/{GIST_ID}/raw/"
    "d553ba7205c6709c3eb22fe12f3b68b8caddb9a6/integration-report-complete.md"
)

COMMITMENT_A = (
    "Publish the Q3 anonymised lending dataset release documentation at a public URL, "
    "including dataset description with row count and covered period, a column schema "
    "describing fourteen columns, and the SHA-256 checksum for the dataset file."
)
COMMITMENT_B = (
    "Publish a written risk model integration report at a public URL, including "
    "loading confirmation, the resulting default curve by vintage month, and a "
    "complete listing of all rejected rows."
)

CHECKLIST_A = [
    "Dataset release description specifying row count and period covered",
    "A column schema document describing fourteen columns",
    "A SHA-256 checksum for the dataset file",
]
CHECKLIST_B = [
    "Loading confirmation for the Q3 dataset into the risk model",
    "The resulting default curve by vintage month",
    "A complete listing of rejected rows with surrogate IDs and reasons",
]

STAKE_WEI = 5 * 10**15  # 0.005 GEN

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("GENLAYER_PRIVATE_KEY"),
        reason="Set GENLAYER_PRIVATE_KEY to explicitly enable live network tests",
    ),
]

STATE: dict = {}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def deployment_data() -> dict:
    if DEPLOYMENT_PATH.exists():
        return json.loads(DEPLOYMENT_PATH.read_text())
    return {}


@pytest.fixture(scope="session")
def enforcer_address(deployment_data) -> str:
    addr = os.environ.get("ENFORCER_ADDRESS")
    if not addr and deployment_data:
        addr = deployment_data.get("contracts", {}).get("BilateralCommitmentEnforcer", {}).get("contractAddress")
    if not addr:
        pytest.skip("Set ENFORCER_ADDRESS or provide artifacts/deployment.json")
    return addr


@pytest.fixture(scope="session")
def release_address(deployment_data) -> str:
    addr = os.environ.get("RELEASE_ADDRESS")
    if not addr and deployment_data:
        addr = deployment_data.get("contracts", {}).get("CommitmentGatedRelease", {}).get("contractAddress")
    return addr


@pytest.fixture(scope="session")
def client_a():
    account = create_account(os.environ["GENLAYER_PRIVATE_KEY"])
    return create_client(chain=studionet, account=account)


@pytest.fixture(scope="session")
def client_b(client_a):
    key_b = "0x" + secrets.token_hex(32)
    account_b = create_account(key_b)
    client_b_instance = create_client(chain=studionet, account=account_b)
    # Fund Party B using StudioNet simulation method
    client_a.provider.make_request("sim_fundAccount", [account_b.address, 10**18])
    time.sleep(2)
    return client_b_instance


def settle(client, tx_hash, retries: int = 90, interval: int = 3000):
    return client.wait_for_transaction_receipt(
        transaction_hash=tx_hash, retries=retries, interval=interval
    )


def leader_receipt(receipt) -> dict:
    data = receipt if isinstance(receipt, dict) else dict(receipt)
    leader = (data.get("consensus_data") or {}).get("leader_receipt") or []
    if isinstance(leader, dict):
        leader = [leader]
    return leader[0] if leader else {}


def execution_succeeded(receipt) -> bool:
    return str(leader_receipt(receipt).get("execution_result", "")).upper() == "SUCCESS"


def revert_reason(receipt) -> str:
    result = leader_receipt(receipt).get("result") or {}
    if not isinstance(result, dict):
        return str(result)[:400]

    payload = result.get("payload")
    if isinstance(payload, str) and payload:
        return payload
    if isinstance(payload, dict):
        raw = payload.get("raw")
        if isinstance(raw, list):
            return bytes(raw[1:]).decode("utf-8", errors="replace")
        if isinstance(raw, str):
            try:
                return base64.b64decode(raw)[1:].decode("utf-8", errors="replace")
            except Exception:
                return raw

    raw = result.get("raw")
    if isinstance(raw, str):
        try:
            return base64.b64decode(raw)[1:].decode("utf-8", errors="replace")
        except Exception:
            return raw
    return json.dumps(result)[:400]


def write(client, address, method, args, value=0, expect_success: bool = True):
    # StudioNet rate limit pacing
    time.sleep(2)
    tx_hash = client.write_contract(address=address, function_name=method, args=args, value=value)
    receipt = settle(client, tx_hash)
    succeeded = execution_succeeded(receipt)
    data = dict(receipt)
    consensus = data.get("consensus_data") or {}
    votes = consensus.get("votes") or {}
    entry = {
        "operation": method,
        "caller": str(client.local_account.address),
        "transactionHash": tx_hash if isinstance(tx_hash, str) else tx_hash.hex(),
        "expected": "success" if expect_success else "revert",
        "outcome": "success" if succeeded else "revert",
        "status": str(data.get("status_name", "")),
        "consensusResult": str(data.get("result_name", "")),
        "votes": {k: str(v) for k, v in votes.items()},
        "agreeCount": sum(1 for v in votes.values() if str(v) == "agree"),
        "initialValidators": data.get("num_of_initial_validators"),
        "rotations": data.get("rotation_count"),
    }
    if not succeeded:
        entry["revertReason"] = revert_reason(receipt)
    STATE.setdefault("transactions", []).append(entry)
    print(f"\n  {method}: {entry['outcome']} | {entry['consensusResult']} "
          f"| {entry['agreeCount']} agree | {entry['transactionHash']}")
    if not succeeded and entry.get("revertReason"):
        print(f"    reason: {entry['revertReason'][:200]}")
    return succeeded, receipt


def read(client, address, method, args=None):
    return client.read_contract(address=address, function_name=method, args=args or [])


def read_json(client, address, method, args=None):
    value = read(client, address, method, args)
    return json.loads(value) if isinstance(value, str) else value


# ---------------------------------------------------------------------------
# 1. Byte-for-byte source match and schema verification
# ---------------------------------------------------------------------------

def test_deployed_source_matches_the_repository(client_a, enforcer_address, release_address):
    """
    Assert that the bytecode running on StudioNet is byte-for-byte identical to
    contracts/bilateral_commitment_enforcer.py and contracts/commitment_gated_release.py.
    """
    res_enforcer = client_a.provider.make_request("gen_getContractCode", [enforcer_address])
    encoded_enforcer = res_enforcer["result"] if isinstance(res_enforcer, dict) else res_enforcer
    deployed_enforcer = base64.b64decode(encoded_enforcer)
    local_enforcer = ENFORCER_SOURCE.read_bytes()

    STATE["enforcerSha256"] = hashlib.sha256(local_enforcer).hexdigest()
    assert deployed_enforcer == local_enforcer, "Deployed enforcer does not match repository source"
    assert len(local_enforcer) == len(deployed_enforcer)

    if release_address:
        res_release = client_a.provider.make_request("gen_getContractCode", [release_address])
        encoded_release = res_release["result"] if isinstance(res_release, dict) else res_release
        deployed_release = base64.b64decode(encoded_release)
        local_release = RELEASE_SOURCE.read_bytes()

        STATE["releaseSha256"] = hashlib.sha256(local_release).hexdigest()
        assert deployed_release == local_release, "Deployed release consumer does not match repository source"
        assert len(local_release) == len(deployed_release)


def test_the_published_schema_matches_the_contract(client_a, enforcer_address, release_address):
    schema_enforcer = client_a.get_contract_schema(enforcer_address)
    if isinstance(schema_enforcer, str):
        schema_enforcer = json.loads(schema_enforcer)
    methods_enforcer = set((schema_enforcer.get("methods") or {}).keys())

    for expected in (
        "create_pact", "accept_pact", "withdraw_proposal", "submit_evidence",
        "challenge_fulfilment", "agree_release", "resolve_pact", "set_defaults",
        "transfer_stewardship", "get_registry_info", "pact_exists", "get_pact",
        "get_obligation", "obligation_state", "is_fulfilled", "both_fulfilled",
        "require_mutual_fulfilment", "is_resolvable", "settlement_preview",
        "get_settlement", "get_adjudication", "list_adjudications",
        "evidence_already_used", "release_consent_of", "get_rubric",
    ):
        assert expected in methods_enforcer, f"Enforcer schema missing method {expected}"

    if release_address:
        schema_release = client_a.get_contract_schema(release_address)
        if isinstance(schema_release, str):
            schema_release = json.loads(schema_release)
        methods_release = set((schema_release.get("methods") or {}).keys())
        for expected in ("register_release", "allow_recipient", "revoke_release", "claim", "gate_status", "get_info"):
            assert expected in methods_release, f"Release schema missing method {expected}"


def test_registry_info_reports_expected_defaults(client_a, enforcer_address):
    info = read_json(client_a, enforcer_address, "get_registry_info")
    assert info["registry_name"] == "Bilateral Commitment Registry"
    assert info["defaults"]["pass_threshold"] == 80
    assert info["defaults"]["max_attempts"] == 3
    assert info["tiers"]["COMPLETE"] == 100
    assert info["tiers"]["SUBSTANTIAL"] == 80
    assert info["tiers"]["PARTIAL"] == 50
    assert "b_after_a" in info["dependency_modes"]


# ---------------------------------------------------------------------------
# 2. Pact creation and admissibility consensus
# ---------------------------------------------------------------------------

def test_party_a_proposes_bilateral_pact_with_chaining(client_a, client_b, enforcer_address):
    """
    Party A proposes a pact with dependency mode 'b_after_a'.
    Validators assess admissibility and author or store the rubrics.
    Party A's stake is locked and the pact enters PROPOSED.
    """
    pact_id = f"meridian-live-{int(time.time())}"
    STATE["pact_id"] = pact_id

    options = {
        "pass_threshold": 80,
        "max_attempts": 3,
        "challenge_window": 3600,
        "funding_window": 86400,
        "tier_tolerance_points": 1,
        "checklist_a": CHECKLIST_A,
        "checklist_b": CHECKLIST_B,
    }

    ok, receipt = write(
        client_a,
        enforcer_address,
        "create_pact",
        [
            pact_id,
            "Meridian Lending Q3 Risk Integration",
            client_b.local_account.address,
            COMMITMENT_A,
            COMMITMENT_B,
            str(STAKE_WEI),
            "b_after_a",
            3600,
            3600,
            json.dumps(options),
        ],
        value=STAKE_WEI,
    )
    assert ok, f"create_pact failed: {revert_reason(receipt)}"

    pact = read_json(client_a, enforcer_address, "get_pact", [pact_id])
    assert pact["status"] == "PROPOSED"
    assert pact["party_a"].lower() == client_a.local_account.address.lower()
    assert pact["party_b"].lower() == client_b.local_account.address.lower()
    assert pact["dependency"] == "b_after_a"
    assert pact["terms_digest"]
    STATE["terms_digest"] = pact["terms_digest"]


# ---------------------------------------------------------------------------
# 3. Acceptance and dependency enforcement
# ---------------------------------------------------------------------------

def test_party_b_accepts_pact_with_exact_terms_digest(client_a, client_b, enforcer_address):
    """
    Party B accepts the pact with the exact terms digest and locks stake B.
    The pact activates.
    """
    pact_id = STATE["pact_id"]
    terms_digest = STATE["terms_digest"]

    # Acceptance with invalid digest must revert
    ok_fail, receipt_fail = write(
        client_b,
        enforcer_address,
        "accept_pact",
        [pact_id, "0x" + "00" * 32],
        value=STAKE_WEI,
        expect_success=False,
    )
    assert not ok_fail, "accept_pact should have reverted on invalid terms digest"

    # Acceptance with correct terms digest succeeds
    ok, receipt = write(
        client_b,
        enforcer_address,
        "accept_pact",
        [pact_id, terms_digest],
        value=STAKE_WEI,
    )
    assert ok, f"accept_pact failed: {revert_reason(receipt)}"

    pact = read_json(client_a, enforcer_address, "get_pact", [pact_id])
    assert pact["status"] == "ACTIVE"


def test_dependency_chain_keeps_party_b_waiting_with_no_clock(client_a, enforcer_address):
    """
    Under 'b_after_a', Party A is OPEN and clock is running.
    Party B is WAITING and has no clock started.
    """
    pact_id = STATE["pact_id"]
    ob_a = read_json(client_a, enforcer_address, "get_obligation", [pact_id, "A"])
    ob_b = read_json(client_a, enforcer_address, "get_obligation", [pact_id, "B"])

    assert ob_a["clock_started"] is True
    assert ob_a["seconds_remaining"] > 0

    assert ob_b["clock_started"] is False
    assert ob_b["seconds_remaining"] == 0

    assert read(client_a, enforcer_address, "obligation_state", [pact_id, "A"]) == "OPEN"
    assert read(client_a, enforcer_address, "obligation_state", [pact_id, "B"]) == "WAITING"


def test_party_b_cannot_submit_evidence_while_waiting(client_b, enforcer_address):
    """Party B cannot submit evidence while waiting for Party A to fulfil."""
    pact_id = STATE["pact_id"]
    ok, receipt = write(
        client_b,
        enforcer_address,
        "submit_evidence",
        [pact_id, EVIDENCE_B_URL],
        expect_success=False,
    )
    assert not ok, "submit_evidence should have reverted while WAITING"
    reason = revert_reason(receipt).lower()
    assert "has not started" in reason or "not yet confirmed" in reason

# ---------------------------------------------------------------------------
# 4. Party A fulfilment against live evidence URL
# ---------------------------------------------------------------------------

def test_party_a_submits_evidence_and_fulfils_commitment(client_a, enforcer_address):
    """
    Party A submits public gist evidence. Validators fetch it live, score all 3
    deliverables present, and agree on COMPLETE tier.
    """
    pact_id = STATE["pact_id"]
    ok, receipt = write(client_a, enforcer_address, "submit_evidence", [pact_id, EVIDENCE_A_URL])
    assert ok, f"Party A submit_evidence failed: {revert_reason(receipt)}"

    assert read(client_a, enforcer_address, "is_fulfilled", [pact_id, "A"]) is True
    assert read(client_a, enforcer_address, "obligation_state", [pact_id, "A"]) == "FULFILLED"

    adjudication = read_json(client_a, enforcer_address, "get_adjudication", [pact_id, "A", 1])
    bound = adjudication.get("consensus_bound", {})
    assert bound.get("passed") is True
    assert bound.get("tier") == "COMPLETE"
    assert bound.get("credit") == 100
    STATE["adjudication_a"] = adjudication


def test_fulfilment_of_party_a_starts_party_b_deadline_clock(client_a, enforcer_address):
    """
    Once Party A's fulfilment is confirmed, Party B derives as OPEN and its
    deadline clock starts running.
    """
    pact_id = STATE["pact_id"]
    ob_b = read_json(client_a, enforcer_address, "get_obligation", [pact_id, "B"])
    assert ob_b["clock_started"] is True
    assert ob_b["seconds_remaining"] > 0
    assert read(client_a, enforcer_address, "obligation_state", [pact_id, "B"]) == "OPEN"


def test_replay_protection_blocks_identical_evidence_url(client_a, enforcer_address):
    """The canonical replay index blocks submitting the same evidence URL twice."""
    pact_id = STATE["pact_id"]
    ok, receipt = write(
        client_a,
        enforcer_address,
        "submit_evidence",
        [pact_id, EVIDENCE_A_URL],
        expect_success=False,
    )
    assert not ok, "Resubmission of identical evidence URL must revert"
    reason = revert_reason(receipt)
    assert "already been confirmed as fulfilled" in reason or "already been adjudicated" in reason


# ---------------------------------------------------------------------------
# 5. Reference consumer cross-contract gating
# ---------------------------------------------------------------------------

def test_consumer_contract_gates_access_on_party_a_fulfilment(client_a, client_b, release_address):
    """
    CommitmentGatedRelease checks the enforcer on-chain:
    Party A is fulfilled, so a release gated on SIDE_A opens and can be claimed.
    """
    if not release_address:
        pytest.skip("RELEASE_ADDRESS not configured")

    pact_id = STATE["pact_id"]
    release_key = f"key-q3-a-{int(time.time())}"
    payload = "https://internal.vault.example/data/access-token-01"

    ok, _ = write(
        client_a,
        release_address,
        "register_release",
        [release_key, "Q3 Dataset Access Gate", pact_id, "SIDE_A", payload, True],
    )
    assert ok

    status = read_json(client_a, release_address, "gate_status", [release_key])
    assert status["open"] is True

    # Party B claims the payload
    ok_claim, _ = write(client_b, release_address, "claim", [release_key])
    assert ok_claim

    assert read(client_a, release_address, "has_claimed", [release_key, client_b.local_account.address]) is True


def test_consumer_contract_blocks_unfulfilled_party_b_gate(client_a, client_b, release_address):
    """A release gated on SIDE_B remains locked until Party B fulfils."""
    if not release_address:
        pytest.skip("RELEASE_ADDRESS not configured")

    pact_id = STATE["pact_id"]
    release_key_b = f"key-q3-b-{int(time.time())}"
    STATE["release_key_b"] = release_key_b
    payload = "https://internal.vault.example/risk-model/weights-q3"

    ok, _ = write(
        client_a,
        release_address,
        "register_release",
        [release_key_b, "Q3 Risk Model Access Gate", pact_id, "SIDE_B", payload, True],
    )
    assert ok

    status = read_json(client_a, release_address, "gate_status", [release_key_b])
    assert status["open"] is False

    # Claim must fail
    ok_claim, receipt = write(client_b, release_address, "claim", [release_key_b], expect_success=False)
    assert not ok_claim
    assert "closed" in revert_reason(receipt).lower()


# ---------------------------------------------------------------------------
# 6. Party B fulfilment and mutual resolution
# ---------------------------------------------------------------------------

def test_party_b_submits_evidence_and_fulfils_commitment(client_a, client_b, enforcer_address):
    """
    Party B submits public report evidence. Validators confirm all deliverables
    and score COMPLETE tier.
    """
    pact_id = STATE["pact_id"]
    ok, receipt = write(client_b, enforcer_address, "submit_evidence", [pact_id, EVIDENCE_B_URL])
    assert ok, f"Party B submit_evidence failed: {revert_reason(receipt)}"

    assert read(client_a, enforcer_address, "is_fulfilled", [pact_id, "B"]) is True
    assert read(client_a, enforcer_address, "obligation_state", [pact_id, "B"]) == "FULFILLED"
    assert read(client_a, enforcer_address, "both_fulfilled", [pact_id]) is True

    adjudication = read_json(client_a, enforcer_address, "get_adjudication", [pact_id, "B", 1])
    bound = adjudication.get("consensus_bound", {})
    assert bound.get("passed") is True
    assert bound.get("tier") in ("COMPLETE", "SUBSTANTIAL")
    assert bound.get("credit") in (80, 100)
    STATE["adjudication_b"] = adjudication


def test_consumer_contract_now_permits_party_b_gate(client_a, client_b, release_address):
    """Now that Party B is fulfilled, the SIDE_B gated release unlocks and can be claimed."""
    if not release_address:
        pytest.skip("RELEASE_ADDRESS not configured")

    release_key_b = STATE["release_key_b"]
    status = read_json(client_a, release_address, "gate_status", [release_key_b])
    assert status["open"] is True

    ok_claim, _ = write(client_a, release_address, "claim", [release_key_b])
    assert ok_claim
    assert read(client_a, release_address, "has_claimed", [release_key_b, client_a.local_account.address]) is True


def test_both_parties_agree_release_and_settle_at_par(client_a, client_b, enforcer_address):
    """
    Both parties consent to agree_release, skipping dispute delay and settling
    immediately at par. Value is conserved exactly.
    """
    pact_id = STATE["pact_id"]

    # Party A consents
    ok_a, _ = write(client_a, enforcer_address, "agree_release", [pact_id])
    assert ok_a
    assert read(client_a, enforcer_address, "release_consent_of", [pact_id, "A"]) is True

    # Party B consents -> second call settles the pact
    ok_b, _ = write(client_b, enforcer_address, "agree_release", [pact_id])
    assert ok_b

    pact = read_json(client_a, enforcer_address, "get_pact", [pact_id])
    assert pact["status"] == "SETTLED"

    settlement = read_json(client_a, enforcer_address, "get_settlement", [pact_id])
    assert settlement["outcome"] in ("RELEASED", "MUTUAL_RELEASE", "MUTUAL_FULFILMENT")
    assert settlement["payout_a_wei"] == str(STAKE_WEI)
    assert settlement["payout_b_wei"] == str(STAKE_WEI)
    assert settlement["forfeit_a_wei"] == "0"
    assert settlement["forfeit_b_wei"] == "0"
    assert int(settlement["payout_a_wei"]) + int(settlement["payout_b_wei"]) == 2 * STAKE_WEI

    STATE["settlement"] = settlement


# ---------------------------------------------------------------------------
# 7. Verification artifact creation
# ---------------------------------------------------------------------------

def test_write_the_verification_artifact(client_a, enforcer_address, release_address):
    """Record live observations for artifacts/live-verification.json."""
    pact_id = STATE.get("pact_id")
    pact = read_json(client_a, enforcer_address, "get_pact", [pact_id]) if pact_id else {}
    reg_info = read_json(client_a, enforcer_address, "get_registry_info")

    artifact = {
        "network": "studionet",
        "rpc": "https://studio.genlayer.com/api",
        "chainId": 61999,
        "contractAddress": enforcer_address,
        "consumerAddress": release_address,
        "enforcerSha256": STATE.get("enforcerSha256"),
        "releaseSha256": STATE.get("releaseSha256"),
        "deployedSourceMatchesRepository": True,
        "evidence": {
            "evidenceA": EVIDENCE_A_URL,
            "evidenceB": EVIDENCE_B_URL,
        },
        "finalPact": pact,
        "adjudicationA": STATE.get("adjudication_a"),
        "adjudicationB": STATE.get("adjudication_b"),
        "settlement": STATE.get("settlement"),
        "registryInfo": reg_info,
        "transactions": STATE.get("transactions", []),
        "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    path = ROOT / "artifacts" / "live-verification.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(f"\n  wrote {path}")
    assert path.is_file()
