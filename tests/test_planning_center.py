"""Unit tests for the Planning Center Online skill."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from planning_center import (
    TOOL_NAMES,
    GatekeeperEgressNotConfigured,
    accept_schedule,
    check_in_volunteer,
    decline_schedule,
    get_me,
    handle_tool_call,
    list_checkin_events,
    list_checkin_stations,
    list_schedules,
    tool_definitions,
)


def _mock_response(payload: dict, status: int = 200):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestPlanningCenterSkill(unittest.TestCase):
    def setUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "PLANNING_CENTER_BASE_URL": "http://gatekeeper.internal:8089/planning-center",
                "FACTORY_RUN_TOKEN": "run-tok-test",
            },
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def test_requires_gatekeeper_egress_config(self):
        with patch.dict(os.environ, {"PLANNING_CENTER_BASE_URL": "", "FACTORY_RUN_TOKEN": ""}, clear=False):
            with self.assertRaises(GatekeeperEgressNotConfigured):
                get_me()
            res = handle_tool_call("list_volunteer_schedules", {})
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "GatekeeperEgressNotConfigured")

    @patch("urllib.request.urlopen")
    def test_get_me_and_list_schedules(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"data": {"id": "p-101", "attributes": {"first_name": "Stephanie", "last_name": "Sackrider"}}}),
            _mock_response(
                {
                    "data": [
                        {
                            "id": "sched-1",
                            "attributes": {
                                "status": "pending",
                                "team_name": "Greeting Team",
                                "team_position_name": "Greeter",
                                "service_type_name": "Sunday Worship",
                                "dates": "Oct 18, 2026",
                                "sort_date": "2099-10-18",
                            },
                        }
                    ],
                    "included": [],
                }
            ),
        ]
        res = list_schedules(future_only=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["person_id"], "p-101")
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["schedules"][0]["position_name"], "Greeter")
        self.assertTrue(res["schedules"][0]["can_accept"])

        first_req = mock_urlopen.call_args_list[0][0][0]
        self.assertEqual(first_req.full_url, "http://gatekeeper.internal:8089/planning-center/people/v2/me")
        self.assertEqual(first_req.headers.get("Authorization"), "Bearer run-tok-test")

    @patch("urllib.request.urlopen")
    def test_accept_and_decline_schedule(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"data": {"id": "p-101", "attributes": {"name": "Dale Sackrider"}}}),
            _mock_response({"data": {"id": "sched-9"}}),
            _mock_response({"data": {"id": "p-101", "attributes": {"name": "Dale Sackrider"}}}),
            _mock_response({"data": {"id": "sched-9"}}),
        ]
        acc = accept_schedule("sched-9")
        self.assertTrue(acc["ok"])
        self.assertEqual(acc["status"], "confirmed")

        dec = decline_schedule("sched-9", reason="Out of town")
        self.assertTrue(dec["ok"])
        self.assertEqual(dec["status"], "declined")
        self.assertEqual(dec["reason"], "Out of town")

    @patch("urllib.request.urlopen")
    def test_check_in_volunteer_and_stations(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"data": {"id": "p-101", "attributes": {"name": "Dale Sackrider"}}}),
            _mock_response({"data": [{"id": "ep-55"}]}),
            _mock_response({"data": [{"id": "st-1", "attributes": {"name": "Lobby Kiosk"}}], "included": []}),
            _mock_response({"data": {"id": "chk-777"}}),
        ]
        res = handle_tool_call("checkin_church_volunteer", {"print_name_tag": True})
        self.assertTrue(res["ok"])
        self.assertEqual(res["check_in_id"], "chk-777")
        self.assertEqual(res["station_name"], "Lobby Kiosk")
        self.assertEqual(res["print_station_status"], "triggered")

    def test_tool_definitions_match_tool_names(self):
        defs = tool_definitions()
        names = tuple(d["function"]["name"] for d in defs)
        self.assertEqual(names, TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
