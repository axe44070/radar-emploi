"""France Travail — API officielle « Offres d'emploi v2 » (gratuite, francetravail.io).

Limite de l'API : 150 offres par page, 1 150 au maximum par recherche.
Si une recherche dépasse ce plafond, la période est coupée en deux
récursivement pour ne perdre aucune offre.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..core import Job, parse_date, strip_html
from .base import Source, SourceError

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
PAGE = 150
WINDOW_CAP = 1150
MIN_WINDOW = timedelta(minutes=30)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class FranceTravail(Source):
    family = "francetravail"

    def _token(self) -> str:
        cid, secret = self.env("FRANCETRAVAIL_CLIENT_ID", "FRANCETRAVAIL_CLIENT_SECRET")
        r = self.session.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": cid,
                "client_secret": secret,
                "scope": "api_offresdemploiv2 o2dsoffre",
            },
            timeout=30,
        )
        if r.status_code != 200:
            raise SourceError(f"authentification refusée ({r.status_code}) : {r.text[:200]}")
        return r.json()["access_token"]

    def _page(self, headers, params, start) -> tuple[list[dict], int]:
        p = dict(params, range=f"{start}-{start + PAGE - 1}")
        r = self.session.get(SEARCH_URL, headers=headers, params=p, timeout=30)
        if r.status_code == 204:
            return [], 0
        if r.status_code not in (200, 206):
            raise SourceError(f"recherche en erreur ({r.status_code}) : {r.text[:200]}")
        total = 0
        m = re.search(r"/(\d+)", r.headers.get("Content-Range", ""))
        if m:
            total = int(m.group(1))
        return r.json().get("resultats", []), total

    def _search(self, headers, query, start_dt, end_dt) -> list[dict]:
        params = {
            "motsCles": query,
            "minCreationDate": _fmt(start_dt),
            "maxCreationDate": _fmt(end_dt),
            "sort": 1,
        }
        deps = self.cfg.get("departements") or []
        if deps:
            params["departement"] = ",".join(str(d) for d in deps)
        first, total = self._page(headers, params, 0)
        if total > WINDOW_CAP:
            if end_dt - start_dt > MIN_WINDOW:
                mid = start_dt + (end_dt - start_dt) / 2
                return self._search(headers, query, start_dt, mid) + self._search(headers, query, mid, end_dt)
            self.warn(f"« {query} » : plus de {WINDOW_CAP} offres en 30 min, résultats tronqués")
        results = list(first)
        start = PAGE
        while len(first) == PAGE and start < min(total, WINDOW_CAP):
            first, _ = self._page(headers, params, start)
            results.extend(first)
            start += PAGE
        return results

    def fetch(self) -> list[Job]:
        headers = {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}
        jobs = {}
        for q in self.ctx.queries:
            for o in self._search(headers, q, self.ctx.since, self.ctx.now):
                oid = str(o.get("id"))
                origin = (o.get("origineOffre") or {})
                url = origin.get("urlOrigine") or f"https://candidat.francetravail.fr/offres/recherche/detail/{oid}"
                jobs[oid] = Job(
                    source="francetravail",
                    source_id=oid,
                    title=o.get("intitule", ""),
                    company=(o.get("entreprise") or {}).get("nom", "") or "",
                    location=(o.get("lieuTravail") or {}).get("libelle", "") or "",
                    url=url,
                    published=parse_date(o.get("dateCreation")),
                    description=strip_html(o.get("description")),
                    contract=o.get("typeContratLibelle") or o.get("typeContrat") or "",
                    salary=(o.get("salaire") or {}).get("libelle", "") or "",
                )
        return list(jobs.values())
