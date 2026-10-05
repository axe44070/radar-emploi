from __future__ import annotations

from .adzuna import Adzuna
from .apec import Apec
from .ats import Ats
from .base import Context, NotConfigured, Source, SourceError
from .feeds import Page, Rss
from .francetravail import FranceTravail
from .google_jobs import GoogleJobs

SEARCH_SOURCES = {
    "francetravail": FranceTravail,
    "apec": Apec,
    "adzuna": Adzuna,
    "google_jobs": GoogleJobs,
}


def configured_sources(cfg: dict) -> list[tuple[str, type[Source], dict]]:
    """Liste (nom, classe, config) des sources actives."""
    s = cfg.get("sources") or {}
    out = []
    for key, cls in SEARCH_SOURCES.items():
        sc = s.get(key) or {}
        if sc.get("actif", False):
            out.append((key, cls, sc))
    for e in s.get("entreprises") or []:
        out.append((f"{e['ats'].lower()}:{e['id']}", Ats, e))
    for e in s.get("rss") or []:
        out.append((f"rss:{e.get('nom') or e['url']}", Rss, e))
    for e in s.get("pages") or []:
        out.append((f"page:{e.get('nom') or e['url']}", Page, e))
    return out


__all__ = ["Context", "NotConfigured", "SourceError", "configured_sources"]
