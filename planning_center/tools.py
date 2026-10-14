"""Tool definitions (OpenAI function-calling JSON schema) for Planning Center Online."""

from __future__ import annotations

from typing import Any

from .client import (
    GatekeeperEgressNotConfigured,
    PlanningCenterAPIError,
    accept_schedule,
    check_in_volunteer,
    decline_schedule,
    list_checkin_events,
    list_checkin_stations,
    list_schedules,
)

__all__ = ["TOOL_NAMES", "tool_definitions", "handle_tool_call"]

TOOL_NAMES = (
    "list_volunteer_schedules",
    "accept_volunteer_schedule",
    "decline_volunteer_schedule",
    "checkin_church_volunteer",
    "list_church_checkin_stations",
    "list_church_checkin_events",
)

_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_volunteer_schedules",
            "description": (
                "List upcoming church volunteer serving schedules from Planning Center Services "
                "for the authenticated user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["all", "pending", "confirmed", "declined"],
                        "description": "Filter by schedule status (default 'all').",
                    },
                    "future_only": {
                        "type": "boolean",
                        "description": "Whether to only return future schedules (default true).",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accept_volunteer_schedule",
            "description": "Accept a church volunteer schedule assignment in Planning Center Services.",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {
                        "type": "string",
                        "description": "The Planning Center schedule ID to accept.",
                    }
                },
                "required": ["schedule_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "decline_volunteer_schedule",
            "description": "Decline a church volunteer schedule assignment in Planning Center Services.",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {
                        "type": "string",
                        "description": "The Planning Center schedule ID to decline.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional reason for declining.",
                    },
                },
                "required": ["schedule_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "checkin_church_volunteer",
            "description": (
                "Check the user in for their volunteer shift at church via Planning Center Check-Ins, "
                "triggering their volunteer name tag print at the church check-in kiosk/printer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "station_id": {
                        "type": "string",
                        "description": "Optional specific check-in station ID. If omitted, uses active station.",
                    },
                    "event_period_id": {
                        "type": "string",
                        "description": "Optional specific event period ID. If omitted, uses today's active service period.",
                    },
                    "print_name_tag": {
                        "type": "boolean",
                        "description": "Whether to trigger the name tag label print at the kiosk (default true).",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_church_checkin_stations",
            "description": "List available church check-in kiosks and attached label printers from Planning Center Check-Ins.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_church_checkin_events",
            "description": "List active church worship services and check-in events from Planning Center Check-Ins.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]


def tool_definitions() -> list[dict]:
    """Return tool schemas suitable for passing to model tool calling."""
    return [t.copy() for t in _TOOLS]


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an LLM tool call to the matching Planning Center client function."""
    try:
        if name == "list_volunteer_schedules":
            return list_schedules(
                future_only=args.get("future_only", True),
                status_filter=args.get("status", "all"),
            )
        elif name == "accept_volunteer_schedule":
            return accept_schedule(str(args["schedule_id"]))
        elif name == "decline_volunteer_schedule":
            return decline_schedule(str(args["schedule_id"]), reason=args.get("reason"))
        elif name == "checkin_church_volunteer":
            return check_in_volunteer(
                station_id=args.get("station_id"),
                event_period_id=args.get("event_period_id"),
                print_name_tag=args.get("print_name_tag", True),
            )
        elif name == "list_church_checkin_stations":
            return list_checkin_stations()
        elif name == "list_church_checkin_events":
            return list_checkin_events()
        else:
            return {"ok": False, "error": f"Unknown tool: {name}"}
    except GatekeeperEgressNotConfigured as e:
        return {"ok": False, "error": "GatekeeperEgressNotConfigured", "message": str(e)}
    except PlanningCenterAPIError as e:
        return e.as_result()
    except Exception as e:
        return {"ok": False, "error": str(e)}
