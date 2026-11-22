"""PAIS service for "Purchase specialized equipment and instruments".

The DCR graph runs on dcrgraphs.net (the process engine). This Flask service
sits in front of it and is the one entry point for everyone else:

  * the CEP (Siddhi) starts cases:          POST /from_siddhi
  * people work on cases:                   GET  /cases/<id>
                                            POST /cases/<id>/activities/<label>
  * the CEP is told about selected events:  PAIS -> SIDDHI_URL (optional)
  * every executed activity is written to an event log (CSV) for conformance checking.

Configuration (environment variables):
  DCR_USERNAME, DCR_PASSWORD, DCR_GRAPH_ID   dcrgraphs.net account and graph
  SIDDHI_URL      where to POST events for the CEP (optional; without it they are only printed)
  EVENT_LOG       path of the CSV event log (default: event_log.csv)
"""
import csv
import json
import os
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

from dcr_client import DcrClient, DcrError, norm

# CEP event type -> entry activity that starts a case.
TRIGGERS = {
    "LowStock": "Request Consumables",
    "CasualtySurge": "Declare Emergency Procurement",
    "CapacityShortage": "Identify Equipment Needed",
}

# Activities the CEP is told about (PAIS -> CEP), with the event type it receives.
NOTIFY_CEP = {
    "Register in Inventory": "StockUpdated",
    "Put Into Service": "EquipmentInService",
}

# Role recorded in the log when the PAIS executes an activity itself, e.g. on a CEP trigger.
SYSTEM_ROLE = "PAIS"

LOG_FIELDS = ["case_id", "activity", "timestamp", "role", "data"]


def find(activities, label):
    """Find an activity by its label (case and spacing do not matter) or by its ID."""
    wanted = norm(label)
    return next((a for a in activities if norm(a.label) == wanted or a.id == label), None)


def create_app(dcr, siddhi_url=None, log_path="event_log.csv"):
    app = Flask(__name__)
    cases = []  # case IDs started through this service, newest last

    def write_log(case_id, activity, role, data):
        new_file = not os.path.exists(log_path)
        with open(log_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow({"case_id": case_id, "activity": activity,
                             "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "role": role, "data": json.dumps(data) if data else ""})

    def notify_cep(case_id, activity, data):
        event_type = next((t for label, t in NOTIFY_CEP.items() if norm(label) == norm(activity)), None)
        if event_type is None:
            return
        event = {"eventType": event_type, "caseId": case_id, **(data or {})}
        if not siddhi_url:
            print(f"[PAIS -> CEP] (no SIDDHI_URL set) {event}")
            return
        try:
            requests.post(siddhi_url, json=event, timeout=5)
            print(f"[PAIS -> CEP] {event}")
        except requests.RequestException as e:
            # The case already moved on in the engine; a CEP outage must not undo that.
            print(f"[PAIS -> CEP] could not send {event}: {e}")

    def state(case_id):
        activities = dcr.activities(case_id)
        pending = [a.label for a in activities if a.pending and a.included]
        return {
            "caseId": case_id,
            "enabled": [a.to_dict() for a in activities if a.enabled],
            "pending": pending,
            # Judged from the pending flags: the API's own isAccepting ignores pending activities in nests.
            "finished": not pending,
        }

    def execute(case_id, label, role, data=None):
        """Execute an activity by label. Returns (response body, HTTP status)."""
        activity = find(dcr.activities(case_id), label)
        if activity is None:
            return {"error": f"'{label}' is not available in case {case_id} (unknown or excluded)"}, 404
        if not activity.enabled:
            return {"error": f"'{activity.label}' is not enabled in case {case_id}"}, 409
        if role != SYSTEM_ROLE and activity.roles and role not in activity.roles:
            return {"error": f"Role '{role}' may not execute '{activity.label}' "
                             f"(allowed: {', '.join(activity.roles)})"}, 403
        dcr.execute(case_id, activity.id)
        write_log(case_id, activity.label, role, data)
        notify_cep(case_id, activity.label, data)
        return state(case_id), 200

    def start_case(event_type, data):
        if event_type not in TRIGGERS:
            return {"error": f"Unknown event type '{event_type}'. Known: {', '.join(TRIGGERS)}"}, 400
        case_id = dcr.create_case()
        cases.append(case_id)
        body, status = execute(case_id, TRIGGERS[event_type], SYSTEM_ROLE, data)
        print(f"[CEP -> PAIS] {event_type} started case {case_id}")
        return body, (201 if status == 200 else status)

    @app.errorhandler(DcrError)
    def dcr_error(e):
        return jsonify({"error": str(e)}), 502

    @app.post("/from_siddhi")
    def from_siddhi():
        """Called by Siddhi when it detects a pattern, e.g. {"eventType": "LowStock", "item": "...", "value": 12}."""
        data = request.get_json(force=True, silent=True) or {}
        body, status = start_case(data.pop("eventType", None), data)
        return jsonify(body), status

    @app.get("/cases")
    def list_cases():
        return jsonify({"cases": cases})

    @app.get("/cases/<case_id>")
    def get_case(case_id):
        return jsonify(state(case_id))

    @app.post("/cases/<case_id>/activities/<label>")
    def do_activity(case_id, label):
        """Body: {"role": "Finance", "data": {...}}. The role is checked against the graph's roles."""
        payload = request.get_json(force=True, silent=True) or {}
        if not payload.get("role"):
            return jsonify({"error": "Send the role executing the activity, e.g. {\"role\": \"Finance\"}"}), 400
        body, status = execute(case_id, label, payload["role"], payload.get("data"))
        return jsonify(body), status

    return app


if __name__ == "__main__":
    missing = [v for v in ("DCR_USERNAME", "DCR_PASSWORD", "DCR_GRAPH_ID") if not os.getenv(v)]
    if missing:
        raise SystemExit(f"Set these environment variables first: {', '.join(missing)}")
    client = DcrClient(os.environ["DCR_GRAPH_ID"], os.environ["DCR_USERNAME"], os.environ["DCR_PASSWORD"])
    service = create_app(client, os.getenv("SIDDHI_URL"), os.getenv("EVENT_LOG", "event_log.csv"))
    print("Purchasing PAIS running at http://localhost:5000")
    service.run(host="0.0.0.0", port=5000)
