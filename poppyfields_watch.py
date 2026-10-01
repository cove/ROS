#!/usr/bin/env python3
"""Check official LA County ROS, Assessor map, and permit records independently."""

import hashlib
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
ROS_VIEWER = "https://dpw.lacounty.gov/sur/landrecords/#map"
PARCEL_QUERY = "https://public.gis.lacounty.gov/public/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0/query"
ROS_QUERY = "https://dpw.gis.lacounty.gov/dpw/rest/services/landrecords_mapviewer/MapServer/6/query"
MAP_BASE = "https://maps.assessor.lacounty.gov/GeoCortex/Essentials/PAIS/REST/sites/PAIS/Resources"
MAP_ID = "5841-018-"
MAP_BASELINE_SHA256 = "e0c88d50bd8dba318b5eff3bacd812c783bd2ad574d0e9f52b58edfc9d07ce46"
PERMIT_BASE = "https://losangelescountyca-energovweb.tylerhost.net/apps/selfservice/api"
PERMIT_ID = "167b6b58-dd21-41ee-86ef-a46ca50687bc"
PERMIT_NUMBER = "CREB2025000667"
PERMIT_VIEWER = f"https://losangelescountyca-energovweb.tylerhost.net/apps/selfservice#/permit/{PERMIT_ID}"


class LookupFailure(Exception):
    pass


def county_json(url, *, data=None, headers=None):
    request = Request(url, data=data, headers={"User-Agent": "poppyfields-county-watch/1.0", **(headers or {})})
    try:
        with urlopen(request, timeout=35) as response:
            if response.status != 200:
                raise LookupFailure(f"HTTP {response.status} from {url}")
            result = json.load(response)
    except (OSError, ValueError, TimeoutError) as exc:
        raise LookupFailure(f"County JSON request failed at {url}: {exc}") from exc
    if not isinstance(result, dict) or "error" in result:
        raise LookupFailure(f"Invalid County JSON from {url}: {result!r}")
    return result


def arcgis_features(url, **parameters):
    result = county_json(url, data=urlencode({**parameters, "f": "json"}).encode("ascii"),
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
    if not isinstance(result.get("features"), list):
        raise LookupFailure(f"County ArcGIS response lacks features at {url}: {result!r}")
    return result["features"]


def surveys_for(ain):
    parcel = arcgis_features(PARCEL_QUERY, where=f"AIN='{ain}'", outFields="AIN,APN,SitusAddress",
                             outSR="102100", returnGeometry="true")
    if len(parcel) != 1 or parcel[0].get("attributes", {}).get("AIN") != ain:
        raise LookupFailure(f"Expected one exact County parcel for AIN {ain}; found {len(parcel)}")
    geometry = parcel[0].get("geometry")
    if not isinstance(geometry, dict) or not geometry.get("rings"):
        raise LookupFailure(f"No usable parcel polygon for AIN {ain}")
    features = arcgis_features(ROS_QUERY, where="1=1", geometry=json.dumps(geometry, separators=(",", ":")),
                              geometryType="esriGeometryPolygon", inSR="102100",
                              spatialRel="esriSpatialRelIntersects",
                              outFields="OBJECTID,BOOK_PAGE,REC_DATE,PLS,RCE,RS_BOOK,LOCATION",
                              returnGeometry="false")
    records = []
    for feature in features:
        attributes = feature.get("attributes")
        if not isinstance(attributes, dict) or not attributes.get("BOOK_PAGE"):
            raise LookupFailure(f"Incomplete County ROS result for AIN {ain}")
        date_ms = attributes.get("REC_DATE")
        records.append({
            "book_page": attributes["BOOK_PAGE"],
            "recorded_date": datetime.fromtimestamp(date_ms / 1000, PACIFIC).date().isoformat()
            if isinstance(date_ms, (int, float)) else None,
            "surveyor_license": attributes.get("PLS") or attributes.get("RCE"),
            "rs_book": attributes.get("RS_BOOK"),
            "objectid": attributes.get("OBJECTID"),
        })
    return records


def check_ros():
    control = surveys_for("5841018007")
    if not any(record["book_page"] == "370-015" for record in control):
        raise LookupFailure("ROS control failed: 426 E Poppyfields AIN 5841018007 lacks RS 370-015")
    targets = {
        "446 E Poppyfields Dr": {ain: surveys_for(ain) for ain in ("5841018004", "5841018005")},
        "454 E Poppyfields Dr": {"5841018003": surveys_for("5841018003")},
    }
    return {"status": "verified", "control": {"ain": "5841018007", "surveys": control},
            "targets": targets, "viewer": ROS_VIEWER}


def check_map():
    nav_url = f"{MAP_BASE}/ParcelMapNavigator?{urlencode({'f': 'json', 'MapId': MAP_ID})}"
    pdf_url = f"{MAP_BASE}/ParcelMap?{urlencode({'f': 'file', 'MapId': MAP_ID})}"
    navigator = county_json(nav_url)
    if navigator.get("MapId") != MAP_ID:
        raise LookupFailure(f"County map navigator did not verify {MAP_ID}: {navigator!r}")
    try:
        with urlopen(Request(pdf_url, headers={"User-Agent": "poppyfields-county-watch/1.0", "Cache-Control": "no-cache"}), timeout=35) as response:
            if response.status != 200 or response.url.split("/")[2].lower() != "maps.assessor.lacounty.gov":
                raise LookupFailure(f"Unexpected County map redirect or HTTP {response.status}: {response.url}")
            content_type = response.headers.get_content_type()
            pdf = response.read(15_000_001)
    except (OSError, TimeoutError) as exc:
        raise LookupFailure(f"County Assessor PDF request failed at {pdf_url}: {exc}") from exc
    if (content_type != "application/pdf" or not 10_000 <= len(pdf) <= 15_000_000
            or not pdf.startswith(b"%PDF-") or b"%%EOF" not in pdf[-1024:]):
        raise LookupFailure(f"Missing or invalid Assessor PDF at {pdf_url}: {content_type}, {len(pdf)} bytes")
    digest = hashlib.sha256(pdf).hexdigest()
    with tempfile.NamedTemporaryFile(mode="wb", prefix="assessor_5841_018_", suffix=".pdf", delete=False) as output:
        output.write(pdf)
        pdf_path = str(Path(output.name).resolve())
    return {"status": "verified_unchanged" if digest == MAP_BASELINE_SHA256 else "review_required",
            "map_id": MAP_ID, "watched_parcels": {
                "438 E Poppyfields Dr": ["5841-018-006"],
                "446 E Poppyfields Dr": ["5841-018-004", "5841-018-005"],
                "454 E Poppyfields Dr": ["5841-018-003"]},
            "source_url": pdf_url, "navigator_url": nav_url,
            "sha256": digest, "baseline_sha256": MAP_BASELINE_SHA256, "pdf_path": pdf_path,
            "baseline_observation": "438 is parcel 6 in Lot 74; the baseline shows 14.07 by the 446 strip and 50.72/65.93 by 454. A changed checksum requires visual review."}


def check_permit():
    tenants_url = f"{PERMIT_BASE}/Home/GetTenants"
    tenant_response = county_json(tenants_url)
    tenants = tenant_response.get("Result")
    if tenant_response.get("Success") is not True or not isinstance(tenants, list) or len(tenants) != 1:
        raise LookupFailure(f"Cannot identify County permit portal tenant: {tenants!r}")
    tenant = tenants[0]
    if tenant.get("FriendlyTenantName") != "Los Angeles County" or not tenant.get("TenantID") or not tenant.get("TenantUrl"):
        raise LookupFailure(f"Unexpected County permit portal tenant: {tenant!r}")
    headers = {"Accept": "application/json", "tenantId": str(tenant["TenantID"]),
               "tenantName": str(tenant["TenantName"]), "Tyler-TenantUrl": str(tenant["TenantUrl"]),
               "Tyler-Tenant-Culture": "en-US"}
    record_url = f"{PERMIT_BASE}/energov/permits/{PERMIT_ID}"
    activity_url = f"{PERMIT_BASE}/energov/workflow/summary/activities/1/{PERMIT_ID}"
    record = county_json(record_url, headers=headers)
    activities = county_json(activity_url, headers=headers)
    item = record.get("Result")
    steps = activities.get("Result")
    if (record.get("Success") is not True or not isinstance(item, dict)
            or item.get("PermitNumber") != PERMIT_NUMBER or item.get("MainParcelNumber") != "5841018003"
            or activities.get("Success") is not True or not isinstance(steps, list)):
        raise LookupFailure(f"Invalid permit identity or workflow response: {record.get('ErrorMessage')!r}; {activities.get('ErrorMessage')!r}")
    reviews = [{"name": s.get("Name"), "status_code": s.get("Status"),
                "status_name": s.get("ActivityStatusName"), "completed_on": s.get("CompletedOn"),
                "raw": s}
               for s in steps if s.get("Name") == "Permit Plan Review - Rebuild"]
    clearances = [{"name": s.get("Name"), "status_code": s.get("Status"),
                   "status_name": s.get("ActivityStatusName"), "completed_on": s.get("CompletedOn"),
                   "raw": s}
                  for s in steps if s.get("Name") == "Permit Plan Clearances - Rebuild"]
    if not reviews or not clearances or not isinstance(item.get("PermitStatus"), str):
        raise LookupFailure("Permit workflow is missing required plan review or clearance steps")
    issued = bool(item.get("Issued") or item.get("IssueDate"))
    return {"status": "verified", "permit_number": PERMIT_NUMBER, "address": item.get("MainAddress"),
            "apn": item["MainParcelNumber"], "permit_status": item["PermitStatus"],
            "issued": issued, "issue_date": item.get("IssueDate"),
            "plan_review_activities": reviews, "clearance_activities": clearances,
            "plan_review_passed": any(s.get("status_name") in ("Passed", "Approved") for s in reviews),
            "clearances_finished": all(s.get("completed_on") for s in clearances),
            "viewer": PERMIT_VIEWER, "record_url": record_url, "activity_url": activity_url}


def main():
    output = {"checked_at": datetime.now(PACIFIC).isoformat(timespec="seconds"), "checks": {}}
    for name, checker in (("ros", check_ros), ("assessor_map", check_map), ("permit", check_permit)):
        try:
            output["checks"][name] = checker()
        except Exception as exc:
            output["checks"][name] = {"status": "lookup_failure", "error": f"{type(exc).__name__}: {exc}"}
    output["status"] = ("verified" if all(c["status"] in ("verified", "verified_unchanged", "review_required")
                    for c in output["checks"].values()) else "partial_failure")
    print(json.dumps(output, indent=2))
    return 0 if output["status"] == "verified" else 2


if __name__ == "__main__":
    sys.exit(main())
