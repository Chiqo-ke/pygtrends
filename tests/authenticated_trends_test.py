import json
import logging
from pathlib import Path

from trendspy.authenticated_trends import (
    decode_batchexecute_response,
    extract_from_captured_events,
    extract_related_query_tables,
    normalize_related_query_tables,
)


FIXTURE = Path(__file__).parent / "fixtures" / "related_queries_rpc.json"


def test_decodes_nested_rpc_and_separates_top_and_rising():
    decoded = decode_batchexecute_response(FIXTURE.read_text())
    tables = extract_related_query_tables(decoded)

    assert tables == {
        "top": [{"query": "top signal", "interest": 91, "change": "12%"}],
        "rising": [
            {"query": "rising signal", "interest": 11, "change": "200%"},
            {"query": "breakout signal", "interest": 3, "change": "Breakout"},
        ],
    }


def test_normalization_keeps_interest_separate_from_change():
    decoded = decode_batchexecute_response(FIXTURE.read_text())
    tables = normalize_related_query_tables(extract_related_query_tables(decoded))

    assert tables["rising"][0] == {
        "query": "rising signal",
        "interest": 11,
        "change": "200%",
    }
    assert tables["rising"][1]["change"] == "Breakout"


def test_captured_event_logs_safe_metadata_and_discovers_rpc_id(caplog):
    caplog.set_level(logging.INFO)
    body = FIXTURE.read_text()
    events = [{
        "body": body,
        "response": {
            "url": "https://trends.google.com/_/TrendsUi/data/batchexecute",
            "status": 200,
        },
    }]

    tables = extract_from_captured_events(events)

    assert len(tables["top"]) == 1
    assert "rpc.sanitized" in caplog.text
    assert "Search interest" not in caplog.text
    assert "200%" not in caplog.text
