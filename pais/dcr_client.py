"""Client for the DCR Active Repository API (repository.dcrgraphs.net).

Wraps the three calls the PAIS needs, as in the course guide: create a case
(a "simulation"), read the case's activities, and execute an activity.
"""
import html
import json
import re

import requests

API = "https://repository.dcrgraphs.net/api"


class DcrError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def norm(text):
    """Normalise a label so that case, spacing and '&' vs 'and' do not matter."""
    return re.sub(r"[^a-z0-9]", "", text.lower().replace("&", "and"))


class Activity:
    def __init__(self, attrs):
        self.attrs = attrs
        self.id = attrs["id"]
        self.label = attrs.get("label") or self.id
        self.roles = [r.strip() for r in attrs.get("roles", "").split(",") if r.strip()]

    def flag(self, name):
        return self.attrs.get(name, "").lower() == "true"

    @property
    def enabled(self):
        return self.flag("enabled")

    @property
    def pending(self):
        return self.flag("pending")

    @property
    def included(self):
        # "included" is the activity's own flag; the effective value also accounts for its nest.
        return self.attrs.get("effectivelyincluded", self.attrs.get("included", "true")).lower() == "true"

    def to_dict(self):
        return {"id": self.id, "label": self.label, "roles": self.roles,
                "enabled": self.enabled, "pending": self.pending, "included": self.included}


def _unwrap(body):
    """The API returns XML wrapped in a JSON string; return plain XML."""
    body = body.strip()
    if body.startswith('"'):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
    return body.replace('\\"', '"')


def _attrs(text):
    return {k.lower(): html.unescape(v) for k, v in re.findall(r'([\w:.-]+)\s*=\s*"([^"]*)"', text)}


def parse_activities(body):
    """Parse the /events response. The API leaves out activities that are themselves excluded."""
    return [Activity(a) for a in map(_attrs, re.findall(r"<event\b([^>]*)>", _unwrap(body))) if "id" in a]


class DcrClient:
    def __init__(self, graph_id, username, password):
        self.base = f"{API}/graphs/{graph_id}"
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({"Accept": "application/xml"})

    def _call(self, method, path, **kwargs):
        try:
            return self.session.request(method, self.base + path, timeout=30, **kwargs)
        except requests.RequestException as e:
            raise DcrError(f"Could not reach dcrgraphs.net: {e}") from e

    def create_case(self):
        r = self._call("POST", "/sims", json={})
        if r.status_code not in (200, 201):
            raise DcrError(f"Creating a case failed: HTTP {r.status_code}: {r.text[:300]}", r.status_code)
        if r.headers.get("simulationid"):
            return r.headers["simulationid"]
        # Same approach as the course guide: list all cases and take the newest.
        r = self._call("GET", "/sims", params={"filter": "all"})
        ids = [int(i) for i in re.findall(r'<trace[^>]*\bid="(\d+)"', _unwrap(r.text))]
        if not ids:
            raise DcrError(f"Could not find the new case ID: HTTP {r.status_code}: {r.text[:300]}")
        return str(max(ids))

    def activities(self, case_id):
        r = self._call("GET", f"/sims/{case_id}/events", params={"filter": "all"})
        if r.status_code != 200:
            r = self._call("GET", f"/sims/{case_id}/events")
        if r.status_code != 200:
            raise DcrError(f"Reading case {case_id} failed: HTTP {r.status_code}: {r.text[:300]}", r.status_code)
        return parse_activities(r.text)

    def execute(self, case_id, activity_id):
        r = self._call("POST", f"/sims/{case_id}/events/{activity_id}", json={})
        if r.status_code >= 300:
            raise DcrError(f"Executing {activity_id} failed: HTTP {r.status_code}: {r.text[:300]}", r.status_code)
