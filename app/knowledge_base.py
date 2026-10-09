"""Bedrijfskennis die de klant zelf invoert — beheren, uitlezen en matchen.

Waarom dit naast `app/ai/knowledge.py` bestaat: die module doorzoekt SharePoint live
via Graph en vereist dus Microsoft 365. Deze bron werkt overal, ook voor een
Gmail-klant, ten koste van het feit dat Otto de tekst wél bewaart.

Het matchen gebeurt bewust met termoverlap, niet met embeddings. Drie redenen: het
kost geen enkele AI-token (en Otto rekent per stoel af), het heeft geen extra
dependency nodig, en het is deterministisch — een klant die zich afvraagt waaróm een
blok is meegestuurd, krijgt een antwoord dat te volgen is. Voor een handvol
kennisblokken per organisatie is dat ruim genoeg; pas bij duizenden documenten zou
een vectorindex zich terugverdienen, en dan nog is SharePoint daar de betere plek.

Niets hier mag de conceptketen breken: `search()` en `extract_text()` geven een lege
uitkomst terug in plaats van te falen, net als `retrieve()`.
"""
from __future__ import annotations

import html
import re
import zipfile
from io import BytesIO

from app.extensions import db
from app.models import KnowledgeEntry

# Een fragment mag kort zijn: het beschermt het AI-budget per stoel en beperkt meteen
# hoeveel tekst een aanvaller via een geüpload bestand in de prompt zou krijgen.
SNIPPET_MAX = 400
BODY_MAX = 20_000          # per blok bewaarde tekst
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_UNPACKED_BYTES = 20 * 1024 * 1024   # zipbom-grens voor .docx
MIN_SCORE = 2              # onder deze score is de overlap toeval, geen relevantie

# Woorden die in elke mail staan en dus niets zeggen over het onderwerp. Nederlands,
# Engels en Frans door elkaar — een Belgische inbox is dat ook.
_STOPWORDS = {
    "aan", "als", "bij", "dag", "dan", "dat", "deze", "die", "dit", "door", "een", "eens",
    "en", "er", "goede", "graag", "haar", "had", "heb", "hebben", "heeft", "het",
    "hier", "hoe", "hun", "ik", "in", "is", "je", "kan", "kunnen", "maar", "me", "meer",
    "met", "mij", "mijn", "moet", "na", "naar", "niet", "nog", "nu", "of", "om", "ons",
    "onze", "ook", "op", "over", "te", "tot", "uw", "van", "veel", "voor", "want",
    "wat", "we", "wel", "werd", "wij", "wil", "worden", "zal", "ze", "zijn", "zou",
    "beste", "hallo", "hey", "dank", "danku", "bedankt", "groeten", "vriendelijke",
    "and", "are", "can", "for", "from", "have", "hello", "hi", "how", "the", "thanks",
    "that", "this", "was", "will", "with", "would", "you", "your", "our", "please",
    "avec", "bonjour", "dans", "des", "est", "les", "merci", "nous", "pour", "vous",
}


# Achtervoegsels die in het Nederlands (en genoeg Engels) alleen de vorm veranderen,
# niet het onderwerp. Langste eerst, en er blijft altijd een leesbare stam over.
_SUFFIXES = ("ingen", "ering", "heden", "ing", "heid", "en", "es", "s", "e")
_STEM_MIN = 4      # korter dan dit stammen we niet — dan blijft er niets over
_PREFIX_MIN = 5    # "lever" mag "leveringstermijn" raken; "lev" mag dat niet


# ── Tokeniseren & scoren ─────────────────────────────────────────────────────
def _stem(word: str) -> str:
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= _STEM_MIN:
            return word[: -len(suf)]
    return word


def _tokens(text: str) -> set[str]:
    """Betekenisdragende stammen, kleingeschreven. Cijfers blijven staan: een
    ordernummer of een postcode is juist wél onderscheidend."""
    raw = re.findall(r"[\w-]{3,}", (text or "").lower(), flags=re.UNICODE)
    return {_stem(w) for w in raw if w not in _STOPWORDS}


def _matches(term: str, stems: set[str]) -> bool:
    """Raakt deze zoekterm dit blok? Naast een exacte treffer telt ook een
    prefixtreffer, want het Nederlands plakt woorden aan elkaar: wie naar
    "levering" vraagt, bedoelt ook "leveringstermijn" en "leveringskosten".
    Vandaar de ondergrens van vijf tekens — anders raakt "kost" ook "kostuum"."""
    if term in stems:
        return True
    if len(term) < _PREFIX_MIN:
        return False
    return any(s.startswith(term) or (len(s) >= _PREFIX_MIN and term.startswith(s)) for s in stems)


def _weight(term: str) -> int:
    """Een lang woord is specifieker dan een kort. "garantietermijn" zegt echt iets;
    "datum" staat in de helft van alle mails."""
    return 2 if len(term) >= 7 else 1


def _score(terms: set[str], entry: KnowledgeEntry) -> int:
    """Hoe hard dit blok de mail raakt. De titel weegt het zwaarst: wie een blok
    'Leveringstermijnen' noemt, zegt daarmee waar het over gaat."""
    title, body = _tokens(entry.title), _tokens(entry.body)
    total = 0
    for term in terms:
        w = _weight(term)
        if _matches(term, title):
            total += 3 * w
        elif _matches(term, body):
            total += w
    return total


def _snippet(entry: KnowledgeEntry, terms: set[str]) -> str:
    """Een kort venster rond de eerste rake term, zodat het fragment het antwoord
    bevat en niet alleen de aanhef van het document."""
    body = re.sub(r"\s+", " ", entry.body or "").strip()
    if len(body) <= SNIPPET_MAX:
        return body
    lowered = body.lower()
    # De vroegste treffer, niet de eerste die de set toevallig oplevert — anders
    # verschilt het fragment tussen twee runs met exact dezelfde invoer.
    hits = [pos for pos in (lowered.find(t) for t in terms) if pos >= 0]
    if not hits:
        return body[:SNIPPET_MAX]
    hit = min(hits)
    start = max(0, hit - SNIPPET_MAX // 3)
    return ("…" if start else "") + body[start:start + SNIPPET_MAX] + "…"


def for_org(org, account=None) -> list[KnowledgeEntry]:
    """Organisatiebrede blokken + die van deze mailbox. Zonder mailbox alleen de
    organisatiebrede — zo lekt de kennis van support@ nooit in een persoonlijk concept."""
    if org is None:
        return []
    q = db.session.query(KnowledgeEntry).filter(
        KnowledgeEntry.organization_id == org.id,
        KnowledgeEntry.enabled.is_(True),
    )
    if account is not None:
        q = q.filter((KnowledgeEntry.account_id.is_(None)) | (KnowledgeEntry.account_id == account.id))
    else:
        q = q.filter(KnowledgeEntry.account_id.is_(None))
    return q.order_by(KnowledgeEntry.id).all()


def _count_use(entries: list[KnowledgeEntry]) -> None:
    """Tel dat deze blokken een concept onderbouwd hebben — atomair in SQL, want twee
    gelijktijdige concepten zouden elkaars verhoging anders overschrijven (dezelfde
    fout die de verbruiksmeting ooit te laag liet tellen).

    Bewust géén commit: `search()` draait binnen de transactie van de aanroeper. Rolt
    die terug, dan hoort de teller ook niet te bewegen."""
    try:
        db.session.query(KnowledgeEntry).filter(
            KnowledgeEntry.id.in_([e.id for e in entries])
        ).update({KnowledgeEntry.used_count: KnowledgeEntry.used_count + 1},
                 synchronize_session="fetch")
    except Exception:  # noqa: BLE001 — een teller is nooit een concept waard
        pass


def search(org, subject: str, body: str, top_n: int = 3, account=None) -> list[dict]:
    """De best passende kennisblokken als {title, snippet, url}-dicts — hetzelfde
    contract als de SharePoint-hits, zodat `retrieve()` ze gewoon kan samenvoegen.
    Geeft [] terug bij twijfel; grondslag is een bonus, nooit een voorwaarde.

    Deze functie doet zelf geen commit of rollback: ze wordt aangeroepen middenin het
    schrijven van een concept, en de transactie is daar van de conceptketen."""
    try:
        terms = _tokens(f"{subject or ''} {(body or '')[:1000]}")
        if not terms:
            return []
        scored = [(s, e) for e in for_org(org, account) if (s := _score(terms, e)) >= MIN_SCORE]
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        picked = [entry for _s, entry in scored[:top_n]]
    except Exception:  # noqa: BLE001 — grondslag is best-effort, een concept mag er nooit op stuklopen
        return []
    if not picked:
        return []
    _count_use(picked)
    return [{"title": e.title, "snippet": _snippet(e, terms), "url": ""} for e in picked]


# ── Tekst uit een geüpload bestand ───────────────────────────────────────────
def _docx_text(data: bytes) -> str:
    """Tekst uit een .docx zonder externe bibliotheek: het bestand is een zip met
    word/document.xml erin. Bewust met een regex over de XML in plaats van met een
    XML-parser — een geüpload bestand is niet te vertrouwen, en zo is er geen parser
    die entiteiten kan uitvouwen."""
    with zipfile.ZipFile(BytesIO(data)) as zf:
        if sum(i.file_size for i in zf.infolist()) > MAX_UNPACKED_BYTES:
            return ""
        try:
            xml = zf.read("word/document.xml").decode("utf-8", "replace")
        except KeyError:
            return ""
    # Per alinea de tekstruns samenvoegen, dan pas de alinea's met een newline —
    # anders plakt "Levertermijn: 5 werkdagen" aan de volgende zin vast en matcht
    # er een term die er in het document niet naast stond.
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
    lines = ["".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S)) for p in paragraphs]
    return html.unescape("\n".join(line for line in lines if line.strip()))


def _pdf_text(data: bytes) -> str:
    """PDF alleen als pypdf geïnstalleerd is — optioneel, net als de andere providers.
    Zonder de bibliotheek zegt de uploadroute eerlijk dat dit formaat niet kan."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    reader = PdfReader(BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_text(filename: str, data: bytes) -> tuple[str, str | None]:
    """(tekst, foutmelding). Een lege tekst zonder fout betekent: gelukt, maar er stond
    niets bruikbaars in (een gescande pdf bijvoorbeeld)."""
    if len(data) > MAX_UPLOAD_BYTES:
        return "", "Het bestand is groter dan 2 MB."
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    try:
        if ext in {"txt", "md", "csv"}:
            text = data.decode("utf-8", "replace")
        elif ext == "docx":
            text = _docx_text(data)
        elif ext == "pdf":
            text = _pdf_text(data)
            if not text.strip():
                return "", ("Deze pdf bevat geen uitleesbare tekst (of pypdf ontbreekt op de server). "
                            "Plak de tekst hieronder, of upload een .docx of .txt.")
        else:
            return "", "Ondersteunde bestanden: .txt, .md, .csv, .docx en .pdf."
    except Exception:  # noqa: BLE001 — een stuk bestand mag een 500 nooit waard zijn
        return "", "Dit bestand kon niet gelezen worden."
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:BODY_MAX], None


# ── Beheer ───────────────────────────────────────────────────────────────────
def create(org, *, title: str, body: str, account=None, source: str = "text",
           filename: str | None = None) -> KnowledgeEntry | None:
    title, body = (title or "").strip()[:200], (body or "").strip()[:BODY_MAX]
    if not (title and body):
        return None
    entry = KnowledgeEntry(
        organization_id=org.id, account_id=account.id if account is not None else None,
        title=title, body=body, source=source, filename=(filename or None),
    )
    db.session.add(entry)
    db.session.commit()
    return entry
