# tests/test_wayback.py
"""Tests for search_wayback — Wayback Machine (Internet Archive) integration.

All HTTP is mocked at ``requests.get``; the suite never touches archive.org.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import requests

from openosint.correlation import EntityType, make_entity
from openosint.extractors import EXTRACTOR_REGISTRY
from openosint.tools.search_wayback import (
    _AVAILABILITY_URL,
    _CDX_URL,
    _normalize_target,
    run_wayback_osint,
)

_PATCH_GET = "openosint.tools.search_wayback.requests.get"


def _resp(status: int, body: str) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = body
    r.json.side_effect = lambda: json.loads(body)
    return r


def _cdx_json(header: list[str], rows: list[list[str]]) -> str:
    return json.dumps([header, *rows]) if rows else ""


def _fake_get(*, first=None, years=None, latest=None, scan=None, errors=None):
    """Build a requests.get double that routes on the CDX query shape.

    ``errors`` maps a step name to an exception or a Response to return
    instead of the happy-path body.
    """
    errors = errors or {}

    def _get(url, params=None, headers=None, timeout=None, proxies=None):
        params = params or {}
        if url == _AVAILABILITY_URL:
            step = "latest"
            ok = json.dumps(latest if latest is not None else {"archived_snapshots": {}})
        elif params.get("limit") == "1":
            step = "first"
            ok = _cdx_json(["timestamp", "original", "statuscode"], first or [])
        elif params.get("collapse") == "timestamp:4":
            step = "years"
            ok = _cdx_json(["timestamp"], years or [])
        else:
            step = "scan"
            ok = _cdx_json(["original", "statuscode", "mimetype"], scan or [])
        if step in errors:
            err = errors[step]
            if isinstance(err, Exception):
                raise err
            return err
        return _resp(200, ok)

    return _get


_FIRST = [["20020115000000", "http://example.com/", "200"]]
_YEARS = [["20020115000000"], ["20101010101010"], ["20260830120000"]]
_LATEST = {
    "url": "example.com",
    "archived_snapshots": {
        "closest": {
            "available": True,
            "status": "200",
            "timestamp": "20260830120000",
            "url": "http://web.archive.org/web/20260830120000/https://example.com/",
        }
    },
}
_SCAN = [
    ["http://example.com/", "200", "text/html"],
    ["http://www.example.com/", "200", "text/html"],
    ["https://old.example.com/index.html", "200", "text/html"],
    ["http://example.com/robots.txt", "200", "text/plain"],
    ["http://example.com/about", "301", "text/html"],
]


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------


def test_normalize_bare_domain_is_host_mode() -> None:
    assert _normalize_target("Example.COM") == ("example.com", "example.com", "host")


def test_normalize_url_strips_scheme_query_and_keeps_path() -> None:
    assert _normalize_target("https://Example.com/a/b?x=1#frag") == (
        "example.com/a/b",
        "example.com",
        "url",
    )


def test_normalize_drops_port_and_trailing_slash() -> None:
    assert _normalize_target("http://example.com:8080/") == ("example.com", "example.com", "host")


def test_normalize_rejects_garbage() -> None:
    assert _normalize_target("") is None
    assert _normalize_target("not a domain") is None
    assert _normalize_target("localhost") is None


async def test_invalid_target_returns_error_without_network() -> None:
    with patch(_PATCH_GET) as mock_get:
        result = await run_wayback_osint("???")
    assert result.startswith("Error:")
    mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_domain_lookup_reports_all_sections() -> None:
    with patch(
        _PATCH_GET, side_effect=_fake_get(first=_FIRST, years=_YEARS, latest=_LATEST, scan=_SCAN)
    ):
        result = await run_wayback_osint("example.com")

    assert "[Wayback] Target: example.com" in result
    assert (
        "First capture: 2002-01-15 (HTTP 200) https://web.archive.org/web/20020115000000/http://example.com/"
        in result
    )
    assert "Latest capture: 2026-08-30 (HTTP 200)" in result
    assert "Years with captures (3, 2002–2026): 2002, 2010, 2026" in result
    assert "[Wayback] Historical hosts (3):" in result
    for host in ("example.com", "www.example.com", "old.example.com"):
        assert f"  • {host}" in result
    assert "[!] Notable archived paths (1):" in result
    assert "http://example.com/robots.txt  [200, text/plain]" in result
    assert "Archived URLs (showing 5 of 5 unique)" in result


async def test_domain_mode_scans_whole_domain_with_matchtype_domain() -> None:
    calls: list[dict] = []

    def _spy(url, params=None, **kw):
        calls.append({"endpoint": url, **(params or {})})
        return _fake_get(first=_FIRST, years=_YEARS, latest=_LATEST, scan=_SCAN)(
            url, params=params, **kw
        )

    with patch(_PATCH_GET, side_effect=_spy):
        await run_wayback_osint("example.com")

    scan = next(c for c in calls if c.get("collapse") == "urlkey")
    assert scan["matchType"] == "domain"
    assert scan["url"] == "example.com"
    assert all(c["endpoint"] in (_CDX_URL, _AVAILABILITY_URL) for c in calls)
    assert all(c.get("output") == "json" for c in calls if c["endpoint"] == _CDX_URL)


async def test_url_mode_scans_prefix_only_and_omits_host_list() -> None:
    calls: list[dict] = []

    def _spy(url, params=None, **kw):
        calls.append({"endpoint": url, **(params or {})})
        return _fake_get(first=_FIRST, years=_YEARS, latest=_LATEST, scan=_SCAN[:1])(
            url, params=params, **kw
        )

    with patch(_PATCH_GET, side_effect=_spy):
        result = await run_wayback_osint("https://example.com/blog/")

    scan = next(c for c in calls if c.get("collapse") == "urlkey")
    assert scan["matchType"] == "prefix"
    assert scan["url"] == "example.com/blog/"
    assert "Historical hosts" not in result


async def test_max_urls_caps_listed_urls() -> None:
    with patch(
        _PATCH_GET, side_effect=_fake_get(first=_FIRST, years=_YEARS, latest=_LATEST, scan=_SCAN)
    ):
        result = await run_wayback_osint("example.com", max_urls=2)
    assert "Archived URLs (showing 2 of 5 unique)" in result


async def test_no_captures_message() -> None:
    with patch(_PATCH_GET, side_effect=_fake_get(first=[])) as mock_get:
        result = await run_wayback_osint("never-archived.example")
    assert result == "No captures found for 'never-archived.example' in the Wayback Machine."
    # Nothing else is fetched once the index says there is nothing.
    assert mock_get.call_count == 1


async def test_latest_unavailable_is_omitted_not_invented() -> None:
    with patch(
        _PATCH_GET, side_effect=_fake_get(first=_FIRST, years=_YEARS, latest=None, scan=_SCAN)
    ):
        result = await run_wayback_osint("example.com")
    assert "Latest capture" not in result
    assert "First capture" in result


# ---------------------------------------------------------------------------
# Error paths — failed lookups are reported as undetermined, never as absent
# ---------------------------------------------------------------------------


async def test_network_error_on_first_query_is_scan_error() -> None:
    with patch(_PATCH_GET, side_effect=requests.ConnectionError("boom")):
        result = await run_wayback_osint("example.com")
    assert result.startswith("Scan error: Network error querying the Wayback Machine")


async def test_timeout_is_reported() -> None:
    with patch(_PATCH_GET, side_effect=requests.Timeout()):
        result = await run_wayback_osint("example.com", timeout_seconds=7)
    assert result == "Scan error: Wayback Machine request timed out after 7s."


async def test_rate_limit_and_5xx_are_reported() -> None:
    with patch(_PATCH_GET, return_value=_resp(429, "")):
        assert "rate limit" in (await run_wayback_osint("example.com"))
    with patch(_PATCH_GET, return_value=_resp(503, "")):
        assert "temporarily unavailable (HTTP 503)" in (await run_wayback_osint("example.com"))


async def test_non_json_cdx_body_is_reported() -> None:
    with patch(_PATCH_GET, return_value=_resp(200, "<html>maintenance</html>")):
        result = await run_wayback_osint("example.com")
    assert result == "Scan error: Wayback CDX API returned a non-JSON response."


async def test_partial_failures_are_flagged_undetermined() -> None:
    fake = _fake_get(
        first=_FIRST,
        errors={
            "years": requests.ConnectionError("years down"),
            "latest": _resp(500, ""),
            "scan": requests.Timeout(),
        },
    )
    with patch(_PATCH_GET, side_effect=fake):
        result = await run_wayback_osint("example.com")

    assert "First capture: 2002-01-15" in result
    assert "[?] Capture years undetermined" in result
    assert "[?] Latest capture undetermined" in result
    assert "[?] Host/URL scan undetermined" in result
    assert "Historical hosts" not in result
    assert "No captures" not in result


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------


async def test_extractor_yields_historical_hosts_only() -> None:
    with patch(
        _PATCH_GET, side_effect=_fake_get(first=_FIRST, years=_YEARS, latest=_LATEST, scan=_SCAN)
    ):
        output = await run_wayback_osint("example.com")

    seed = make_entity(EntityType.DOMAIN, "example.com", 1.0)
    entities, relationships = EXTRACTOR_REGISTRY["search_wayback"](output, seed)

    values = sorted(e.value for e in entities)
    assert values == ["old.example.com", "www.example.com"]  # seed itself excluded
    assert all(e.type == EntityType.DOMAIN for e in entities)
    assert {r.kind for r in relationships} == {"archived_host"}
    # archived URL lines must never leak into entities
    assert not any("robots" in e.value for e in entities)


def test_extractor_is_defensive_on_junk() -> None:
    seed = make_entity(EntityType.DOMAIN, "example.com", 1.0)
    assert EXTRACTOR_REGISTRY["search_wayback"]("", seed) == ([], [])
    assert EXTRACTOR_REGISTRY["search_wayback"]("Scan error: nope", seed) == ([], [])
