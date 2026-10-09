"""Microsoft To Do via Graph (`Tasks.ReadWrite`).

Two-way sync surface for Otto's task list: lists, tasks (with delta), and steps
(checklistItems). The pull (Graph → Otto) and conflict logic live in
`app/tasks/todo_sync.py`; this adapter is the thin HTTP layer that speaks todoTask
JSON. Reuses OutlookProvider for auth + the refresh-on-401 transport, so it inherits
the self-healing token. Degrades to app-local tasks when Graph is absent.
"""
from __future__ import annotations

import urllib.error
from datetime import date, datetime

from flask import current_app

from app.providers.exceptions import ProviderNotConfigured


def _if_match(etag: str | None) -> dict | None:
    return {"If-Match": etag} if etag else None


_TZ = "Europe/Brussels"
# Simple recurrence vocabulary <-> Graph recurrencePatternType.
_RECUR_TO_GRAPH = {
    "daily": ("daily", None),
    "weekdays": ("weekly", ["monday", "tuesday", "wednesday", "thursday", "friday"]),
    "weekly": ("weekly", None),
    "monthly": ("absoluteMonthly", None),
    "yearly": ("absoluteYearly", None),
}
_GRAPH_TO_RECUR = {"daily": "daily", "weekly": "weekly",
                   "absoluteMonthly": "monthly", "absoluteYearly": "yearly"}
_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday"}


class TodoProvider:
    def __init__(self, account) -> None:
        self.account = account

    @classmethod
    def is_configured(cls) -> bool:
        return bool(current_app.config.get("GRAPH_CONFIGURED"))

    def _outlook(self):
        from app.providers.outlook import OutlookProvider

        return OutlookProvider(self.account)

    # ── Lists ────────────────────────────────────────────────────────────────
    def list_lists(self) -> list[dict]:
        ob = self._outlook()
        out, path = [], "/me/todo/lists"
        while path:
            data = ob._get_abs(path)
            for lst in data.get("value", []):
                out.append({
                    "id": lst["id"],
                    "name": lst.get("displayName") or "Taken",
                    "is_default": lst.get("wellknownListName") == "defaultList",
                })
            path = data.get("@odata.nextLink")
        return out

    def default_list_id(self) -> str:
        for lst in self.list_lists():
            if lst["is_default"]:
                return lst["id"]
        lists = self.list_lists()
        return lists[0]["id"] if lists else self.create_list("Taken")["id"]

    def create_list(self, name: str) -> dict:
        created = self._outlook()._post("/me/todo/lists", {"displayName": name[:128]})
        return {"id": created.get("id", ""), "name": created.get("displayName", name)}

    # ── Tasks ────────────────────────────────────────────────────────────────
    def list_tasks(self, list_id: str, delta_link: str | None = None) -> tuple[list[dict], str | None]:
        """Return (raw todoTask dicts, new delta link). Drains nextLink pages.

        Removed tasks come back as `{'id':…, '@removed':…}` so the caller can delete
        the local mirror. First call (no delta_link) walks the delta endpoint, which
        returns everything plus a deltaLink for next time.
        """
        ob = self._outlook()
        path = delta_link or f"/me/todo/lists/{list_id}/tasks/delta"
        items: list[dict] = []
        while True:
            data = ob._get_abs(path)
            items.extend(data.get("value", []))
            nxt = data.get("@odata.nextLink")
            if nxt:
                path = nxt
                continue
            return items, data.get("@odata.deltaLink")

    def get_task(self, list_id: str, task_id: str) -> dict:
        return self._outlook()._get(f"/me/todo/lists/{list_id}/tasks/{task_id}")

    def create_task(self, list_id: str, fields: dict) -> dict:
        if not self.is_configured():
            raise ProviderNotConfigured("Microsoft To Do (Graph) is niet geconfigureerd.")
        return self._outlook()._post(f"/me/todo/lists/{list_id}/tasks", self._task_payload(fields))

    def update_task(self, list_id: str, task_id: str, fields: dict, etag: str | None = None) -> dict:
        """PATCH with optimistic concurrency: send If-Match when we hold an etag, and on a
        412 (the task changed remotely since) refetch the fresh etag and retry once — so our
        edited fields land on the current version without clobbering concurrent remote edits."""
        path = f"/me/todo/lists/{list_id}/tasks/{task_id}"
        payload = self._task_payload(fields)
        try:
            return self._outlook()._patch(path, payload, _if_match(etag))
        except urllib.error.HTTPError as exc:
            if exc.code == 412 and etag:
                fresh = self.get_task(list_id, task_id).get("@odata.etag")
                return self._outlook()._patch(path, payload, _if_match(fresh))
            raise

    def delete_task(self, list_id: str, task_id: str, etag: str | None = None) -> None:
        path = f"/me/todo/lists/{list_id}/tasks/{task_id}"
        try:
            self._outlook()._delete(path, _if_match(etag))
        except urllib.error.HTTPError as exc:
            if exc.code == 412:  # changed remotely — the user's intent is to delete; retry unconditionally
                self._outlook()._delete(path)
            else:
                raise

    # ── Steps (checklistItems) ────────────────────────────────────────────────
    def list_steps(self, list_id: str, task_id: str) -> list[dict]:
        data = self._outlook()._get(f"/me/todo/lists/{list_id}/tasks/{task_id}/checklistItems")
        return data.get("value", [])

    def create_step(self, list_id: str, task_id: str, title: str, done: bool = False) -> dict:
        return self._outlook()._post(
            f"/me/todo/lists/{list_id}/tasks/{task_id}/checklistItems",
            {"displayName": title[:255], "isChecked": bool(done)},
        )

    def update_step(self, list_id: str, task_id: str, step_id: str, *, title: str | None = None,
                    done: bool | None = None) -> dict:
        payload: dict = {}
        if title is not None:
            payload["displayName"] = title[:255]
        if done is not None:
            payload["isChecked"] = bool(done)
        return self._outlook()._patch(
            f"/me/todo/lists/{list_id}/tasks/{task_id}/checklistItems/{step_id}", payload)

    def delete_step(self, list_id: str, task_id: str, step_id: str) -> None:
        self._outlook()._delete(f"/me/todo/lists/{list_id}/tasks/{task_id}/checklistItems/{step_id}")

    # ── Attachments (taskFileAttachment) ──────────────────────────────────────
    def list_attachments(self, list_id: str, task_id: str) -> list[dict]:
        data = self._outlook()._get(f"/me/todo/lists/{list_id}/tasks/{task_id}/attachments")
        return data.get("value", [])

    def attachment_content(self, list_id: str, task_id: str, att_id: str) -> bytes:
        """Raw bytes of an attachment (the Graph `$value` stream)."""
        return self._outlook()._send("GET", f"/me/todo/lists/{list_id}/tasks/{task_id}/attachments/{att_id}/$value")

    def create_attachment(self, list_id: str, task_id: str, name: str, content_type: str, content: bytes) -> dict:
        import base64

        payload = {
            "@odata.type": "#microsoft.graph.taskFileAttachment",
            "name": (name or "bestand")[:255],
            "contentType": content_type or "application/octet-stream",
            "contentBytes": base64.b64encode(content).decode(),
        }
        return self._outlook()._post(f"/me/todo/lists/{list_id}/tasks/{task_id}/attachments", payload)

    def delete_attachment(self, list_id: str, task_id: str, att_id: str) -> None:
        self._outlook()._delete(f"/me/todo/lists/{list_id}/tasks/{task_id}/attachments/{att_id}")

    # ── Payload mapping ───────────────────────────────────────────────────────
    def _task_payload(self, f: dict) -> dict:
        """Normalised Otto fields -> Graph todoTask body. Only includes provided keys."""
        payload: dict = {}
        if "title" in f:
            payload["title"] = (f.get("title") or "").strip()[:255]
        if "notes" in f:
            payload["body"] = {"content": f.get("notes") or "", "contentType": "text"}
        if "important" in f:
            payload["importance"] = "high" if f.get("important") else "normal"
        if "status" in f and f.get("status"):
            payload["status"] = f["status"]
        if "due_date" in f:
            payload["dueDateTime"] = self._date_field(f.get("due_date"))
        if "reminder_at" in f:
            rem = f.get("reminder_at")
            payload["isReminderOn"] = bool(rem)
            payload["reminderDateTime"] = self._date_field(rem)
        if "recurrence" in f:
            payload["recurrence"] = self._recurrence_payload(
                f.get("recurrence"), f.get("due_date"), f.get("recurrence_interval"))
        return payload

    @staticmethod
    def _date_field(value) -> dict | None:
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            iso = f"{value.isoformat()}T08:00:00"
        elif isinstance(value, datetime):
            iso = value.replace(microsecond=0).isoformat()
        else:
            iso = str(value)
        return {"dateTime": iso, "timeZone": _TZ}

    @staticmethod
    def _recurrence_payload(recurrence: str | None, start, interval: int | None = 1) -> dict | None:
        if not recurrence or recurrence not in _RECUR_TO_GRAPH:
            return None
        rtype, days = _RECUR_TO_GRAPH[recurrence]
        pattern: dict = {"type": rtype, "interval": max(int(interval or 1), 1)}
        if days:
            pattern["daysOfWeek"] = days
        start_date = start.isoformat() if isinstance(start, (date, datetime)) else (start or date.today().isoformat())
        return {"pattern": pattern, "range": {"type": "noEnd", "startDate": str(start_date)[:10]}}

    @staticmethod
    def recurrence_from_graph(recurrence: dict | None) -> str | None:
        if not recurrence:
            return None
        pattern = recurrence.get("pattern") or {}
        rtype = pattern.get("type")
        if rtype == "weekly" and _WEEKDAYS.issuperset({d.lower() for d in pattern.get("daysOfWeek", [])}) \
                and len(pattern.get("daysOfWeek", [])) == 5:
            return "weekdays"
        return _GRAPH_TO_RECUR.get(rtype)

    @staticmethod
    def interval_from_graph(recurrence: dict | None) -> int:
        try:
            return max(int((recurrence or {}).get("pattern", {}).get("interval", 1)), 1)
        except (TypeError, ValueError):
            return 1
