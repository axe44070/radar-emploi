"""Filtrage des offres : profils, exclusions, localisation, priorités."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .core import Job, norm, norm_company

REMOTE_WORDS = ("teletravail", "remote", "full remote", "a distance", "home office")


def _compile(words) -> re.Pattern | None:
    parts = sorted({norm(w) for w in (words or []) if norm(w)}, key=len, reverse=True)
    if not parts:
        return None
    alt = "|".join(re.escape(p) for p in parts)
    return re.compile(rf"(?<![a-z0-9])(?:{alt})(?![a-z0-9])")


@dataclass
class Match:
    profiles: list[str]
    score: int
    to_check: bool  # retenue uniquement via la description
    priority: bool


class Matcher:
    def __init__(self, cfg: dict):
        self.profiles = {}
        for key, p in (cfg.get("profils") or {}).items():
            self.profiles[key] = (
                p.get("libelle", key),
                _compile(p.get("mots_cles_titre")),
                _compile(p.get("mots_cles_description")),
            )
        self.exclude = _compile(cfg.get("exclure_titre"))
        loc = cfg.get("localisation") or {}
        self.places = [norm(x) for x in loc.get("lieux") or [] if norm(x)]
        self.keep_remote = loc.get("garder_teletravail", True)
        self.keep_unknown = loc.get("garder_lieu_inconnu", True)
        self.priority = {norm_company(c) for c in cfg.get("entreprises_prioritaires") or []}

    def location_ok(self, job: Job) -> bool:
        if not self.places:
            return True
        loc = norm(job.location)
        if not loc:
            return self.keep_unknown
        if any(re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", loc) for p in self.places):
            return True
        if self.keep_remote:
            blob = loc + " " + norm(job.title)
            return any(w in blob for w in REMOTE_WORDS)
        return False

    def match(self, job: Job) -> Match | None:
        title = norm(job.title)
        if not title:
            return None
        if self.exclude and self.exclude.search(title):
            return None
        if not self.location_ok(job):
            return None
        desc = norm(job.description)
        hits, labels, via_desc = 0, [], False
        for label, title_re, desc_re in self.profiles.values():
            if title_re and title_re.search(title):
                labels.append(label)
                hits += len(set(title_re.findall(title)))
            elif desc_re and desc and desc_re.search(desc):
                labels.append(label)
                via_desc = True
        if not labels:
            return None
        priority = norm_company(job.company) in self.priority if job.company else False
        score = hits * 2 + (3 if priority else 0) + (0 if via_desc and not hits else 1)
        return Match(labels, score, to_check=(via_desc and hits == 0), priority=priority)
