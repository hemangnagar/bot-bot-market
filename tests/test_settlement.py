from settlement import SimLedger, PaymentHeader, unb64, b64


def test_sim_ledger_roundtrip(tmp_path):
    L = SimLedger(tmp_path / "l.sqlite", secret="s")
    L.fund("a", 1.0)
    req = L.quote("p", 0.02, resource="/invoke/p")
    hdr = L.sign("a", req)
    wire = unb64(b64(hdr.to_wire()))
    assert wire["x402Version"] == 2 and wire["accepted"]["payTo"] == "p"
    res = L.verify_and_settle(PaymentHeader.from_wire(wire), fee_to_broker=0.0)
    assert res.success and res.tx_id
    assert L.balance("a") == 0.98 and L.balance("p") == 0.02
    assert L.verify_and_settle(hdr).error.startswith("nonce already settled")
    rows = L.ledger_rows()
    assert rows[0]["from"] == "a" and rows[0]["to"] == "p" and rows[0]["amount"] == 0.02


def test_bad_signature_and_funds(tmp_path):
    L = SimLedger(tmp_path / "l.sqlite", secret="s")
    req = L.quote("p", 0.5)
    hdr = L.sign("poor", req)
    assert L.verify(hdr).error == "insufficient funds"
    hdr.payload["amount"] = "1"
    assert L.verify(hdr).error == "payload does not match requirement"
    L.fund("poor", 1.0)
    hdr2 = L.sign("poor", req)
    hdr2.payload["signature"] = "0" * 64
    assert L.verify(hdr2).error == "bad signature"
