"""Google Jobs via SerpAPI (payant au-delà du quota gratuit).

Google Jobs agrège LinkedIn, Indeed, Welcome to the Jungle, HelloWork,
Cadremploi, sites carrières… C'est la façon propre de couvrir ces sites
sans les scraper directement (ce que leurs conditions d'utilisation interdisent).
"""
from __future__ import annotations

import hashlib

from ..core import Job
from .base import Source, SourceError

URL = "https://serpapi.com/search.json"


class GoogleJobs(Source):
    family = "google_jobs"

    def fetch(self) -> list[Job]:
        (key,) = self.env("SERPAPI_KEY")
        jobs = {}
        queries = self.cfg.get("requetes") or self.ctx.queries
        for q in queries:
            token = None
            for _ in range(int(self.cfg.get("pages_max", 2))):
                params = {"engine": "google_jobs", "q": q, "location": self.cfg.get("lieu", "France"),
                          "gl": "fr", "hl": "fr", "api_key": key}
                if token:
                    params["next_page_token"] = token
                data = self.get_json(URL, params=params)
                if data.get("error") and "hasn't returned any results" not in data["error"]:
                    raise SourceError(data["error"])
                for o in data.get("jobs_results", []) or []:
                    apply = (o.get("apply_options") or [{}])[0]
                    oid = o.get("job_id") or hashlib.sha1(
                        f"{o.get('title')}|{o.get('company_name')}|{o.get('location')}".encode()).hexdigest()
                    oid = hashlib.sha1(oid.encode()).hexdigest()[:20]  # job_id Google est très long
                    ext = o.get("detected_extensions") or {}
                    jobs[oid] = Job(
                        source="google_jobs",
                        source_id=oid,
                        title=o.get("title", ""),
                        company=o.get("company_name", ""),
                        location=o.get("location", ""),
                        url=apply.get("link") or o.get("share_link", ""),
                        description=(o.get("description") or "")[:2000],
                        contract=ext.get("schedule_type", ""),
                        salary=ext.get("salary", ""),
                    )
                token = (data.get("serpapi_pagination") or {}).get("next_page_token")
                if not token:
                    break
        return list(jobs.values())
