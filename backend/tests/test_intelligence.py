from datetime import datetime, timezone

from backend.app.services.intelligence import (
    annotate_corroboration,
    classify_event,
    format_event_message,
    parse_bls_ics,
    parse_gdelt,
    parse_fomc_calendar_html,
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


def test_fomc_calendar_parser_extracts_future_meetings():
    html = """
    <html><body>
      <h3>2026 FOMC Meetings</h3>
      <div>September</div><div>15-16*</div>
      <div>October</div><div>27-28</div>
      <div>December</div><div>8-9*</div>
      <h3>2027 FOMC Meetings</h3>
      <div>January</div><div>26-27</div>
    </body></html>
    """
    events = parse_fomc_calendar_html(html, year=2026)
    assert len(events) == 3
    assert events[0].importance == 10.0
    assert events[0].event_time == datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
    assert events[0].metadata["summary_of_economic_projections"] is True


def test_cross_source_corroboration_counts_independent_domains():
    from backend.app.services.intelligence import NormalizedEvent

    base = dict(
        event_kind="news",
        status="reported",
        summary="",
        source_tier=3,
        confidence="medium",
        importance=8.0,
    )
    events = [
        NormalizedEvent(
            canonical_key="a",
            title="Iran escalation pushes crude oil sharply higher",
            source_name="Source A",
            source_url="https://a.example/story",
            **base,
        ),
        NormalizedEvent(
            canonical_key="b",
            title="Crude oil sharply higher after Iran escalation",
            source_name="Source B",
            source_url="https://b.example/story",
            **base,
        ),
    ]
    annotate_corroboration(events)
    assert events[0].metadata["corroboration_count"] == 2
    assert events[1].metadata["corroboration_count"] == 2


def test_project_update_is_thesis_change_candidate():
    importance, assets, themes = classify_event(
        "Chainlink announces major CCIP integration and protocol update"
    )
    assert importance >= 7.0
    assert "LINKUSDT" in assets
    assert "project_update" in themes
    assert "thesis_change_candidate" in themes


def test_event_message_includes_market_context():
    from backend.app.db import IntelligenceEvent
    from backend.app.config import settings

    previous = settings.telegram_language
    settings.telegram_language = "bilingual"
    try:
        event = IntelligenceEvent(
            canonical_key="test:event",
            event_kind="news",
            status="reported",
            title="Chainlink announces major CCIP integration",
            summary="A meaningful protocol ecosystem update.",
            source_name="Official source",
            source_url="https://example.com",
            source_tier=1,
            confidence="high",
            importance=8.0,
            affected_assets_json='["LINKUSDT"]',
            themes_json='["crypto","project_update","thesis_change_candidate"]',
            metadata_json="{}",
        )
        text = format_event_message(
            event,
            market_context=[
                {
                    "symbol": "LINKUSDT",
                    "price": 13.245,
                    "confidence": "high",
                    "source_count": 3,
                }
            ],
        )
    finally:
        settings.telegram_language = previous

    assert "LINKUSDT: 13.245" in text
    assert "事件发生时相关加密资产价格" in text
    assert "Related crypto prices at alert time" in text
    assert "3 个数据源" in text
