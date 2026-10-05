"""Pages carrières d'entreprises via leur logiciel de recrutement (API publiques).

Ces API renvoient TOUTES les offres ouvertes de l'entreprise : on ne dépend
d'aucun moteur de recherche, c'est la source la plus exhaustive pour vos
entreprises cibles.
"""
from __future__ import annotations

from ..core import Job, parse_date, strip_html
from .base import Source, SourceError


class Ats(Source):
    """Une instance par entreprise : nom de source = "<ats>:<id>"."""

    def __init__(self, name, cfg, ctx):
        super().__init__(name, cfg, ctx)
        self.ats = cfg["ats"].lower()
        self.slug = str(cfg["id"])
        self.company = cfg.get("nom") or self.slug
        self.family = self.ats

    def _job(self, sid, title, location, url, published=None, desc="", contract="") -> Job:
        return Job(source=self.name, source_id=str(sid), title=title or "", company=self.company,
                   location=location or "", url=url or "", published=parse_date(published),
                   description=strip_html(desc), contract=contract or "")

    def fetch(self) -> list[Job]:
        fn = getattr(self, f"_{self.ats}", None)
        if not fn:
            raise SourceError(f"ATS inconnu : {self.ats}")
        return fn()

    def _greenhouse(self):
        data = self.get_json(f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs", params={"content": "true"})
        return [self._job(o["id"], o.get("title"), (o.get("location") or {}).get("name"), o.get("absolute_url"),
                          o.get("first_published") or o.get("updated_at"), o.get("content", ""))
                for o in data.get("jobs", [])]

    def _lever(self):
        host = "api.eu.lever.co" if self.cfg.get("region") == "eu" else "api.lever.co"
        data = self.get_json(f"https://{host}/v0/postings/{self.slug}", params={"mode": "json"})
        if not isinstance(data, list):
            raise SourceError("réponse Lever inattendue")
        return [self._job(o["id"], o.get("text"), (o.get("categories") or {}).get("location"), o.get("hostedUrl"),
                          o.get("createdAt"), o.get("descriptionPlain", ""),
                          (o.get("categories") or {}).get("commitment"))
                for o in data]

    def _ashby(self):
        data = self.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{self.slug}")
        return [self._job(o["id"], o.get("title"), o.get("location"), o.get("jobUrl"),
                          o.get("publishedAt"), o.get("descriptionPlain", ""), o.get("employmentType"))
                for o in data.get("jobs", []) if o.get("isListed", True)]

    def _smartrecruiters(self):
        out, offset = [], 0
        while True:
            data = self.get_json(f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings",
                                 params={"limit": 100, "offset": offset})
            rows = data.get("content", [])
            for o in rows:
                loc = o.get("location") or {}
                out.append(self._job(o["id"], o.get("name"),
                                     ", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
                                     f"https://jobs.smartrecruiters.com/{self.slug}/{o['id']}",
                                     o.get("releasedDate"),
                                     contract=(o.get("typeOfEmployment") or {}).get("label")))
            offset += len(rows)
            if not rows or offset >= data.get("totalFound", 0):
                return out

    def _workable(self):
        data = self.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{self.slug}")
        return [self._job(o.get("shortcode"), o.get("title"),
                          ", ".join(x for x in (o.get("city"), o.get("country")) if x),
                          o.get("url") or o.get("shortlink"),
                          o.get("published_on") or o.get("created_at"),
                          contract=o.get("employment_type"))
                for o in data.get("jobs", [])]
