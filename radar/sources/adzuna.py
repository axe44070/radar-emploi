"""Adzuna — agrégateur multi-sites, API officielle gratuite (developer.adzuna.com).

Couvre la France et plusieurs pays (gb, us, de, ch, sg, ...). Chaque entrée de
`pays` interroge un pays et une ville, avec les requêtes de la langue indiquée.
Sans `pays`, la source interroge la France avec les requêtes françaises.
"""
from __future__ import annotations

import math

from ..core import Job, parse_date, strip_html
from .base import Source

URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
PAGE = 50
MAX_PAGES = 5
CURRENCY = {"gb": "£", "us": "$", "ch": "CHF", "sg": "S$"}


class Adzuna(Source):
    family = "adzuna"

    def _targets(self) -> list[dict]:
        pays = self.cfg.get("pays")
        if pays:
            return pays
        return [{"code": "fr", "lieu": self.cfg.get("lieu", ""), "langue": "fr"}]

    def _queries(self, target: dict) -> list[str]:
        if target.get("requetes"):
            return target["requetes"]
        if target.get("langue", "fr") == "en":
            return self.ctx.queries_en or self.ctx.queries
        return self.ctx.queries_fr or self.ctx.queries

    def fetch(self) -> list[Job]:
        app_id, app_key = self.env("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
        days = max(1, math.ceil((self.ctx.now - self.ctx.since).total_seconds() / 86400))
        jobs = {}
        errors = []
        for target in self._targets():
            country = str(target["code"]).lower()
            try:
                self._fetch_country(app_id, app_key, country, target, days, jobs)
            except Exception as e:  # un pays en échec ne bloque pas les autres
                errors.append(f"{country}/{target.get('lieu', '')}: {e}")
                self.warn(f"pays {country} {target.get('lieu', '')} en échec : {e}")
        if errors and not jobs:
            raise RuntimeError("tous les pays Adzuna ont échoué : " + " | ".join(errors[:3]))
        return list(jobs.values())

    def _fetch_country(self, app_id, app_key, country, target, days, jobs) -> None:
        for q in self._queries(target):
            for page in range(1, MAX_PAGES + 1):
                params = {
                    "app_id": app_id,
                    "app_key": app_key,
                    "what_phrase": q,
                    "max_days_old": days,
                    "results_per_page": PAGE,
                    "sort_by": "date",
                    "content-type": "application/json",
                }
                if target.get("lieu"):
                    params["where"] = target["lieu"]
                rows = self.get_json(URL.format(country=country, page=page), params=params).get("results", [])
                for o in rows:
                    oid = f"{country}:{o.get('id')}"
                    sal = ""
                    if o.get("salary_min"):
                        cur = CURRENCY.get(country, "€")
                        lo, hi = int(o["salary_min"]), int(o.get("salary_max") or o["salary_min"])
                        sal = f"{lo:,} – {hi:,} {cur}".replace(",", " ")
                    jobs[oid] = Job(
                        source="adzuna",
                        source_id=oid,
                        title=strip_html(o.get("title")),
                        company=(o.get("company") or {}).get("display_name", "") or "",
                        location=(o.get("location") or {}).get("display_name", "") or "",
                        url=o.get("redirect_url", ""),
                        published=parse_date(o.get("created")),
                        description=strip_html(o.get("description")),
                        contract=o.get("contract_type") or "",
                        salary=sal,
                    )
                if len(rows) < PAGE:
                    break
