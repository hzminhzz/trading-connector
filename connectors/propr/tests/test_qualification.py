from connectors.propr.qualification import beta_order_qualification

def test_no_account_is_blocked():
    assert beta_order_qualification().status == "BLOCKED"

def test_fake_approval_cannot_bypass_beta_proof():
    report = beta_order_qualification(account_id="fake", scoped_approval="yes")
    assert report.status == "BLOCKED"
    assert report.remote_mutations == 0
    assert report.approved_instruments == ()
