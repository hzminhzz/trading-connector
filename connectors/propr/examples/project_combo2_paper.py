"""Locally project only independently replay-verified Combo 2 paper outcomes.

No credentials or network client is imported. Actual Propr account monitoring
uses separate read-only account operations and never affects paper equity.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from connectors.propr.combo2_paper_projection import Combo2PaperProjection


def verified_projection(paper_root: Path, run_id: str, sqlite_path: Path) -> dict:
    from copytrade_pm.pm_combo2 import Combo2PaperTracker

    manifest_path = paper_root / run_id / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("MISSING_OR_UNSAFE_PAPER_RUN_MANIFEST")
    manifest = json.loads(manifest_path.read_text())
    policy = manifest["policy"]
    if (
        manifest["run_id"] != run_id
        or policy.get("paper_only") is not True
        or policy.get("live_authorization") is not False
        or policy.get("account_alias") != "#JhW9"
    ):
        raise ValueError("WRONG_OR_LIVE_PAPER_RUN")
    prospective = manifest.get("ingress_mode") == "verified_prospective_source_v1"
    tracker = Combo2PaperTracker(
        paper_root,
        run_id=run_id,
        started_at=manifest["started_at"],
        source_subject_hashes=manifest["source_subject_hashes"],
        prospective=prospective,
        source_evidence_hashes=(
            manifest["source_scope_evidence_shas"] if prospective else None
        ),
        market_contract_sha=(manifest["market_contract_sha"] if prospective else None),
    )
    # Constructor validates the immutable manifest, every event SHA and every
    # post-state. The replay below independently verifies each paper action.
    verified = tracker.verified_paper_decisions()
    projection = Combo2PaperProjection(sqlite_path)
    try:
        inserted = 0
        for item in verified:
            inserted += projection.project(
                run_id=item["run_id"],
                event_id=item["event_id"],
                paper_event_sha=item["paper_event_sha"],
                account_alias=item["account_alias"],
                source_report=item["report"],
            )
        return {
            **projection.summary(),
            "new_paper_action_rows": inserted,
            "verified_source_paper_decisions": len(verified),
            "paper_head_sha": tracker.head,
            "real_propr_account_queried": False,
            "remote_order_transport": "ABSENT",
        }
    finally:
        projection.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--paper-root", type=Path, required=True)
    p.add_argument("--run-id", type=str, required=True)
    p.add_argument("--sqlite", type=Path, required=True)
    args = p.parse_args()
    print(
        json.dumps(
            verified_projection(args.paper_root, args.run_id, args.sqlite),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
