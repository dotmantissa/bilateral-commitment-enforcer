# Bilateral Commitment Enforcer

A primitive for mutual, interdependent obligations settled under validator consensus, built as a GenLayer intelligent contract.

Commercial agreements rarely run in one direction. Most real arrangements are bilateral and contingent: "I publish the dataset once you wire the escrow." "I deliver the audit once you freeze the release branch." "I publish the review once you supply the benchmark data." A conventional smart contract can lock funds against a condition for one side. It cannot coordinate both sides at once when obligations are expressed in natural language, evidence lives on the public web, and each party's performance deadline depends on the other delivering first.

The Bilateral Commitment Enforcer solves this mutual coordination problem. Two counterparties stake value into an escrowed pact. GenLayer validators adjudicate each party's natural language commitments against public evidence URLs, score deliverables against a consent-backed discrete rubric, and settle funds according to an exact value-conserving partial credit algebra.

A core differentiator of this primitive is **dependency chaining**: when party B's performance depends on party A delivering first, party B's deadline clock does not even start until party A's fulfilment is confirmed by consensus. If party A breaches, party B is automatically excused and receives party A's forfeit without ever risking its own stake.

---

## Live deployment

| Field | Value |
| --- | --- |
| Network | GenLayer StudioNet |
| Chain ID | 61999 |
| RPC endpoint | `https://studio.genlayer.com/api` |
| Contract address | `0xb7f9489E5C0b3F4f2004806a7E0646F5f9A2f231` |
| Explorer | https://explorer-studio.genlayer.com/address/0xb7f9489E5C0b3F4f2004806a7E0646F5f9A2f231 |
| Deployment transaction | `0xc01c37d7b573cb5a706389ef7166fd5c9b4003e5d53550cff6d15eb64fe71ce7` |
| Deployer | `0xBC1399c55538eC034d4Da550C03c34Ae0C357f53` |
| GenVM runner | `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` |
| Source SHA-256 | `5da1cf7d0ada0050d5a89b9cddf967d7883aab63f862b0c2f3c41db30641c4dc` |

The deployed bytecode matches `contracts/bilateral_commitment_enforcer.py` byte-for-byte. The live test suite asserts this exact match via `gen_getContractCode` on every run. Verify locally:

```bash
curl -sS https://studio.genlayer.com/api \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"gen_getContractCode",
       "params":["0xb7f9489E5C0b3F4f2004806a7E0646F5f9A2f231"]}' \
  | python3 -c 'import base64,json,sys; sys.stdout.buffer.write(base64.b64decode(json.load(sys.stdin)["result"]))' \
  | diff - contracts/bilateral_commitment_enforcer.py && echo identical
```

The reference consumer used by the live suite is deployed at
[`0xcb9Fb88A019F22cbd11108681310C8c5e2bE2E35`](https://explorer-studio.genlayer.com/address/0xcb9Fb88A019F22cbd11108681310C8c5e2bE2E35) (transaction `0x608c364607889330e078ffa3a5086f7d298d732bde58f04a9104438a01e6ac1a`, SHA-256 `405bb1314ed7189da1e653e2b90f7c3eebac5c4c8605b796a82fc1036bf6b411`).

Full deployment metadata is stored in `artifacts/deployment.json`. The live verification transcript containing all 14 transactions and consensus vote breakdowns is stored in `artifacts/live-verification.json`.

---

## Core Architecture

### 1. Settlement Algebra

Settlement preserves strict value conservation across the entire outcome space:

```
payout_a + payout_b + sink == stake_a + stake_b
```

Per side $i$ (with counterparty $j$):

1. **Effective credit determination**:
   - `effective_credit = 100` if side is `FULFILLED` or `EXCUSED`.
   - `effective_credit = confirmed_partial_credit` (0 to 100) otherwise.

2. **Forfeit calculation**:
   ```
   forfeit_i = stake_i * (100 - effective_credit_i) / 100
   ```

3. **Payout calculation**:
   ```
   payout_i = stake_i - forfeit_i + forfeit_j
   ```

When both sides breach and an optional `mutual_breach_sink` address is declared, forfeits are directed to the sink rather than netting out between breaching parties. If no sink is configured, forfeits cross directly.

This single unified formulation cleanly handles:
- **Mutual fulfilment**: both score 100, zero forfeits, full stakes returned.
- **Unilateral breach**: breaching party forfeits proportionally; performing party receives the forfeit.
- **Chained breach**: party A breaches; party B is `EXCUSED` (effective credit 100) and receives party A's forfeit.
- **Partial performance**: a failing `PARTIAL` tier retains 50% of its stake and forfeits 50%.

### 2. Tiered Partial Credit Taxonomy

Prose evaluations produce noisy point scores (e.g. 73 versus 68) that can fragment validator consensus. The contract quantizes partial performance into five canonical semantic tiers:

| Tier | Canonical Credit | Semantic Definition |
| --- | --- | --- |
| `COMPLETE` | 100 | All rubric deliverables fully satisfied |
| `SUBSTANTIAL` | 80 | Primary deliverables present; minor omissions |
| `PARTIAL` | 50 | Half or significant core deliverables satisfied |
| `MINIMAL` | 20 | Incidental or preliminary deliverables only |
| `NONE` | 0 | No deliverables present (unreadable evidence reverts) |

Validators agree on discrete tiers rather than arbitrary floating point numbers, eliminating leader discretion over monetary payouts.

### 3. Stored Rubrics and Mutual Consent

To guarantee deterministic tier assignment, adjudication scores against a discrete checklist stored on-chain at pact creation.

Each checklist item receives point marks from validators:
- `PRESENT`: 2 points
- `PARTIAL`: 1 point
- `ABSENT`: 0 points

The ratio of earned points to maximum points deterministically maps to the credit tier:
- Every point earned -> `COMPLETE` (100 credit)
- Ratio >= 0.80 -> `SUBSTANTIAL` (80 credit)
- Ratio >= 0.40 -> `PARTIAL` (50 credit)
- Any positive points below 0.40 -> `MINIMAL` (20 credit)
- Zero points -> `NONE` (0 credit)

#### Consent Mechanism

To ensure neither party is judged by an unreviewed rubric:
1. `create_pact` assesses commitment adjudicability, stores discrete checklists on-chain, and leaves the pact `PROPOSED`.
2. A cryptographic hash (`terms_digest`) covers all terms, stakes, windows, commitments, and checklist text.
3. `accept_pact` requires party B to supply the exact `terms_digest`. Party B cannot be bound to terms it has not inspected.
4. `withdraw_proposal` allows party A to withdraw its stake at any time while the pact remains `PROPOSED`.

### 4. Dependency Chaining

Pacts declare one of three dependency modes:
- `none`: Both obligations open concurrently when the pact activates.
- `b_after_a`: Party A is independent. Party B is dependent on party A.
- `a_after_b`: Party B is independent. Party A is dependent on party B.

Under `b_after_a`:
- Upon acceptance, party A is `OPEN` with its deadline clock running.
- Party B enters `WAITING`. No deadline is set and no clock runs.
- Any attempt by party B to call `submit_evidence` while `WAITING` reverts.
- When party A is confirmed `FULFILLED` by consensus, party B transitions to `OPEN` and its deadline clock activates in the same transaction.
- If party A reaches deadline without fulfilling (`BREACHED`), party B derives as `EXCUSED`. Party B forfeits 0 and receives party A's forfeit.

### 5. Consensus Architecture

Three non-deterministic operations execute via GenLayer Optimistic Democracy (`gl.vm.run_nondet_unsafe`):

1. **Admissibility (`create_pact`)**:
   - Leader prompts LLM to verify whether commitments name concrete, verifiable deliverables.
   - Validators re-evaluate and agree on strict fields: `adjudicable` (boolean), `defect` classification, and checklist item count.

2. **Evidence Adjudication (`submit_evidence`)**:
   - Leader fetches the evidence URL and scores each stored checklist item.
   - Validators execute their own independent HTTP fetch, score the rubric, and compute the resulting tier.
   - Consensus requires the **exact same credit tier**, and therefore the same canonical credit and binary pass/fail. Different tiers are always rejected, including adjacent failing tiers one point apart. Both `submit_evidence` and `challenge_fulfilment` use this rule.
   - `tier_tolerance_points` is a deprecated compatibility field, defaulting to zero. Values 0 to 4 remain accepted, stored, and included in the terms digest, but have no effect on consensus. Neither a creator nor a steward can enable cross-tier acceptance.
   - Point totals and item verdicts may differ within the agreed tier. `get_adjudication` labels the leader's total as `panel_earned_points`; it is advisory and never drives settlement. `consensus_bound` contains the exact agreed tier, derived credit and pass/fail, fixed total possible points, and challenge decision.
   - For example, 3/6 points yields `PARTIAL` (50 credit), while 2/6 yields `MINIMAL` (20 credit). On a failing 10 GEN stake, these forfeit 5 GEN and 8 GEN respectively. These readings cannot approve each other. Two readings in `PARTIAL`, such as 3/6 and 4/6, agree on 50 credit and the same forfeiture.

3. **Challenge Adjudication (`challenge_fulfilment`)**:
   - Counterparty challenges confirmed fulfilment within `challenge_window`.
   - Validators evaluate original evidence alongside challenge grounds.
   - Consensus bounds `substantiated` (boolean) and revised tier.

Unbound free-form prose (notes, findings, explanations) is rendered deterministically by `_render_finding` from on-chain state, ensuring identical byte emission across validators.

### 6. Replay Protection

Evidence URLs are canonically normalized (lowercased scheme and host, stripped default ports and tracking parameters) and hashed. Resubmission of an already-evaluated URL for the same side in a pact is rejected deterministically on-chain, preventing repeated re-rolls of the validator panel.

---

## Contract Method Summary

### BilateralCommitmentEnforcer (30 methods)

#### Write Methods (9)
- `create_pact(...)`: Propose a bilateral pact and lock party A's stake. Payable.
- `accept_pact(pact_id, terms_digest)`: Assent to exact terms, lock party B's stake, and activate pact. Payable.
- `withdraw_proposal(pact_id)`: Cancel proposed pact and reclaim party A's stake before party B accepts.
- `submit_evidence(pact_id, evidence_url)`: Submit public evidence URL for validator adjudication.
- `challenge_fulfilment(pact_id, side, grounds)`: Dispute confirmed fulfilment during the challenge window.
- `agree_release(pact_id)`: Mutual consent to cancel pact and return stakes at par immediately.
- `resolve_pact(pact_id)`: Settle terminal pact and transfer stakes according to settlement algebra.
- `set_defaults(defaults_json)`: Update registry defaults for future pacts (steward only).
- `transfer_stewardship(new_steward)`: Reassign registry steward address.

#### View Methods (21)
- `get_registry_info()`: Registry metadata, steward, default parameters, and aggregate statistics.
- `pact_exists(pact_id)`: Boolean existence check.
- `get_pact_count()`: Total pacts registered in registry.
- `get_pact_id_at(index)`: Enumerate registered pact IDs.
- `party_pact_count_of(party)`: Number of pacts involving a specific address.
- `party_pact_id_at(party, index)`: Lookup pact ID by participant and index.
- `terms_digest(pact_id)`: Canonical cryptographic hash of pact terms and rubrics.
- `get_pact(pact_id)`: Complete pact details, stakes, timestamps, and live derived states.
- `get_obligation(pact_id, side)`: Individual obligation details, rubric, countdown, and attempt history.
- `obligation_state(pact_id, side)`: Live derived state string (`PROPOSED`, `WAITING`, `OPEN`, `FULFILLED`, `BREACHED`, `EXCUSED`).
- `is_fulfilled(pact_id, side)`: Boolean check if side is confirmed fulfilled.
- `both_fulfilled(pact_id)`: Boolean check if both sides are confirmed fulfilled.
- `require_mutual_fulfilment(pact_id)`: Reverting check asserting both sides are fulfilled.
- `is_resolvable(pact_id)`: Check whether pact is ready for settlement and identify blocking conditions.
- `settlement_preview(pact_id)`: Dry-run projection of settlement outcomes and payout amounts.
- `get_settlement(pact_id)`: Immutable record of finalized settlement payouts and forfeit arithmetic.
- `get_adjudication(pact_id, side, attempt)`: Historical adjudication report for an attempt.
- `list_adjudications(pact_id, side)`: Array of all adjudication records for a side.
- `evidence_already_used(pact_id, side, evidence_url)`: Replay index check for an evidence URL.
- `release_consent_of(pact_id, side)`: Check whether a party has consented to mutual release.
- `get_rubric(pact_id, side)`: Stored discrete checklist items for a side.

---

## Reference Consumer: CommitmentGatedRelease

`contracts/commitment_gated_release.py` is an intelligent contract proving cross-contract integration. It gates digital assets (access tokens, API credentials, decryption payloads) on confirmed fulfilment of obligations within the enforcer:

- `register_release(release_key, description, pact_id, gate_mode, payload, open_to_all)`: Register a gated release.
- `gate_status(release_key)`: View whether gate is open by querying enforcer state synchronously.
- `claim(release_key)`: Claim the gated payload once conditions are satisfied on-chain.
- `allow_recipient(release_key, recipient)`: Restrict release to specific claimant addresses.
- `revoke_release(release_key)`: Revoke a release and prevent further claims.

---

## Verification and Testing

### Offline Test Suite (579 tests)

The offline suite tests lifecycle transitions, consensus closures, dependency chains, settlement arithmetic, replay prevention, and cross-contract gating without external network calls:

```bash
python3 -m pytest tests/ -v
```

### Documentation Verification Suite

Ensures code snippets, hashes, deployed addresses, and style rules remain synchronized:

```bash
python3 -m pytest tests/test_documentation.py -v
```

### Live StudioNet End-to-End Suite (16 tests)

Executes against live validators on GenLayer StudioNet with real evidence documents:

```bash
GENLAYER_PRIVATE_KEY=0x... ENFORCER_ADDRESS=0xb7f9489E5C0b3F4f2004806a7E0646F5f9A2f231 \
  python3 -m pytest tests/test_live_studionet.py -v -s
```
