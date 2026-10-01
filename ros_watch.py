#!/usr/bin/env python3
"""Check LA County's filed Record of Survey polygons against specific parcels.

Only official LA County ArcGIS REST services are used. A known filed survey for
426 E Poppyfields is required before any negative target result is accepted.
"""

import json
import sys
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

PARCEL_QUERY = (
    "https://public.gis.lacounty.gov/public/rest/services/"
    "LACounty_Cache/LACounty_Parcel/MapServer/0/query"
)
ROS_QUERY = (
    "https://dpw.gis.lacounty.gov/dpw/rest/services/"
    "landrecords_mapviewer/MapServer/6/query"
)
VIEWER = "https://dpw.lacounty.gov/sur/landrecords/#map"
CONTROL_AIN = "5841018007"
CONTROL_BOOK_PAGE = "370-015"
TARGETS = {
    "446 E Poppyfields Dr": ("5841018004", "5841018005"),
    "454 E Poppyfields Dr": ("5841018003",),
}
PACIFIC = ZoneInfo("America/Los_Angeles")


class LookupFailure(Exception):
    pass


def query(url, **parameters):
    request = Request(
        url,
        data=urlencode({**parameters, "f": "json"}).encode("ascii"),
        headers={"User-Agent": "la-county-ros-watch/1.0", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise LookupFailure(f"HTTP {response.status} from {url}")
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise LookupFailure(f"County query failed at {url}: {exc}") from exc
    if not isinstance(payload, dict) or "error" in payload or not isinstance(payload.get("features"), list):
        raise LookupFailure(f"Invalid County response at {url}: {payload.get('error') if isinstance(payload, dict) else payload!r}")
    return payload["features"]


def parcel_geometry(ain):
    features = query(
        PARCEL_QUERY,
        where=f"AIN='{ain}'",
        outFields="AIN,APN,SitusAddress",
        outSR="102100",
        returnGeometry="true",
    )
    if len(features) != 1 or features[0].get("attributes", {}).get("AIN") != ain:
        raise LookupFailure(f"Expected one exact County parcel for AIN {ain}; found {len(features)}")
    geometry = features[0].get("geometry")
    if not isinstance(geometry, dict) or not geometry.get("rings"):
        raise LookupFailure(f"No usable parcel polygon for AIN {ain}")
    return geometry


def surveys_for(ain):
    geometry = parcel_geometry(ain)
    features = query(
        ROS_QUERY,
        where="1=1",
        geometry=json.dumps(geometry, separators=(",", ":")),
        geometryType="esriGeometryPolygon",
        inSR="102100",
        spatialRel="esriSpatialRelIntersects",
        outFields="OBJECTID,BOOK_PAGE,REC_DATE,PLS,RCE,RS_BOOK,LOCATION",
        returnGeometry="false",
    )
    records = []
    for feature in features:
        attrs = feature.get("attributes")
        if not isinstance(attrs, dict) or not attrs.get("BOOK_PAGE"):
            raise LookupFailure(f"Incomplete ROS result for AIN {ain}")
        date_ms = attrs.get("REC_DATE")
        records.append({
            "book_page": attrs["BOOK_PAGE"],
            "recorded_date": datetime.fromtimestamp(date_ms / 1000, PACIFIC).date().isoformat() if isinstance(date_ms, (int, float)) else None,
            "surveyor_license": attrs.get("PLS") or attrs.get("RCE"),
            "rs_book": attrs.get("RS_BOOK"),
            "objectid": attrs.get("OBJECTID"),
        })
    return records


def check():
    control = surveys_for(CONTROL_AIN)
    if not any(record["book_page"] == CONTROL_BOOK_PAGE for record in control):
        raise LookupFailure(f"Control failed: 426 E Poppyfields AIN {CONTROL_AIN} lacks RS {CONTROL_BOOK_PAGE}")
    results = {}
    for property_name, ains in TARGETS.items():
        results[property_name] = {ain: surveys_for(ain) for ain in ains}
    return {
        "status": "verified",
        "checked_at": datetime.now(PACIFIC).isoformat(timespec="seconds"),
        "control": {"ain": CONTROL_AIN, "expected_book_page": CONTROL_BOOK_PAGE, "surveys": control},
        "targets": results,
        "viewer": VIEWER,
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