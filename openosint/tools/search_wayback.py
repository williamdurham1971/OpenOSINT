# openosint/tools/search_wayback.py
"""
Wayback Machine (Internet Archive) intelligence module.

Queries the public Wayback CDX API and Availability API for a domain or URL:
first and latest capture, the years in which captures exist, hostnames the
archive has seen under the domain (historical subdomains), and a sample of
archived URLs with notable paths flagged (robots.txt, admin panels, backups,
config files, ...). No external API key or credentials required.

Endpoints
---------
CDX:          https://web.archive.org/cdx/search/cdx  (output=json)
Availability: https://archive.org/wayback/available

Every finding is labelled by where it came from; a lookup that failed is
reported as "undetermined", never as "absent".
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import urlsplit

import requests

from openosint.proxy import get_requests_proxies
from openosint.tools.exceptions import OSINTError, ToolExecutionError

logger = logging.getLogger(__name__)

_CDX_URL = "https://web.archive.org/cdx/search/cdx"
_AVAILABILITY_URL = "https://archive.org/wayback/available"
_ARCHIVE_WEB_URL = "https://web.archive.org/web/{timestamp}/{original}"

_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_URLS = 25
# Rows requested from the CDX API for host/path discovery. Bounded so that a
# huge site (millions of captures) cannot turn one call into a runaway fetch.
_URL_SCAN_LIMIT = 500
_USER_AGENT = "OpenOSINT (+https://github.com/OpenOSINT/OpenOSINT)"

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z0-9\-]{2,63}$"
)

# Paths worth a second look in an archive: crawl hints, admin surfaces,
# leaked build/config artefacts, and API roots. Matched against the URL path.
_NOTABLE_PATH_RE = re.compile(
    r"(?i)(?:"
    r"/robots\.txt$|/sitemap[^/]*\.xml$|/\.well-known/|"
    r"/\.git(?:/|$)|/\.svn(?:/|$)|/\.env(?:\.|$)|/\.htaccess$|/\.ds_store$|"
    r"/wp-admin|/wp-login\.php|/wp-config|/phpinfo\.php|/phpmyadmin|"
    r"/admin(?:/|$|\.)|/login(?:/|$|\.)|/dashboard(?:/|$)|/cgi-bin/|"
    r"/backup|/dump|/config(?:\.|/|$)|/api(?:/|$)|/swagger|/graphql|"
    r"\.(?:sql|bak|old|zip|tar\.gz|tgz|7z|rar|log)$"
    r")"
)


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


def _normalize_target(target: str) -> tuple[str, str, str] | None:
    """Return (query, host, mode) for *target*, or None if it is not usable.

    mode is "host" when the target is a bare hostname (the whole domain,
    including subdomains, is scanned) or "url" when it carries a path (only
    that URL prefix is scanned). The scheme is dropped: the CDX API keys
    captures by host+path and treats http/https as the same resource.
    """
    raw = target.strip()
    if not raw:
        return None
    raw = _SCHEME_RE.sub("", raw)
    raw = raw.split("#", 1)[0].split("?", 1)[0]
    host, _, path = raw.partition("/")
    host = host.strip().lower().rstrip(".")
    if "@" in host or ":" in host:
        # Credentials or ports have no meaning to the archive index.
        host = host.rsplit("@", 1)[-1].split(":", 1)[0]
    if not _HOST_RE.match(host):
        return None
    path = path.strip()
    if path:
        return f"{host}/{path}", host, "url"
    return host, host, "host"


def _format_timestamp(ts: str) -> str:
    """Render a 14-digit CDX timestamp (YYYYMMDDhhmmss) as YYYY-MM-DD."""
    if len(ts) >= 8 and ts[:8].isdigit():
        return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
    return ts or "unknown"


# ---------------------------------------------------------------------------
# HTTP helpers (synchronous — run in a worker thread by the async entrypoint)
# ---------------------------------------------------------------------------


def _get(url: str, params: dict[str, str], timeout: int) -> requests.Response:
    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
            proxies=get_requests_proxies(),
        )
    except requests.Timeout as exc:
        raise OSINTError(f"Wayback Machine request timed out after {timeout}s.") from exc
    except requests.RequestException as exc:
        raise OSINTError(f"Network error querying the Wayback Machine: {exc}") from exc

    if response.status_code == 429:
        raise OSINTError("Wayback Machine rate limit exceeded. Wait a minute and retry.")
    if response.status_code in (502, 503, 504):
        raise ToolExecutionError(
            f"Wayback Machine is temporarily unavailable (HTTP {response.status_code})."
        )
    if response.status_code != 200:
        raise ToolExecutionError(f"Wayback Machine returned HTTP {response.status_code}.")
    return response


def _cdx(params: dict[str, str], timeout: int) -> list[list[str]]:
    """Run one CDX query and return its data rows (header row stripped).

    The CDX API answers a query with no matches with an empty body rather
    than an empty JSON array, so both shapes are treated as "no rows".
    """
    response = _get(_CDX_URL, {**params, "output": "json"}, timeout)
    body = response.text.strip()
    if not body:
        return []
    try:
        rows = json.loads(body)
    except ValueError as exc:
        raise ToolExecutionError("Wayback CDX API returned a non-JSON response.") from exc
    if not isinstance(rows, list) or not rows:
        return []
    return [r for r in rows[1:] if isinstance(r, list)]


def _availability(query: str, timeout: int) -> dict | None:
    """Return the snapshot closest to now for *query*, or None if unavailable."""
    response = _get(_AVAILABILITY_URL, {"url": query}, timeout)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ToolExecutionError("Wayback Availability API returned a non-JSON response.") from exc
    closest = (payload.get("archived_snapshots") or {}).get("closest") or {}
    if not closest.get("available"):
        return None
    return closest


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


class _Findings:
    __slots__ = (
        "first",
        "latest",
        "years",
        "hosts",
        "urls",
        "scanned",
        "failed",
    )

    def __init__(self) -> None:
        self.first: list[str] | None = None  # [timestamp, original, statuscode]
        self.latest: dict | None = None
        self.years: list[str] = []
        self.hosts: list[str] = []
        self.urls: list[tuple[str, str, str]] = []  # (original, statuscode, mimetype)
        self.scanned: int = 0
        self.failed: dict[str, str] = {}


def _collect(query: str, host: str, mode: str, timeout: int) -> _Findings:
    f = _Findings()

    # 1. First capture of the target itself. This one is allowed to raise:
    #    if the index cannot be reached at all there is nothing to report.
    rows = _cdx(
        {
            "url": query,
            "matchType": "prefix",
            "fl": "timestamp,original,statuscode",
            "limit": "1",
        },
        timeout,
    )
    if not rows:
        return f
    f.first = rows[0]

    # 2. Years with at least one capture (one row per distinct YYYY).
    try:
        year_rows = _cdx(
            {
                "url": query,
                "matchType": "prefix",
                "fl": "timestamp",
                "collapse": "timestamp:4",
            },
            timeout,
        )
        f.years = sorted({r[0][:4] for r in year_rows if r and r[0][:4].isdigit()})
    except OSINTError as exc:
        f.failed["years"] = str(exc)

    # 3. Latest capture, via the Availability API (closest snapshot to now).
    try:
        f.latest = _availability(query, timeout)
    except OSINTError as exc:
        f.failed["latest"] = str(exc)

    # 4. Host + URL discovery. In host mode the whole domain (all subdomains)
    #    is scanned; in URL mode only that URL prefix.
    try:
        scan_rows = _cdx(
            {
                "url": host if mode == "host" else query,
                "matchType": "domain" if mode == "host" else "prefix",
                "fl": "original,statuscode,mimetype",
                "collapse": "urlkey",
                "limit": str(_URL_SCAN_LIMIT),
            },
            timeout,
        )
    except OSINTError as exc:
        f.failed["urls"] = str(exc)
        return f

    hosts: set[str] = set()
    seen: set[str] = set()
    for row in scan_rows:
        original = row[0] if row else ""
        if not original:
            continue
        status = row[1] if len(row) > 1 else ""
        mime = row[2] if len(row) > 2 else ""
        parsed = urlsplit(original if _SCHEME_RE.match(original) else f"http://{original}")
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if hostname:
            hosts.add(hostname)
        if original not in seen:
            seen.add(original)
            f.urls.append((original, status, mime))
    f.scanned = len(scan_rows)
    f.hosts = sorted(hosts)
    return f


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _path_of(original: str) -> str:
    parsed = urlsplit(original if _SCHEME_RE.match(original) else f"http://{original}")
    return parsed.path or "/"


def _build_output(query: str, mode: str, f: _Findings, max_urls: int) -> str:
    if f.first is None:
        return f"No captures found for '{query}' in the Wayback Machine."

    lines: list[str] = [f"[Wayback] Target: {query}"]

    ts, original, status = (f.first + ["", "", ""])[:3]
    lines.append(
        f"[Wayback] First capture: {_format_timestamp(ts)}"
        f"{f' (HTTP {status})' if status else ''} "
        f"{_ARCHIVE_WEB_URL.format(timestamp=ts, original=original)}"
    )

    if f.latest:
        lts = str(f.latest.get("timestamp", ""))
        lstatus = f.latest.get("status", "")
        lurl = f.latest.get("url", "")
        lines.append(
            f"[Wayback] Latest capture: {_format_timestamp(lts)}"
            f"{f' (HTTP {lstatus})' if lstatus else ''} {lurl}".rstrip()
        )
    elif "latest" in f.failed:
        lines.append("[?] Latest capture undetermined — the Availability API lookup failed.")

    if f.years:
        span = f"{f.years[0]}–{f.years[-1]}" if len(f.years) > 1 else f.years[0]
        lines.append(
            f"[Wayback] Years with captures ({len(f.years)}, {span}): {', '.join(f.years)}"
        )
    elif "years" in f.failed:
        lines.append("[?] Capture years undetermined — the CDX year lookup failed.")

    if "urls" in f.failed:
        lines.append(f"[?] Host/URL scan undetermined — {f.failed['urls']}")
        return "\n".join(lines)

    if mode == "host":
        lines.append(f"[Wayback] Historical hosts ({len(f.hosts)}):")
        for h in f.hosts:
            lines.append(f"  • {h}")

    notable = [u for u in f.urls if _NOTABLE_PATH_RE.search(_path_of(u[0]))]
    if notable:
        lines.append(f"[!] Notable archived paths ({len(notable)}):")
        for original, status, mime in notable[:max_urls]:
            meta = ", ".join(x for x in (status, mime) if x)
            lines.append(f"  • {original}" + (f"  [{meta}]" if meta else ""))

    if f.urls:
        suffix = "+" if f.scanned >= _URL_SCAN_LIMIT else ""
        shown = f.urls[:max_urls]
        lines.append(
            f"[Wayback] Archived URLs (showing {len(shown)} of {len(f.urls)}{suffix} unique):"
        )
        for original, status, mime in shown:
            meta = ", ".join(x for x in (status, mime) if x)
            lines.append(f"  • {original}" + (f"  [{meta}]" if meta else ""))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


async def run_wayback_osint(
    target: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
    max_urls: int = _DEFAULT_MAX_URLS,
) -> str:
    """
    Look a domain or URL up in the Wayback Machine.

    Parameters
    ----------
    target:
        Domain (``example.com``) or URL (``https://example.com/path``). With a
        bare domain every hostname under it is scanned; with a URL only that
        prefix is.
    timeout_seconds:
        Per-request HTTP timeout. Up to four requests are made.
    max_urls:
        Maximum archived URLs to list in the output.

    Returns
    -------
    str
        Formatted result string or a descriptive error message. Never raises.
    """
    normalized = _normalize_target(target)
    if normalized is None:
        return "Error: target must be a domain name or URL (e.g. example.com or https://example.com/path)."
    query, host, mode = normalized
    max_urls = max(1, int(max_urls))

    logger.info("Starting Wayback lookup for: %s", query)
    try:
        findings = await asyncio.to_thread(_collect, query, host, mode, timeout_seconds)
        result = _build_output(query, mode, findings, max_urls)
        logger.info("Wayback lookup complete for: %s", query)
        return result
    except OSINTError as exc:
        logger.warning("Wayback lookup failed: %s", exc)
        return f"Scan error: {exc}"
    except Exception as exc:
        logger.exception("Unexpected error during Wayback lookup.")
        return f"Internal error: {exc}"
