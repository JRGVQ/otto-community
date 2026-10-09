"""Unit tests for the Outlook/Graph adapter — the HTTP layer is mocked, no network.

Covers the Fyxer-parity fixes: reply drafts placed ABOVE the quoted original (HTML),
colored + non-destructive category write-back, delta/list paging, and the self-healing
token refresh (refresh-on-401 + proactive) that keeps the background sync alive.
"""
import json
import urllib.error
import urllib.request
from datetime import timedelta

from app.models.base import utcnow
from app.providers.outlook import OutlookProvider, _hex_to_preset, _text_to_html


class _Acct:
    """Minimal stand-in for a MailAccount (provider methods under test ignore it)."""
    subscription_id = None
    watch_expiry = None
    token_expiry = None


def _provider(monkeypatch, *, get=None, post=None, patch=None, delete=None):
    p = OutlookProvider(_Acct())
    monkeypatch.setattr(p, "_get", get or (lambda path: {}))
    monkeypatch.setattr(p, "_post", post or (lambda path, payload=None: {}))
    monkeypatch.setattr(p, "_patch", patch or (lambda path, payload=None: {}))
    monkeypatch.setattr(p, "_delete", delete or (lambda path: None))
    return p  # real _get_abs is kept, so its base-stripping is exercised


# ── 1a. create_draft: AI reply above preserved quote, as escaped HTML ─────────
def test_create_draft_prepends_reply_above_quote(monkeypatch):
    patches = []

    def post(path, payload=None):
        if "createReply" in path:
            return {"id": "draft-9", "body": {"contentType": "html", "content": "<div>QUOTED ORIGINAL</div>"}}
        return {}

    p = _provider(monkeypatch, post=post, patch=lambda path, payload=None: patches.append((path, payload)))
    did = p.create_draft("t", "RE: x", "Hallo,\nDank.", in_reply_to="msg-1")

    assert did == "draft-9"
    path, payload = patches[-1]
    assert path == "/me/messages/draft-9"
    assert payload["body"]["contentType"] == "HTML"
    content = payload["body"]["content"]
    assert content.index("Dank.") < content.index("QUOTED ORIGINAL")  # reply ABOVE quote
    assert "<br>" in content                                          # newline → <br>


def test_create_draft_wraps_reply_in_verdana(monkeypatch):
    posts = []
    p = _provider(monkeypatch, post=lambda path, payload=None: posts.append(payload) or {"id": "d"})
    p.create_draft("t", "RE: x", "Hallo", in_reply_to=None)
    content = posts[-1]["body"]["content"]
    assert "Verdana Pro" in content and "Hallo" in content


def test_list_folders_skips_system_and_pages(monkeypatch):
    # childFolderCount hoort bij de $select sinds submappen meetellen: zonder dat veld
    # weet list_folders niet waar het nog dieper moet kijken.
    pages = {
        "/me/mailFolders?$top=100&$select=id,displayName,childFolderCount": {
            "value": [{"id": "1", "displayName": "Inbox"}, {"id": "2", "displayName": "Klanten"}],
            "@odata.nextLink": "https://graph/next",
        },
        "https://graph/next": {"value": [{"id": "3", "displayName": "Boekhouding"}]},
    }
    p = _provider(monkeypatch, get=lambda path: pages[path])
    monkeypatch.setattr(p, "_get_abs", lambda path: pages[path])
    names = [f["name"] for f in p.list_folders()]
    assert "Klanten" in names and "Boekhouding" in names and "Inbox" not in names


def test_move_to_folder_posts_move(monkeypatch):
    posts = []
    p = _provider(monkeypatch, post=lambda path, payload=None: posts.append((path, payload)) or {})
    p.move_to_folder("msg-1", "folder-9")
    assert posts[-1] == ("/me/messages/msg-1/move", {"destinationId": "folder-9"})


def test_create_draft_falls_back_to_get_when_body_empty(monkeypatch):
    gets, patches = [], []

    def post(path, payload=None):
        return {"id": "draft-x", "body": {"content": ""}} if "createReply" in path else {}

    def get(path):
        gets.append(path)
        return {"body": {"content": "<div>FETCHED QUOTE</div>"}}

    p = _provider(monkeypatch, get=get, post=post, patch=lambda path, payload=None: patches.append(payload))
    p.create_draft(None, "RE", "Hoi", in_reply_to="m1")

    assert any("draft-x" in g for g in gets)                       # defensive GET happened
    assert "FETCHED QUOTE" in patches[-1]["body"]["content"]


def test_create_draft_escapes_html_injection(monkeypatch):
    captured = {}

    def post(path, payload=None):
        return {"id": "d1", "body": {"content": ""}} if "createReply" in path else {}

    p = _provider(monkeypatch, get=lambda path: {"body": {"content": ""}},
                  post=post, patch=lambda path, payload=None: captured.update(payload))
    p.create_draft(None, "RE", "<script>alert(1)</script>", in_reply_to="m1")

    body = captured["body"]["content"]
    assert "<script>" not in body and "&lt;script&gt;" in body


def test_text_to_html_escapes_and_breaks():
    out = _text_to_html("a < b & c\nline2")
    assert "&lt;" in out and "&amp;" in out and "<br>" in out


# ── 1b. apply_label: colored master category + merge (never clobber) ──────────
def test_apply_label_merges_and_creates_colored_master(monkeypatch):
    posts, patches = [], []

    p = _provider(
        monkeypatch,
        get=lambda path: {"categories": ["Belangrijk"]},  # an existing manual category
        post=lambda path, payload=None: posts.append((path, payload)) or {},
        patch=lambda path, payload=None: patches.append((path, payload)) or {},
    )
    p.apply_label("msg-1", "Te beantwoorden", color="#6C4CE6")

    master = [x for x in posts if "masterCategories" in x[0]]
    assert master and master[0][1]["displayName"] == "Te beantwoorden"
    assert master[0][1]["color"].startswith("preset")
    cats = patches[-1][1]["categories"]
    assert "Belangrijk" in cats and "Te beantwoorden" in cats     # manual category preserved


def test_ensure_master_category_ignores_409(monkeypatch):
    def post(path, payload=None):
        raise urllib.error.HTTPError(path, 409, "exists", {}, None)

    _provider(monkeypatch, post=post).ensure_master_category("X", "#ffffff")  # must not raise


def test_ensure_master_category_swallows_403(app, monkeypatch):
    def post(path, payload=None):
        raise urllib.error.HTTPError(path, 403, "forbidden", {}, None)

    _provider(monkeypatch, post=post).ensure_master_category("X", "#ffffff")  # logged, no raise


def test_hex_to_preset_nearest():
    assert _hex_to_preset("#6C4CE6") == "preset8"    # Otto purple → Purple
    assert _hex_to_preset("#000000") == "preset14"   # Black
    assert _hex_to_preset("#E74856") == "preset0"    # Red (exact)
    assert _hex_to_preset(None) == "preset0"
    assert _hex_to_preset("#6C4CE6FF") == "preset8"  # tolerates #RRGGBBAA width


# ── 1c. paging ───────────────────────────────────────────────────────────────
def test_list_recent_follows_nextlink(monkeypatch):
    pages = {
        "/me/messages?$top=50&$select=id,conversationId,subject,bodyPreview,from,sender,isRead,body,receivedDateTime,internetMessageHeaders": {
            "value": [{"id": "1"}, {"id": "2"}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skip=2",
        },
        "/me/messages?$skip=2": {"value": [{"id": "3"}]},
    }
    seen = []

    def get(path):
        seen.append(path)
        return pages[path]

    out = _provider(monkeypatch, get=get).list_recent(limit=300)
    assert [d.provider_message_id for d in out] == ["1", "2", "3"]
    assert "/me/messages?$skip=2" in seen  # _get_abs stripped the absolute base on page 2


# ── 1d. self-healing token refresh ───────────────────────────────────────────
class _Resp:
    """Context-manager stand-in for an http.client response."""
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self) -> bytes:
        return self._body


def test_send_refreshes_token_on_401_then_retries(monkeypatch):
    p = OutlookProvider(_Acct())
    monkeypatch.setattr(p, "_token", lambda: "tok")
    monkeypatch.setattr(p, "_ensure_fresh_token", lambda: None)
    refreshed = []
    monkeypatch.setattr(p, "refresh_token", lambda: refreshed.append(True))

    calls = []

    def fake_urlopen(req, timeout=None):
        assert timeout, "every Graph call must carry a timeout (urlopen blocks forever without one)"
        calls.append(req.full_url)
        if len(calls) == 1:  # first attempt: token is stale
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)
        return _Resp(json.dumps({"value": ["ok"]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert p._get("/me/messages") == {"value": ["ok"]}
    assert refreshed == [True]  # refreshed exactly once
    assert len(calls) == 2      # original + one retry


def test_send_gives_up_after_single_retry(monkeypatch):
    p = OutlookProvider(_Acct())
    monkeypatch.setattr(p, "_token", lambda: "tok")
    monkeypatch.setattr(p, "_ensure_fresh_token", lambda: None)
    monkeypatch.setattr(p, "refresh_token", lambda: None)

    def always_401(req, timeout=None):
        assert timeout, "every Graph call must carry a timeout (urlopen blocks forever without one)"
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", always_401)
    try:
        p._get("/me/messages")
        raise AssertionError("expected the second 401 to propagate")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401


def test_ensure_fresh_token_refreshes_when_expired(monkeypatch):
    acct = _Acct()
    acct.token_expiry = utcnow() - timedelta(minutes=1)  # already expired
    p = OutlookProvider(acct)
    called = []
    monkeypatch.setattr(p, "refresh_token", lambda: called.append(True))
    p._ensure_fresh_token()
    assert called == [True]


def test_ensure_fresh_token_skips_when_unknown_or_future(monkeypatch):
    calls = []
    p = OutlookProvider(_Acct())  # token_expiry None → unknown → skip
    monkeypatch.setattr(p, "refresh_token", lambda: calls.append(True))
    p._ensure_fresh_token()

    acct = _Acct()
    acct.token_expiry = utcnow() + timedelta(hours=1)  # still valid → skip
    p2 = OutlookProvider(acct)
    monkeypatch.setattr(p2, "refresh_token", lambda: calls.append(True))
    p2._ensure_fresh_token()
    assert calls == []


def test_fetch_new_drains_pages_then_returns_deltalink(monkeypatch):
    pages = {
        "/me/mailFolders/inbox/messages/delta?$select=id,conversationId,subject,bodyPreview,from,sender,isRead,body,receivedDateTime,internetMessageHeaders": {
            "value": [{"id": "a", "from": {}}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/delta-page-2",
        },
        "/delta-page-2": {
            "value": [{"id": "b", "from": {}}],
            "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta-next",
        },
    }
    msgs, cursor = _provider(monkeypatch, get=lambda path: pages[path]).fetch_new(None)
    assert [m.provider_message_id for m in msgs] == ["a", "b"]
    assert cursor == "https://graph.microsoft.com/v1.0/delta-next"


def test_delta_baseline_selects_full_fields_so_cursor_carries_them(monkeypatch):
    """Regression: the baseline cursor is reused by fetch_new, and Graph bakes the
    `$select` into the deltaLink. If the baseline drains with `$select=id`, every later
    fetch_new returns id-only messages (no subject/sender/body), which the classifier then
    files as spam. The baseline must use the SAME select as fetch_new."""
    from app.providers.outlook import _MSG_SELECT

    seen: list[str] = []

    def get(path):
        seen.append(path)
        return {"value": [{"id": "old"}], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/base-next"}

    cursor = _provider(monkeypatch, get=get).delta_baseline()
    assert cursor == "https://graph.microsoft.com/v1.0/base-next"
    # The initial delta request must carry the full field selection, not just id.
    assert seen[0] == f"/me/mailFolders/inbox/messages/delta?$select={_MSG_SELECT}"
    assert "subject" in seen[0] and "from" in seen[0] and "body" in seen[0]


# ── Self-heal: detect a narrow ($select=id) cursor + re-fetch one message ─────
def test_deltalink_is_narrow_detects_id_only_cursor():
    from app.providers.outlook import _MSG_SELECT, deltalink_is_narrow

    assert deltalink_is_narrow("https://graph.microsoft.com/v1.0/delta?%24select=id&%24deltatoken=abc")
    assert deltalink_is_narrow("https://graph.microsoft.com/v1.0/delta?$select=id&$deltatoken=abc")
    # A cursor built with the full field selection is fine.
    full = f"https://graph.microsoft.com/v1.0/delta?$select={_MSG_SELECT}&$deltatoken=abc"
    assert not deltalink_is_narrow(full)
    # No cursor / no $select at all → not narrow (nothing to heal).
    assert not deltalink_is_narrow(None)
    assert not deltalink_is_narrow("https://graph.microsoft.com/v1.0/delta?$deltatoken=abc")


def test_fetch_message_returns_full_dto(monkeypatch):
    def get(path):
        assert "$select=" in path and "subject" in path and "body" in path
        return {"id": "m1", "subject": "Echt onderwerp",
                "from": {"emailAddress": {"name": "Klant", "address": "k@x.be"}},
                "bodyPreview": "hallo", "body": {"contentType": "text", "content": "hallo daar"}}

    dto = _provider(monkeypatch, get=get).fetch_message("m1")
    assert dto is not None
    assert dto.subject == "Echt onderwerp" and dto.sender_email == "k@x.be"
    assert dto.body == "hallo daar"


# ── Reserved-scope handling (MSAL rejects offline_access/openid/profile) ───────

def test_graph_msal_scopes_excludes_reserved():
    from app.providers.outlook import GRAPH_MSAL_SCOPES, GRAPH_SCOPES

    assert "offline_access" in GRAPH_SCOPES  # kept for the consent UI + eigen-app docs
    assert not ({"offline_access", "openid", "profile"} & set(GRAPH_MSAL_SCOPES))
    assert "Mail.ReadWrite" in GRAPH_MSAL_SCOPES  # the real delegated scopes survive


def test_normal_connect_scopes_have_no_admin_consent_scope():
    """The self-service connect must not request User.ReadBasic.All — it forces an admin
    approval wall that bounces normal users. It lives only in the admin-consent set."""
    from app.providers.outlook import GRAPH_MSAL_ADMIN_SCOPES, GRAPH_MSAL_SCOPES

    assert "User.ReadBasic.All" not in GRAPH_MSAL_SCOPES
    assert "User.ReadBasic.All" in GRAPH_MSAL_ADMIN_SCOPES
    assert "Mail.ReadWrite" in GRAPH_MSAL_ADMIN_SCOPES  # base scopes still included


def test_refresh_token_does_not_pass_reserved_scopes(app, monkeypatch):
    # Regression: MSAL raises "You cannot use any scope value that is reserved" when a
    # reserved scope is passed explicitly. refresh_token() must hand it the filtered list,
    # or every background token refresh fails ~1h after connect and the inbox stalls.
    import sys
    import types
    from types import SimpleNamespace

    captured = {}

    class _FakeMsalApp:
        def __init__(self, *a, **k):
            pass

        def acquire_token_by_refresh_token(self, refresh_token, scopes):
            captured["scopes"] = scopes
            return {"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600}

    # msal is a prod-only dependency (commented out of requirements.txt to dodge a
    # cryptography pin conflict), so it isn't importable in CI. Inject a stand-in module
    # so refresh_token()'s `from msal import ConfidentialClientApplication` resolves to
    # our fake — no real msal install needed.
    fake_msal = types.ModuleType("msal")
    fake_msal.ConfidentialClientApplication = _FakeMsalApp
    monkeypatch.setitem(sys.modules, "msal", fake_msal)
    monkeypatch.setattr("app.connectors.microsoft_app", lambda org: ("cid", "tid", "secret"))
    monkeypatch.setattr("app.security.crypto.decrypt", lambda v: v)
    monkeypatch.setattr("app.security.crypto.encrypt", lambda v: v)

    acct = SimpleNamespace(refresh_token_enc="rt-enc", access_token_enc=None,
                           token_expiry=None, user=SimpleNamespace(organization=None))
    p = OutlookProvider(acct)
    monkeypatch.setattr(p, "_auth_account", lambda: acct)

    p.refresh_token()

    assert "scopes" in captured  # MSAL was actually called
    assert not ({"offline_access", "openid", "profile"} & set(captured["scopes"]))
    assert acct.access_token_enc == "new-at"  # the refreshed token was stored


# ── HTML-weergave: zuinig met Graph in de paginarender ───────────────────────
def test_inline_attachment_fetches_metadata_first_then_only_the_match(monkeypatch):
    """Een listing mét contentBytes downloadt élke bijlage van de mail; voor één
    cid-plaatje hoort dat één metadata-GET + één item-GET te zijn."""
    import base64

    paths = []

    def get(path, timeout=None):
        paths.append(path)
        if path.endswith("?$select=id,contentId,contentType"):
            return {"value": [
                {"id": "att-1", "contentId": "<logo@mail>", "contentType": "image/png"},
                {"id": "att-2", "contentId": "<foto@mail>", "contentType": "image/jpeg"},
            ]}
        assert path.endswith("/attachments/att-2"), "alleen de gevraagde bijlage ophalen"
        return {"id": "att-2", "contentType": "image/jpeg",
                "contentBytes": base64.b64encode(b"JPEGBYTES").decode()}

    p = _provider(monkeypatch, get=get)
    result = p.get_inline_attachment("m-1", "foto@mail")
    assert result == (b"JPEGBYTES", "image/jpeg")
    assert len(paths) == 2 and "$select=id,contentId,contentType" in paths[0]


def test_inline_attachment_unknown_cid_costs_only_the_listing(monkeypatch):
    calls = []

    def get(path, timeout=None):
        calls.append(path)
        return {"value": [{"id": "att-1", "contentId": "<logo@mail>", "contentType": "image/png"}]}

    p = _provider(monkeypatch, get=get)
    assert p.get_inline_attachment("m-1", "bestaat-niet") is None
    assert len(calls) == 1  # geen item-fetch voor een cid die er niet is


def test_body_html_backfill_uses_a_short_timeout(monkeypatch):
    """De backfill draait ín de paginarender: bij een trage Graph moet hij snel
    opgeven (tekstweergave) in plaats van de worker de volle 20 s vast te houden."""
    seen = {}

    def get(path, timeout=None):
        seen["timeout"] = timeout
        return {"body": {"contentType": "html", "content": "<p>Hoi</p>"}}

    p = _provider(monkeypatch, get=get)
    assert p.get_body_html("m-1") == "<p>Hoi</p>"
    assert seen["timeout"] is not None and seen["timeout"] <= 5


def test_send_honours_a_per_call_timeout(monkeypatch):
    p = OutlookProvider(_Acct())
    monkeypatch.setattr(p, "_token", lambda: "tok")
    monkeypatch.setattr(p, "_ensure_fresh_token", lambda: None)
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        return _R()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    p._get("/me/messages/x?$select=body", timeout=3)
    assert seen["timeout"] == 3


# ── Robuuste ingestie: null-velden en partiële delta-items ────────────────────
def test_to_dto_survives_explicit_null_fields(monkeypatch):
    """Graph stuurt geselecteerde velden soms als expliciete null mee; dict.get(key,
    default) beschermt daar niet tegen. Zo'n mail mag nooit met None-afzender of
    None-onderwerp de database in (dat gaf lege inbox-rijen én een initials-crash)."""
    p = _provider(monkeypatch)
    dto = p._to_dto({"id": "m1", "subject": None, "from": None, "sender": None,
                     "body": None, "bodyPreview": None, "isRead": False})
    assert dto.sender == "" and dto.sender_email == ""
    assert dto.subject == "(geen onderwerp)"
    assert dto.body == "" and dto.snippet == ""


def test_to_dto_falls_back_to_the_sender_field(monkeypatch):
    """Bij 'verzonden namens'-mail is `from` soms leeg en staat de echte afzender
    alleen in `sender`."""
    p = _provider(monkeypatch)
    dto = p._to_dto({"id": "m1", "subject": "Hoi",
                     "from": {"emailAddress": {}},
                     "sender": {"emailAddress": {"name": "Jan", "address": "jan@klant.be"}}})
    assert dto.sender == "Jan" and dto.sender_email == "jan@klant.be"


def test_fetch_new_refetches_a_partial_delta_item(monkeypatch):
    """Delta levert soms een partieel item (bv. alleen een isRead-wijziging) zonder
    afzender/onderwerp. fetch_new haalt het bericht dan één keer volledig op."""
    def get(path):
        if "/messages/m1?" in path:
            return {"id": "m1", "subject": "Echte mail",
                    "from": {"emailAddress": {"name": "Jan", "address": "jan@klant.be"}},
                    "body": {"contentType": "text", "content": "Volledige inhoud"}}
        return {"value": [{"id": "m1", "isRead": True}],
                "@odata.deltaLink": "https://graph/delta2"}

    p = _provider(monkeypatch, get=get)
    msgs, cursor = p.fetch_new(None)
    assert cursor == "https://graph/delta2"
    assert msgs[0].sender_email == "jan@klant.be"
    assert msgs[0].subject == "Echte mail" and msgs[0].body == "Volledige inhoud"


def test_fetch_new_survives_a_failing_refetch(app, monkeypatch):
    """Kan het volledige bericht niet opgehaald worden, dan komt het partiële item
    alsnog (leeg maar veilig) binnen — de sync mag er niet op stranden."""
    def get(path):
        if "/messages/m1?" in path:
            raise RuntimeError("Graph plat")
        return {"value": [{"id": "m1", "isRead": True}], "@odata.deltaLink": "d2"}

    p = _provider(monkeypatch, get=get)
    msgs, _cursor = p.fetch_new(None)
    assert msgs[0].provider_message_id == "m1"
    assert msgs[0].sender_email == "" and msgs[0].removed is False
