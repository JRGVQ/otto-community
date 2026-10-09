"""Knowledge retrieval for grounding AI drafts (RAG) — twee bronnen, één uitkomst.

1. De kennisbank die de klant zelf invult (`app/knowledge_base.py`). Werkt overal,
   ook zonder Microsoft 365, en gaat vóór: het is gecureerde tekst, geen zoekresultaat.
2. De gekoppelde SharePoint-bibliotheek, live doorzocht via de Graph Search API
   (`/search/query`, entityType driveItem). Niets wordt geïndexeerd of bewaard:
   documenten blijven in M365 en alleen korte fragmenten bereiken de LLM. App-only
   (client-credentials), zodat het op de achtergrond werkt voor elk concept.

`retrieve()` NEVER raises into the draft path — every failure degrades to [] (the draft
is then simply ungrounded), mirroring the defensive posture of write-back/sync.
"""
from __future__ import annotations

import json
import re
import urllib.request

from flask import current_app

GRAPH_SEARCH_URL = "https://graph.microsoft.com/v1.0/search/query"
SNIPPET_MAX = 300  # keep snippets short — protects the per-seat AI budget and trims injection surface


def _build_query(subject: str, body: str, path: str | None) -> str:
    """KQL query: a little free text from the mail, scoped to the connected library."""
    text = f"{subject or ''} {(body or '')[:200]}"
    text = re.sub(r'["\\\r\n]+', " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:200]
    q = text or "*"
    if path:
        q = f'{q} path:"{path}"'
    return q


def _app_token(cfg) -> str | None:
    """App-only Graph token (client credentials), same pattern as the platform mailer."""
    from msal import ConfidentialClientApplication

    client = ConfidentialClientApplication(
        cfg["GRAPH_KNOWLEDGE_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{cfg['GRAPH_KNOWLEDGE_TENANT_ID']}",
        client_credential=cfg["GRAPH_KNOWLEDGE_CLIENT_SECRET"],
    )
    token = client.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    return token.get("access_token")


def _post_search(token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        GRAPH_SEARCH_URL, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 — trusted Graph host
        return json.loads(resp.read().decode())


def _parse_hits(data: dict, top_n: int) -> list[dict]:
    out: list[dict] = []
    for resp in data.get("value") or []:
        for container in resp.get("hitsContainers") or []:
            for hit in container.get("hits") or []:
                res = hit.get("resource") or {}
                title = res.get("name") or "Document"
                url = res.get("webUrl") or ""
                snippet = (hit.get("summary") or res.get("description") or "").strip()
                snippet = re.sub(r"\s+", " ", snippet)[:SNIPPET_MAX]
                out.append({"title": title, "snippet": snippet, "url": url})
                if len(out) >= top_n:
                    return out
    return out[:top_n]


def _sharepoint(org, subject: str, body: str, top_n: int) -> list[dict]:
    """De gekoppelde SharePoint-bibliotheek, of [] als er geen is of iets faalt."""
    try:
        cfg = current_app.config
        if not cfg.get("KNOWLEDGE_CONFIGURED"):
            return []
        from app.extensions import db
        from app.models import KnowledgeSource

        src = (
            db.session.query(KnowledgeSource)
            .filter_by(organization_id=org.id, status="connected")
            .first()
        )
        if not src:
            return []
        token = _app_token(cfg)
        if not token:
            return []
        request = {
            "entityTypes": ["driveItem"],
            "query": {"queryString": _build_query(subject, body, src.library_path or src.site_url)},
            "from": 0,
            "size": top_n,
        }
        if cfg.get("GRAPH_KNOWLEDGE_REGION"):  # required for app-only search
            request["region"] = cfg["GRAPH_KNOWLEDGE_REGION"]
        return _parse_hits(_post_search(token, {"requests": [request]}), top_n)
    except Exception:  # noqa: BLE001 — grounding is best-effort; a failure must not break drafting
        current_app.logger.info("knowledge.retrieve skipped for org %s", getattr(org, "id", "?"))
        return []


def retrieve(org, subject: str, body: str, top_n: int = 3, account=None) -> list[dict]:
    """Up to `top_n` {title, snippet, url} dicts grounding this reply, or [] when there
    is nothing to ground it with. Never raises.

    De eigen kennisbank gaat voor op SharePoint: wat de klant expliciet heeft
    ingevuld weegt zwaarder dan wat een zoekopdracht toevallig bovenaan zet. Blijft
    er ruimte over, dan vult SharePoint aan."""
    if org is None:
        return []
    from app import knowledge_base

    own = knowledge_base.search(org, subject, body, top_n=top_n, account=account)
    if len(own) >= top_n:
        return own
    return own + _sharepoint(org, subject, body, top_n - len(own))
