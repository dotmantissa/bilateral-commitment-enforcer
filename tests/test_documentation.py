"""
Tests that keep the documentation honest.

Two failure modes matter here: documented code that does not run wastes an
integrator's time, and documented addresses or hashes that drift from the artifacts
make deployment claims unverifiable. Both are checked mechanically rather than by
review, ensuring documentation never rots.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from conftest import ENFORCER_SOURCE, RELEASE_SOURCE, serve_admissibility

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
INTEGRATION = ROOT / "INTEGRATION.md"
DEPLOYMENT = ROOT / "artifacts" / "deployment.json"

ONE = 10**18


# ---------------------------------------------------------------------------
# The documented integration actually runs against the in-process host
# ---------------------------------------------------------------------------

@pytest.fixture
def documented(host):
    """The setup from the INTEGRATION.md testing section, run verbatim."""
    serve_admissibility(host)
    steward = host.new_address()
    alice = host.new_address()
    bob = host.new_address()
    host.warp(1_800_000_000)

    enforcer = host.deploy(
        ENFORCER_SOURCE,
        "BilateralCommitmentEnforcer",
        args=["Bilateral Commitment Registry"],
        sender=steward,
    )
    release = host.deploy(
        RELEASE_SOURCE,
        "CommitmentGatedRelease",
        args=[enforcer.address],
        sender=alice,
    )
    return host, enforcer, release, steward, alice, bob


def test_the_documented_consumer_reports_its_bound_enforcer(documented):
    host, enforcer, release, steward, alice, bob = documented
    info = json.loads(release.get_info())
    assert info["enforcer"] == enforcer.address
    assert info["owner"] == alice
    assert info["gate_modes"] == ["SIDE_A", "SIDE_B", "MUTUAL"]


def test_the_documented_cross_contract_gate_blocks_before_fulfilment(documented):
    host, enforcer, release, steward, alice, bob = documented
    pact_id = "acme-dataset-pact"

    options = {
        "pass_threshold": 80,
        "max_attempts": 3,
        "challenge_window": 3600,
        "funding_window": 86400,
        "tier_tolerance_points": 0,
        "checklist_a": [
            "An anonymised Q3 dataset published at a public URL",
            "A schema document describing all fourteen columns",
            "A SHA-256 checksum for the dataset file",
        ],
        "checklist_b": [
            "A written integration report published at a public URL",
            "The resulting default curve from the risk model",
            "A list of every row rejected during loading",
        ],
    }

    # Alice proposes
    res = json.loads(
        enforcer.create_pact(
            pact_id,
            "Acme Dataset Integration",
            bob,
            "Publish the anonymised Q3 dataset as a CSV at a public URL with schema and SHA-256.",
            "Publish a written risk model integration report at a public URL with default curve and rejected rows.",
            str(ONE),
            "b_after_a",
            3600,
            3600,
            json.dumps(options),
            sender=alice,
            value=ONE,
        )
    )
    assert res["status"] == "PROPOSED"

    # Alice registers a gated release on CommitmentGatedRelease
    reg = json.loads(
        release.register_release(
            "dataset-key",
            "Acme Q3 Dataset Token",
            pact_id,
            "SIDE_A",
            "https://vault.acme.example/tokens/q3-secret",
            True,
            sender=alice,
        )
    )
    assert reg["gate"] == "SIDE_A"

    # Gate is closed
    status = json.loads(release.gate_status("dataset-key"))
    assert status["open"] is False
    assert "not confirmed as fulfilled" in status["reason"]

    # Claim reverts
    with pytest.raises(Exception):
        release.claim("dataset-key", sender=bob)


def test_the_documented_registry_info_keys_exist(documented):
    host, enforcer, release, steward, alice, bob = documented
    info = json.loads(enforcer.get_registry_info())
    for key in (
        "registry_name", "steward", "created_at", "pact_count",
        "pacts_settled", "total_locked_wei", "total_settled_wei",
        "defaults", "tiers", "dependency_modes",
    ):
        assert key in info, f"{key} is documented but not returned"
    assert info["defaults"]["pass_threshold"] == 80
    assert info["tiers"]["COMPLETE"] == 100
    assert info["tiers"]["NONE"] == 0
    assert info["dependency_modes"] == ["none", "b_after_a", "a_after_b"]


# ---------------------------------------------------------------------------
# The documented facts match the artifacts
# ---------------------------------------------------------------------------

def test_the_documented_addresses_match_the_deployment_artifact():
    deployment = json.loads(DEPLOYMENT.read_text())
    enforcer = deployment["contracts"]["BilateralCommitmentEnforcer"]
    release = deployment["contracts"]["CommitmentGatedRelease"]
    readme = README.read_text()

    assert enforcer["contractAddress"] in readme
    assert enforcer["deploymentTransaction"] in readme
    assert enforcer["sourceSha256"] in readme
    assert enforcer["explorer"] in readme
    assert deployment["deployerAddress"] in readme
    assert deployment["runner"] in readme
    assert release["contractAddress"] in readme


def test_the_documented_source_hash_matches_the_source_on_disk():
    import hashlib

    deployment = json.loads(DEPLOYMENT.read_text())
    for name, path in (
        ("BilateralCommitmentEnforcer", ENFORCER_SOURCE),
        ("CommitmentGatedRelease", RELEASE_SOURCE),
    ):
        entry = deployment["contracts"][name]
        raw = path.read_bytes()
        assert entry["sourceSha256"] == hashlib.sha256(raw).hexdigest(), f"{name} hash is stale"
        assert entry["sourceBytes"] == len(raw), f"{name} byte count is stale"


def test_the_pinned_runner_matches_the_contract_headers():
    deployment = json.loads(DEPLOYMENT.read_text())
    runner = deployment["runner"]
    for path in (ENFORCER_SOURCE, RELEASE_SOURCE):
        head = path.read_text().splitlines()[:2]
        assert any(runner in line for line in head), f"{path.name} does not pin {runner}"
        assert any('"Depends"' in line for line in head)


def test_every_documented_contract_method_exists():
    enforcer_src = ENFORCER_SOURCE.read_text()
    release_src = RELEASE_SOURCE.read_text()
    defined = set(re.findall(r"^\s{4}def (\w+)", enforcer_src, re.M))
    defined |= set(re.findall(r"^\s{4}def (\w+)", release_src, re.M))

    documented = set()
    for path in (README, INTEGRATION):
        text = path.read_text()
        documented |= set(re.findall(r"`(\w+)\(", text))
        documented |= set(re.findall(r"^\| `(\w+)\(", text, re.M))

    external = {
        "print", "json", "len", "str", "int", "bool", "dict", "list", "bytes",
        "Address", "isinstance", "open", "raises", "range", "sorted", "sum",
        "create_account", "create_client", "read_contract", "write_contract",
        "wait_for_transaction_receipt", "deploy_contract", "get_contract_at",
        "get_contract_schema", "loads", "dumps", "view", "emit", "bootstrap",
        "mock_web", "mock_llm", "clear_routes", "new_address", "warp", "deploy",
        "UserError", "run_nondet_unsafe", "gen_getContractCode", "leader_receipt",
        "succeeded", "revert_reason", "b64decode", "sha256", "hexdigest",
        "method", "decode", "get", "strip", "lower", "format", "date",
        "__init__", "BilateralCommitmentEnforcer", "CommitmentGatedRelease",
    }
    unknown = sorted(name for name in documented - defined - external if not name.startswith("_"))
    assert not unknown, f"documented but not defined on any contract: {unknown}"


@pytest.mark.parametrize("path", [README, INTEGRATION], ids=["README", "INTEGRATION"])
def test_the_docs_contain_no_em_dashes(path):
    text = path.read_text()
    offenders = [
        (i, line.strip()[:90])
        for i, line in enumerate(text.splitlines(), start=1)
        if "—" in line or "–" in line
    ]
    assert not offenders, f"{path.name} contains dash characters: {offenders}"


@pytest.mark.parametrize("path", [README, INTEGRATION], ids=["README", "INTEGRATION"])
def test_the_docs_have_balanced_code_fences(path):
    fences = re.findall(r"^```", path.read_text(), re.M)
    assert len(fences) % 2 == 0, f"{path.name} has an unclosed code fence"
