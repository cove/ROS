#!/usr/bin/env python3
"""Check the published LA County Assessor map for Book 5841, Page 18.

The Assessor's scanned PDF is the source of truth for this watch. Its checksum
detects a candidate publication change; the map must then be visually checked
before anyone calls the boundary correction confirmed.
"""

import hashlib
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = "https://maps.assessor.lacounty.gov/GeoCortex/Essentials/PAIS/REST/sites/PAIS/Resources"
MAP_ID = "5841-018-"
WATCHED_PARCELS = {
    "438 E Poppyfields Dr": ("5841-018-006",),
    "446 E Poppyfields Dr": ("5841-018-004", "5841-018-005"),
    "454 E Poppyfields Dr": ("5841-018-003",),
}
NAVIGATOR_URL = f"{BASE}/ParcelMapNavigator?{urlencode({'f': 'json', 'MapId': MAP_ID})}"
PDF_URL = f"{BASE}/ParcelMap?{urlencode({'f': 'file', 'MapId': MAP_ID})}"
BASELINE_SHA256 = "e0c88d50bd8dba318b5eff3bacd812c783bd2ad574d0e9f52b58edfc9d07ce46"
PACIFIC = ZoneInfo("America/Los_Angeles")
MAX_BYTES = 15_000_000


class LookupFailure(Exception):
    pass


def county_get(url):
    request = Request(url, headers={"User-Agent": "la-county-assessor-map-watch/1.0", "Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=30) as response:
            if response.status != 200 or response.url.split("/")[2].lower() != "maps.assessor.lacounty.gov":
                raise LookupFailure(f"Unexpected County response or redirect: HTTP {response.status} at {response.url}")
            content_type = response.headers.get_content_type()
            body = response.read(MAX_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise LookupFailure(f"County request failed at {url}: {exc}") from exc
    if len(body) > MAX_BYTES:
        raise LookupFailure(f"County response exceeds {MAX_BYTES} bytes at {url}")
    return content_type, body


def check():
    nav_type, nav_body = county_get(NAVIGATOR_URL)
    try:
        navigator = json.loads(nav_body)
    except (ValueError, UnicodeError) as exc:
        raise LookupFailure(f"Invalid County map navigator JSON at {NAVIGATOR_URL}: {exc}") from exc
    if not isinstance(navigator, dict) or navigator.get("MapId") != MAP_ID:
        raise LookupFailure(f"County map navigator did not verify {MAP_ID}: {navigator!r}")
    if nav_type not in ("application/json", "text/plain"):
        raise LookupFailure(f"Unexpected County map navigator content type: {nav_type}")

    pdf_type, pdf = county_get(PDF_URL)
    if pdf_type != "application/pdf" or len(pdf) < 10_000 or not pdf.startswith(b"%PDF-") or b"%%EOF" not in pdf[-1024:]:
        raise LookupFailure(f"Missing or invalid Assessor PDF at {PDF_URL}: content type {pdf_type}, {len(pdf)} bytes")
    digest = hashlib.sha256(pdf).hexdigest()
    with tempfile.NamedTemporaryFile(mode="wb", prefix="assessor_5841_018_", suffix=".pdf", delete=False) as out:
        out.write(pdf)
        pdf_path = str(Path(out.name).resolve())

    return {
        "status": "verified_unchanged" if digest == BASELINE_SHA256 else "review_required",
        "checked_at": datetime.now(PACIFIC).isoformat(timespec="seconds"),
        "map_id": MAP_ID,
        "watched_parcels": WATCHED_PARCELS,
        "source_url": PDF_URL,
        "navigator_url": NAVIGATOR_URL,
        "sha256": digest,
        "baseline_sha256": BASELINE_SHA256,
        "pdf_path": pdf_path,
        "baseline_observation": "The visually checked baseline scan shows 438 as parcel 6 in Lot 74, 14.07 by the 446 strip, and 50.72 and 65.93 near 454; a checksum change alone does not establish that the requested correction was published.",
    }


def main():
    try:
        result = check()
    except LookupFailure as exc:
        print(json.dumps({"status": "lookup_failure", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
