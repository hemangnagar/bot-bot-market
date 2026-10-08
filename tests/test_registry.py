import pytest
from fastapi.testclient import TestClient

from registry.app import build_app, load_cards, match_score
from settlement import HDR_REQUIRED, HDR_SIGNATURE, SimLedger, PaymentRequirement, b64, unb64


def test_match_score_is_dumb_keyword_match():
    cards = load_cards()
    assert match_score(cards["edshield"], "scrub pii", None) == 1.0
    assert match_score(cards["edshield"], "translate french", None) == 0.0
    assert match_score(cards["stub_adjudicate_fast"], "", "claim_a") == 0.5


def test_402_handshake_without_live_provider(tmp_path):
    L = SimLedger(tmp_path / "l.sqlite", secret="s")
    L.fund("buyer", 1.0)
    cards = load_cards(["stub_scrub_cheap"])
    cards["stub_scrub_cheap"]["endpoint"] = "http://127.0.0.1:9"  # nothing listens here
    app = build_app(L, cards=cards)
    c = TestClient(app)
    r = c.post("/invoke/stub_scrub_cheap", json={"text": "x"})
    assert r.status_code == 402 and HDR_REQUIRED in r.headers
    body = unb64(r.headers[HDR_REQUIRED])
    assert body == r.json() and body["x402Version"] == 2
    req = PaymentRequirement.from_wire(body["accepts"][0])
    assert req.amount_usd == 0.01 and req.pay_to == "wallet:stub_scrub_cheap"
    hdr = L.sign("buyer", req)
    r = c.post("/invoke/stub_scrub_cheap", json={"text": "x"}, headers={HDR_SIGNATURE: b64(hdr.to_wire())})
    assert r.status_code == 502  # provider unreachable -> not settled
    assert L.balance("buyer") == 1.0
    assert c.get("/services", params={"q": "translate"}).json() == []
