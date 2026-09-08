from datetime import datetime, timezone

from backend.app.services.intelligence import (
    classify_event,
    parse_bls_ics,
    parse_gdelt,
    source_tier,
    upcoming_stage,
)


def test_bls_ppi_calendar_event_is_high_impact_and_timezone_aware():
    payload = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:ppi-2026-09
DTSTART;TZID=America/New_York:20260910T083000
SUMMARY:Producer Price Index
DESCRIPTION:Producer Price Index for August 2026
URL:https://www.bls.gov/schedule/news_release/ppi.htm
END:VEVENT
END:VCALENDAR
"""
    events = parse_bls_ics(payload)
    assert len(events) == 1
    event = events[0]
    assert event.event_kind == "macro_calendar"
    assert event.status == "scheduled"
    assert event.source_tier == 1
    assert event.importance >= 9
    assert event.event_time == datetime(2026, 9, 10, 12, 30, tzinfo=timezone.utc)
    assert "BTCUSDT" in event.affected_assets
    assert "XAUUSDT" in event.affected_assets


def test_geopolitical_oil_event_maps_to_cross_market_assets():
    importance, assets, themes = classify_event(
        "Iran escalation threatens Strait of Hormuz as crude oil rises"
    )
    assert importance >= 8
    assert "geopolitics" in themes
    assert "energy" in themes
    assert {"BTCUSDT", "ETHUSDT", "XAUUSDT"}.issubset(set(assets))


def test_hunter_biden_laptop_event_is_detected_as_crypto_catalyst():
    importance, _, themes = classify_event(
        "Hunter Biden to launch LAPTOP memecoin on Base"
    )
    assert importance >= 7.5
    assert "political_memecoin" in themes
    assert "catalyst" in themes


def test_gdelt_keeps_unknown_source_low_confidence():
    events = parse_gdelt(
        {
            "articles": [
                {
                    "title": "Oil jumps after Iran escalation",
                    "url": "https://example-news.invalid/story",
                    "seendate": "20260908T120000Z",
                    "domain": "example-news.invalid",
                }
            ]
        },
        topic="geopolitics",
    )
    assert len(events) == 1
    assert events[0].source_tier == 4
    assert events[0].confidence == "low"
    assert "news_discovery" in events[0].themes


def test_source_hierarchy():
    assert source_tier("https://www.bls.gov/example") == 1
    assert source_tier("https://www.reuters.com/example") == 2
    assert source_tier("https://www.coindesk.com/example") == 3
    assert source_tier("https://unknown.example/example") == 4


def test_upcoming_stages():
    now = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    assert upcoming_stage(datetime(2026, 9, 9, 7, 0, tzinfo=timezone.utc), now) == "24h"
    assert upcoming_stage(datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc), now) == "2h"
    assert upcoming_stage(datetime(2026, 9, 8, 8, 10, tzinfo=timezone.utc), now) == "15m"
