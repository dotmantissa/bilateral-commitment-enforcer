# Integrating the Bilateral Commitment Enforcer

This guide explains how to integrate the Bilateral Commitment Enforcer into your protocol, front end, or workflow.

The deployed primitive on StudioNet is
[`0xb7f9489E5C0b3F4f2004806a7E0646F5f9A2f231`](https://explorer-studio.genlayer.com/address/0xb7f9489E5C0b3F4f2004806a7E0646F5f9A2f231).
It is multi-tenant and permissionless: any two addresses can create and settle pacts without protocol deployment or admin approval.

---

## 1. Cross-Contract Integration (GenLayer Smart Contracts)

Downstream contracts frequently need to gate real-world actions on whether a counterparty has fulfilled a commitment:
- Releasing an escrowed payload (API key, IPFS CID, decryption secret).
- Unpausing a downstream DeFi vault once an audit report is confirmed.
- Triggering secondary contract calls when both parties complete reciprocal tasks.

### Synchronous Gate Verification

Because GenLayer view calls are synchronous, your contract can read the enforcer's live state during execution without off-chain keepers or asynchronous callbacks:

```python
from genlayer import *

class MyConsumer(gl.Contract):
    enforcer: Address

    def __init__(self, enforcer_address: str):
        self.enforcer = Address(str(enforcer_address).strip())

    @gl.public.write
    def execute_action(self, pact_id: str) -> None:
        target = gl.get_contract_at(self.enforcer)

        # Gate check: Party A must be confirmed as fulfilled
        if not bool(target.view().is_fulfilled(pact_id, "A")):
            raise gl.vm.UserError("[EXPECTED] Party A has not fulfilled its commitment")

        # Proceed with gated business logic
        self._deliver_payload(pact_id)
```

### Checking Mutual Fulfilment

If your action requires both parties to have delivered their commitments:

```python
# Boolean variant (non-reverting)
if not bool(target.view().both_fulfilled(pact_id)):
    raise gl.vm.UserError("[EXPECTED] Mutual obligations are not yet fulfilled")

# Reverting variant with detailed reason
target.view().require_mutual_fulfilment(pact_id)
```

### Reference Consumer Implementation

`contracts/commitment_gated_release.py` is a production-shaped reference consumer deployed alongside the enforcer. It demonstrates:
- Binding an enforcer address at deployment.
- Associating release keys with specific pacts and gate modes (`SIDE_A`, `SIDE_B`, or `MUTUAL`).
- Synchronously asserting gate status before releasing secret payloads.
- Tracking claimant authorization and preventing double claims.

---

## 2. Front End and Backend Integration (Python SDK)

Interact with the deployed enforcer on StudioNet using `genlayer_py`:

```python
import json
from genlayer_py import create_account, create_client
from genlayer_py.chains import studionet

account = create_account("0xYOUR_PRIVATE_KEY")
client = create_client(chain=studionet, account=account)
enforcer_address = "0xb7f9489E5C0b3F4f2004806a7E0646F5f9A2f231"
```

### Step 1: Propose a Bilateral Pact (Party A)

Party A creates the pact, declares party B and obligations, and sends its stake:

```python
options = {
    "pass_threshold": 80,
    "max_attempts": 3,
    "challenge_window": 86400,
    "funding_window": 604800,
    "tier_tolerance_points": 0,
    "checklist_a": [
        "Dataset release document containing row count and covered period",
        "A column schema document describing fourteen columns",
        "A SHA-256 checksum for the dataset file",
    ],
    "checklist_b": [
        "Loading confirmation for the Q3 dataset into the risk model",
        "The resulting default curve by vintage month",
        "A complete listing of rejected rows with surrogate IDs and reasons",
    ],
}

tx_hash = client.write_contract(
    address=enforcer_address,
    function_name="create_pact",
    args=[
        "meridian-pact-001",
        "Meridian Q3 Dataset & Model Integration",
        "0xPARTY_B_ADDRESS",
        "Publish the Q3 dataset documentation at a public URL with schema and checksum.",
        "Publish the model integration report at a public URL with curve and rejections.",
        "1000000000000000000",  # Party B stake in wei (1 GEN)
        "b_after_a",            # Dependency mode
        86400,                  # Party A window (seconds)
        86400,                  # Party B window (seconds)
        json.dumps(options),
    ],
    value=10**18,              # Party A stake sent with call (1 GEN)
)
receipt = client.wait_for_transaction_receipt(tx_hash)
```

Evidence submissions and challenges require consensus on the exact credit tier.
`tier_tolerance_points` is deprecated: legacy values 0 to 4 are still stored and
covered by `terms_digest`, but cannot relax tier agreement. A rejected result
commits no attempt, replay marker, challenge usage, or credit change. Validators
may disagree on point totals within one tier because settlement uses only that
tier's canonical credit. Read `consensus_bound` for the agreed decision and
`panel_earned_points` for the leader's advisory point total.

### Step 2: Read Terms Digest (Party B)

Party B queries the pact and reviews terms before assenting:

```python
pact = json.loads(client.read_contract(enforcer_address, "get_pact", ["meridian-pact-001"]))
digest = pact["terms_digest"]
print(f"Terms Digest to verify: {digest}")
```

### Step 3: Accept Pact (Party B)

Party B accepts by passing the exact `terms_digest` and attaching its stake:

```python
tx_hash = client.write_contract(
    address=enforcer_address,
    function_name="accept_pact",
    args=["meridian-pact-001", digest],
    value=10**18,  # Matching stake in wei
)
receipt = client.wait_for_transaction_receipt(tx_hash)
```

### Step 4: Submit Evidence URL

Once a party's obligation is `OPEN`, it submits a public evidence URL:

```python
tx_hash = client.write_contract(
    address=enforcer_address,
    function_name="submit_evidence",
    args=["meridian-pact-001", "https://gist.githubusercontent.com/.../dataset.md"],
)
receipt = client.wait_for_transaction_receipt(tx_hash)
```

### Step 5: Settle and Resolve

Once obligations reach terminal states:

```python
# Check whether pact is resolvable
status = json.loads(client.read_contract(enforcer_address, "is_resolvable", ["meridian-pact-001"]))

if status["resolvable"]:
    tx_hash = client.write_contract(
        address=enforcer_address,
        function_name="resolve_pact",
        args=["meridian-pact-001"],
    )
    client.wait_for_transaction_receipt(tx_hash)
```

Alternatively, both parties can invoke `agree_release` to immediately settle at par without waiting for dispute windows:

```python
client.write_contract(enforcer_address, "agree_release", ["meridian-pact-001"])
```

---

## 3. Testing Integrations Locally

Use the in-process GenVM host (`tests/genvm_host.py`) to test your consumer contracts offline with real SDK storage and consensus simulation:

```python
from pathlib import Path
from genvm_host import bootstrap

def test_custom_consumer():
    host = bootstrap()
    steward = host.new_address()
    alice = host.new_address()

    # Deploy enforcer
    enforcer = host.deploy(
        Path("contracts/bilateral_commitment_enforcer.py"),
        "BilateralCommitmentEnforcer",
        args=["Test Registry"],
        sender=steward,
    )

    # Deploy consumer pointing to enforcer
    consumer = host.deploy(
        Path("contracts/commitment_gated_release.py"),
        "CommitmentGatedRelease",
        args=[enforcer.address],
        sender=alice,
    )

    info = json.loads(consumer.get_info())
    assert info["enforcer"] == enforcer.address
```

---

## 4. Method API Reference

### Core Write Methods

| Method | Parameters | Payable | Description |
| --- | --- | --- | --- |
| `create_pact` | `pact_id: str, title: str, party_b: str, commitment_a: str, commitment_b: str, stake_b_wei: str, dependency: str, window_a_seconds: int, window_b_seconds: int, options_json: str` | Yes | Proposes a pact and deposits party A's stake. |
| `accept_pact` | `pact_id: str, terms_digest: str` | Yes | Accepts terms digest, deposits party B stake, and activates pact. |
| `withdraw_proposal` | `pact_id: str` | No | Reclaims party A stake if pact has not been accepted by party B. |
| `submit_evidence` | `pact_id: str, evidence_url: str` | No | Submits evidence URL for validator rubric adjudication. |
| `challenge_fulfilment` | `pact_id: str, side: str, grounds: str` | No | Challenges confirmed counterparty fulfilment during dispute window. |
| `agree_release` | `pact_id: str` | No | Registers consent for mutual release at par. Settles upon second party call. |
| `resolve_pact` | `pact_id: str` | No | Settles terminal obligations and distributes stakes. |

### Core View Methods

| Method | Parameters | Returns | Description |
| --- | --- | --- | --- |
| `get_registry_info` | None | `str` (JSON) | Registry wide configuration, defaults, and statistics. |
| `get_pact` | `pact_id: str` | `str` (JSON) | Full pact record with live derived obligation states. |
| `get_obligation` | `pact_id: str, side: str` | `str` (JSON) | Obligation details, rubric items, seconds remaining, and best tier. |
| `obligation_state` | `pact_id: str, side: str` | `str` | Current lifecycle state string (`PROPOSED`, `WAITING`, `OPEN`, `FULFILLED`, `BREACHED`, `EXCUSED`). |
| `is_fulfilled` | `pact_id: str, side: str` | `bool` | True if confirmed fulfilled. |
| `both_fulfilled` | `pact_id: str` | `bool` | True if both sides confirmed fulfilled. |
| `require_mutual_fulfilment` | `pact_id: str` | `bool` | Returns True or reverts with readable failure reason. |
| `terms_digest` | `pact_id: str` | `str` | Cryptographic hash binding pact terms and checklists. |
| `is_resolvable` | `pact_id: str` | `str` (JSON) | Resolution readiness and blocking explanation. |
| `settlement_preview` | `pact_id: str` | `str` (JSON) | Simulated settlement payouts and forfeits based on current state. |
| `get_settlement` | `pact_id: str` | `str` (JSON) | Immutable record of settled payout and forfeit distribution. |
| `get_adjudication` | `pact_id: str, side: str, attempt: int` | `str` (JSON) | Validator panel consensus marks and finding for an attempt. |
