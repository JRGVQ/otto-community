"""Microsoft Planner via Graph (scope `Tasks.ReadWrite`) + directory lookup (`User.ReadBasic.All`).

Turns an Otto task into a real Planner task in a chosen plan/bucket, optionally assigned to a
colleague picked from the Microsoft 365 directory. Microsoft To Do is a *personal* list and
cannot assign work to other people — Planner is the team-task surface that can, which is why
task assignment routes through here. Degrades cleanly when Graph is not configured: the caller
keeps the task app-local.

All endpoints here use the signed-in user's own delegated token (via OutlookProvider), so they
are not mailbox-scoped — any of the user's connected Outlook accounts can drive them.
"""
from __future__ import annotations

from urllib.parse import quote

from flask import current_app

from app.providers.exceptions import ProviderNotConfigured

# A Planner assignment must carry this literal odata type and a non-empty orderHint.
_ASSIGN_TYPE = "#microsoft.graph.plannerAssignment"
_USER_SELECT = "id,displayName,mail,userPrincipalName"


class PlannerProvider:
    def __init__(self, account) -> None:
        self.account = account

    @classmethod
    def is_configured(cls) -> bool:
        return bool(current_app.config.get("GRAPH_CONFIGURED"))

    def _outlook(self):
        from app.providers.outlook import OutlookProvider

        return OutlookProvider(self.account)

    # ── Reads (populate the task picker) ─────────────────────────────────────
    def list_plans(self) -> list[dict]:
        """Planner plans the signed-in user can see (across the groups they belong to)."""
        data = self._outlook()._get("/me/planner/plans")
        return [
            {"id": p["id"], "title": p.get("title") or "(naamloos plan)"}
            for p in data.get("value", [])
            if p.get("id")
        ]

    def list_buckets(self, plan_id: str) -> list[dict]:
        data = self._outlook()._get(f"/planner/plans/{quote(plan_id)}/buckets")
        return [
            {"id": b["id"], "name": b.get("name") or "(naamloze bucket)"}
            for b in data.get("value", [])
            if b.get("id")
        ]

    def search_users(self, query: str, limit: int = 25) -> list[dict]:
        """Directory users for the assignee picker.

        Filtered server-side by name/UPN prefix when a query is given; a tenant that
        rejects the filter falls back to the first directory page so the picker is never
        empty just because of a query-shape quirk.
        """
        ob = self._outlook()
        base = f"/users?$select={_USER_SELECT}&$top={int(limit)}"
        q = (query or "").strip()
        path = base
        if q:
            expr = f"startswith(displayName,'{q}') or startswith(userPrincipalName,'{q}')"
            path = f"{base}&$filter={quote(expr)}"
        try:
            data = ob._get(path)
        except Exception:  # noqa: BLE001 — filter unsupported/restricted → plain page
            try:
                data = ob._get(base)
            except Exception:  # noqa: BLE001
                # No directory access (User.ReadBasic.All not admin-consented) → empty picker,
                # never an error. Assigning to yourself / known users still works.
                return []
        out = []
        for u in data.get("value", []):
            if not u.get("id"):
                continue
            out.append({
                "id": u["id"],
                "name": u.get("displayName") or u.get("userPrincipalName") or "?",
                "email": u.get("mail") or u.get("userPrincipalName") or "",
            })
        return out

    # ── Write (create the task in Planner) ───────────────────────────────────
    def create_task(self, plan_id: str, title: str, *, bucket_id: str | None = None,
                    assignee_ids: list[str] | None = None, due_iso: str | None = None) -> str:
        """Create a Planner task; return its id. Raises if Graph is not configured."""
        if not self.is_configured():
            raise ProviderNotConfigured("Microsoft Planner (Graph) is niet geconfigureerd.")
        payload: dict = {"planId": plan_id, "title": (title or "").strip()[:255]}
        if bucket_id:
            payload["bucketId"] = bucket_id
        if assignee_ids:
            payload["assignments"] = {
                uid: {"@odata.type": _ASSIGN_TYPE, "orderHint": " !"}
                for uid in assignee_ids if uid
            }
        if due_iso:
            payload["dueDateTime"] = due_iso
        created = self._outlook()._post("/planner/tasks", payload)
        return created.get("id", "")
