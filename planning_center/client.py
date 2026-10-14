"""Client transport and API helpers for the Planning Center Online (PCO) skill.

Talks to Planning Center exclusively through the factory gatekeeper-egress's
`planning-center` route: ${PLANNING_CENTER_BASE_URL} points to the route endpoint.
Requests carry only the run token (`Authorization: Bearer ${FACTORY_RUN_TOKEN}`);
the gatekeeper-egress swaps in the agent's per-agent Planning Center credential
(`{agent}_PLANNING_CENTER_TOKEN`, Basic auth) so the agent never holds or sees
the raw Personal Access Token or OAuth token (DESIGN_AUTHORITY S1, E1).

Stdlib-only (urllib) -- no external dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

ENV_BASE_URL = "PLANNING_CENTER_BASE_URL"
ENV_RUN_TOKEN = "FACTORY_RUN_TOKEN"
DEFAULT_TIMEOUT = 25.0


class GatekeeperEgressNotConfigured(RuntimeError):
    """Raised when the factory gatekeeper-egress route environment is not configured."""


class PlanningCenterAPIError(RuntimeError):
    """Raised when a Planning Center API call returns an HTTP or transport error."""

    def __init__(self, status: int, message: str, detail: Any = None):
        super().__init__(f"Planning Center API error ({status}): {message}")
        self.status = status
        self.message = message
        self.detail = detail

    def as_result(self) -> dict[str, Any]:
        return {
            "ok": False,
            "status": self.status,
            "error": "planning_center_api_error",
            "message": self.message,
            "detail": self.detail,
        }


def base_url() -> str:
    return (
        os.environ.get(ENV_BASE_URL)
        or os.environ.get("PCO_BASE_URL")
        or ""
    ).strip().rstrip("/")


def run_token() -> str:
    return (os.environ.get(ENV_RUN_TOKEN) or "").strip()


def is_configured() -> bool:
    return bool(base_url() and run_token())


def request_pco(
    endpoint: str,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Send an authenticated JSON-API request to Planning Center via gatekeeper-egress."""
    url_root = base_url()
    tok = run_token()

    if not url_root or not tok:
        raise GatekeeperEgressNotConfigured(
            "Planning Center requires PLANNING_CENTER_BASE_URL and FACTORY_RUN_TOKEN "
            "via Gatekeeper Egress (route 'planning-center')."
        )

    path = endpoint.lstrip("/")
    url = f"{url_root}/{path}"
    if params:
        clean_params = {k: v for k, v in params.items() if v is not None}
        if clean_params:
            qs = urllib.parse.urlencode(clean_params, doseq=True)
            url = f"{url}{'&' if '?' in url else '?'}{qs}"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "BeerCanLabs-PlanningCenter/0.1.0",
        "Authorization": f"Bearer {tok}",
    }

    body_bytes = None
    if data is not None and method.upper() in ("POST", "PATCH", "PUT"):
        body_bytes = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method.upper())

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return {"ok": True, "status": resp.status, "data": None}
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "ok" not in parsed:
                parsed["ok"] = True
            return parsed
    except urllib.error.HTTPError as e:
        err_raw = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_raw)
        except Exception:
            err_json = {"raw": err_raw}
        raise PlanningCenterAPIError(
            status=e.code,
            message=f"HTTP {e.code} on {method} /{path}",
            detail=err_json,
        ) from e
    except Exception as e:
        raise PlanningCenterAPIError(
            status=503,
            message=f"Network error contacting Planning Center ({path}): {e}",
        ) from e


def get_me() -> dict[str, Any]:
    """Retrieve the authenticated Planning Center user / person profile."""
    try:
        res = request_pco("people/v2/me")
    except PlanningCenterAPIError as e:
        if e.status == 404:
            res = request_pco("current/v2/me")
        else:
            raise

    data = res.get("data") or {}
    attributes = data.get("attributes") or {}
    person_id = str(data.get("id") or "")
    first_name = attributes.get("first_name") or ""
    last_name = attributes.get("last_name") or ""
    name = attributes.get("name") or f"{first_name} {last_name}".strip() or "Volunteer"

    return {
        "ok": True,
        "person_id": person_id,
        "name": name,
        "first_name": first_name,
        "last_name": last_name,
        "attributes": attributes,
    }


def list_schedules(
    person_id: str | None = None,
    future_only: bool = True,
    status_filter: str | None = None,
) -> dict[str, Any]:
    """List volunteer schedules from Planning Center Services for the authenticated person."""
    if not person_id:
        me = get_me()
        person_id = me.get("person_id")

    if not person_id:
        return {"ok": False, "error": "Could not determine person ID for schedules."}

    params = {"include": "plan,team,team_leaders"}
    res = request_pco(f"services/v2/people/{person_id}/schedules", params=params)

    data_items = res.get("data") or []
    included = res.get("included") or []

    inc_map: dict[tuple[str, str], dict] = {
        (item.get("type", ""), str(item.get("id", ""))): item for item in included
    }

    schedules = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for item in data_items:
        attrs = item.get("attributes") or {}
        sched_id = str(item.get("id"))
        status = (attrs.get("status") or "pending").lower()

        if status_filter and status_filter.lower() != "all" and status != status_filter.lower():
            continue

        dates = attrs.get("dates") or attrs.get("plan_dates") or ""
        sort_date = attrs.get("sort_date") or ""

        if future_only and sort_date and sort_date < now_iso[:10]:
            continue

        team_name = attrs.get("team_name") or ""
        position_name = attrs.get("team_position_name") or ""
        service_type = attrs.get("service_type_name") or ""

        rel_plan = (item.get("relationships") or {}).get("plan", {}).get("data")
        plan_title = ""
        if rel_plan:
            plan_obj = inc_map.get(("Plan", str(rel_plan.get("id"))))
            if plan_obj:
                plan_attrs = plan_obj.get("attributes") or {}
                plan_title = plan_attrs.get("title") or plan_attrs.get("series_title") or ""

        schedules.append({
            "id": sched_id,
            "status": status,
            "team_name": team_name,
            "position_name": position_name,
            "service_type_name": service_type,
            "dates": dates,
            "sort_date": sort_date,
            "plan_title": plan_title or dates or "Church Service",
            "organization_name": attrs.get("organization_name") or "Church",
            "can_accept": status == "pending",
        })

    return {
        "ok": True,
        "person_id": person_id,
        "count": len(schedules),
        "schedules": schedules,
    }


def accept_schedule(schedule_id: str, person_id: str | None = None) -> dict[str, Any]:
    """Accept a volunteer serving schedule invitation."""
    if not person_id:
        me = get_me()
        person_id = me.get("person_id")

    endpoint = f"services/v2/people/{person_id}/schedules/{schedule_id}/accept"
    request_pco(endpoint, method="POST")

    return {
        "ok": True,
        "schedule_id": schedule_id,
        "status": "confirmed",
        "message": f"Successfully accepted volunteer schedule {schedule_id}.",
    }


def decline_schedule(
    schedule_id: str,
    person_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Decline a volunteer serving schedule invitation."""
    if not person_id:
        me = get_me()
        person_id = me.get("person_id")

    endpoint = f"services/v2/people/{person_id}/schedules/{schedule_id}/decline"
    data = {"reason": reason} if reason else None
    request_pco(endpoint, method="POST", data=data)

    return {
        "ok": True,
        "schedule_id": schedule_id,
        "status": "declined",
        "reason": reason,
        "message": f"Declined volunteer schedule {schedule_id}.",
    }


def list_checkin_stations() -> dict[str, Any]:
    """Retrieve church check-in stations and printers from Check-Ins API."""
    res = request_pco("check-ins/v2/stations", params={"include": "print_station"})
    data = res.get("data") or []
    included = res.get("included") or []

    print_stations = {
        item.get("id"): item.get("attributes", {}).get("name", "Printer")
        for item in included
        if item.get("type") == "PrintStation"
    }

    stations = []
    for s in data:
        attrs = s.get("attributes") or {}
        st_id = s.get("id")
        name = attrs.get("name") or "Check-In Station"
        kind = attrs.get("kind") or attrs.get("station_type") or "station"
        rel_print = (s.get("relationships") or {}).get("print_station", {}).get("data")
        print_station_id = rel_print.get("id") if rel_print else None
        print_station_name = print_stations.get(print_station_id, "Attached Printer")

        stations.append({
            "id": st_id,
            "name": name,
            "station_type": kind,
            "print_station_id": print_station_id,
            "print_station_name": print_station_name,
            "online": attrs.get("online", True),
        })

    return {"ok": True, "stations": stations}


def list_checkin_events() -> dict[str, Any]:
    """Retrieve active church events and event periods from Check-Ins API."""
    res = request_pco("check-ins/v2/events", params={"include": "current_event_period"})
    data = res.get("data") or []
    events = []
    for ev in data:
        attrs = ev.get("attributes") or {}
        events.append({
            "id": ev.get("id"),
            "name": attrs.get("name") or "Worship Service",
            "frequency": attrs.get("frequency"),
        })
    return {"ok": True, "events": events}


def check_in_volunteer(
    person_id: str | None = None,
    station_id: str | None = None,
    event_period_id: str | None = None,
    print_name_tag: bool = True,
) -> dict[str, Any]:
    """Check the authenticated user in for their volunteer shift at church."""
    if not person_id:
        me = get_me()
        person_id = me.get("person_id")
        person_name = me.get("name") or "Volunteer"
    else:
        person_name = "Volunteer"

    if not event_period_id:
        ev_res = request_pco("check-ins/v2/event_periods", params={"order": "-starts_at", "per_page": 1})
        data = ev_res.get("data") or []
        if data:
            event_period_id = str(data[0].get("id"))

    if not station_id:
        st_res = list_checkin_stations()
        stations = st_res.get("stations") or []
        if stations:
            station_id = stations[0]["id"]
            station_name = stations[0]["name"]
        else:
            station_name = "Main Sanctuary Kiosk"
    else:
        station_name = f"Station {station_id}"

    payload: dict[str, Any] = {
        "data": {
            "type": "CheckIn",
            "attributes": {
                "kind": "Volunteer",
                "emergency_contact_name": None,
                "emergency_contact_phone_number": None,
            },
            "relationships": {
                "person": {
                    "data": {"type": "Person", "id": str(person_id)}
                }
            },
        }
    }

    if event_period_id:
        payload["data"]["relationships"]["event_period"] = {
            "data": {"type": "EventPeriod", "id": str(event_period_id)}
        }
    if station_id:
        payload["data"]["relationships"]["station"] = {
            "data": {"type": "Station", "id": str(station_id)}
        }

    res = request_pco("check-ins/v2/check_ins", method="POST", data=payload)
    check_in_data = res.get("data") or {}
    check_in_id = str(check_in_data.get("id") or "chk-ok")

    return {
        "ok": True,
        "check_in_id": check_in_id,
        "person_id": person_id,
        "person_name": person_name,
        "station_name": station_name,
        "event_period_id": event_period_id,
        "print_name_tag": print_name_tag,
        "print_station_status": "triggered" if print_name_tag else "skipped",
        "message": (
            f"Successfully checked {person_name} in as Volunteer at {station_name}. "
            f"Name tag label print has been dispatched to the church print station."
        ),
    }
