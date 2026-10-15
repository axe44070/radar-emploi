"""Modèle d'offre, normalisation de texte et utilitaires de date."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

_TAG_RE = re.compile(r"<[^>]+>")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_LEGAL_SUFFIXES = re.compile(
    r"\b(sas|sasu|sa|sarl|eurl|sca|snc|se|gmbh|ltd|inc|llc|group|groupe|france|holding)\b"
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(value) -> datetime | None:
    """Accepte ISO 8601, RFC 822 (RSS), timestamp en ms ou s."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(ts, timezone.utc)
        s = str(value).strip()
        if s.isdigit():
            return parse_date(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", text))).strip()


def norm(text: str | None) -> str:
    """Minuscules, sans accents, ponctuation -> espaces."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = t.replace("œ", "oe").replace("æ", "ae")
    return _NON_ALNUM.sub(" ", t).strip()


def norm_company(name: str | None) -> str:
    return re.sub(r"\s+", " ", _LEGAL_SUFFIXES.sub(" ", norm(name))).strip()


def city_token(location: str | None) -> str:
    n = norm(location)
    # "75 - Paris 8e" -> "paris" ; "Lyon, France" -> "lyon"
    for tok in n.split():
        if not tok.isdigit():
            return tok
    return n.split()[0] if n else ""


@dataclass
class Job:
    source: str
    source_id: str
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    published: datetime | None = None
    description: str = ""
    contract: str = ""
    salary: str = ""

    @property
    def uid(self) -> str:
        return hashlib.sha1(f"{self.source}|{self.source_id}".encode()).hexdigest()[:16]

    @property
    def fuzzy_key(self) -> str | None:
        company = norm_company(self.company)
        if not company:  # offre anonyme : pas de rapprochement inter-sources
            return None
        return f"{norm(self.title)}|{company}|{city_token(self.location)}"

    @property
    def family(self) -> str:
        """Famille de source ("greenhouse:doctolib" -> "greenhouse")."""
        return self.source.split(":")[0]
