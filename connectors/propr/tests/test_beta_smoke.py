import json
import sys

from connectors.propr.examples import beta_readonly_smoke


def test_missing_key_reports_blocked_without_exposing_account(monkeypatch, capsys):
    monkeypatch.delenv("PROPR_BETA_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["beta_readonly_smoke", "--account-id", "sensitive-account"])
    assert beta_readonly_smoke.main() == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == "BLOCKED"
    assert "sensitive-account" not in output
    assert "PROPR_BETA_API_KEY" in result["reason"]
