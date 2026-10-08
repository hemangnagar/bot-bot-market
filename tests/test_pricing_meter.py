from runner.meter import parse_sse_usage
from runner.pricing import Pricing, Usage


def test_pricing_matches_docs_table():
    p = Pricing()
    assert p.cost_usd("claude-sonnet-5-5", Usage(1_000_000, 0)) == 2.0
    assert p.cost_usd("claude-haiku-5-5", Usage(0, 1_000_000)) == 0.5
    assert abs(p.cost_usd("claude-haiku-5-5", Usage(200_000, 0)) - 0.10) < 1e-9  # over-100K tier
    assert p.cost_usd("claude-sonnet-5-5", Usage(cache_read=1_000_000)) == 0.10
    assert p.resolve("claude-sonnet-5-5-20260101") == "claude-sonnet-5-5"


def test_sse_usage_merge():
    events = [
        {"type": "message_start", "message": {"model": "claude-haiku-5-5", "usage": {"input_tokens": 5, "output_tokens": 1,
                                                                               "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 0},
                                                                               "cache_read_input_tokens": 50}}},
        {"type": "content_block_delta"},
        {"type": "message_delta", "usage": {"output_tokens": 42, "input_tokens": 5, "cache_read_input_tokens": 50}},
    ]
    model, u = parse_sse_usage(events)
    assert model == "claude-haiku-5-5" and u == Usage(5, 42, 100, 0, 50)
