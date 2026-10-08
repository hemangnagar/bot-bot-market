# Settlement

`Settlement` (base.py) is the protocol: `quote`, `verify`, `verify_and_settle`, `balance`.

## SimLedger (default)
SQLite wallets and a ledger table with `from`, `to`, `amount`, `fee_to_broker`, `tx_id`, `nonce`.
Payments are HMAC-signed with a shared secret (`BENCH_SIM_SECRET`) in place of an EVM signature.
Deterministic, no network, no cost.

## Wire format vs x402 v2
Header names and envelope shapes follow x402 protocol v2 as shipped in the `x402` 2.25.0 Python
package (`PAYMENT-REQUIRED`, `PAYMENT-SIGNATURE`, `PAYMENT-RESPONSE`; `{"x402Version": 2, "accepts": [...]}`;
`{"x402Version", "scheme", "network", "accepted", "payload"}`). Deviations:

- network is `sim:ledger` (CAIP-2 syntax, not a real chain) and asset is `USD`, 6 decimal places;
- the `payload` is `{from, to, amount, nonce, memo, signature}` with an HMAC signature, not an EIP-3009
  authorization;
- the brief called the payment header `PAYMENT`; the SDK's v2 name `PAYMENT-SIGNATURE` is used.
- the `x402` package itself is not imported in phase 1: its EVM scheme needs web3 dependencies and keys.
  Phase 3 (`X402Testnet`) will use `x402ResourceServer` + `HTTPFacilitatorClient` on `eip155:84532`.
