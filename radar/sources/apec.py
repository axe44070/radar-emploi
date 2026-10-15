"""APEC — offres cadres. Utilise le service JSON du site apec.fr (non officiel).

Si l'APEC modifie son site, la source passera en erreur et une alerte
« source en panne » sera envoyée : rien ne disparaît silencieusement.
"""
from __future__ import annotations

from ..core import Job, parse_date, strip_html
from .base import Source, SourceError

URL = "https://www.apec.fr/cms/webservices/rechercheOffre"
DETAIL = "https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/{}"
PAGE = 100
MAX_PAGES = 10


class Apec(Source):
    family = "apec"

    def _payload(self, query: str, start: int) -> dict:
        return {
            "motsCles": query,
            "lieux": [],
            "fonctions": [],
            "statutPoste": [],
            "typesContrat": [],
            "typesConvention": ["143684", "143685", "143686", "143687"],
            "niveauxExperience": [],
            "idsEtablissement": [],
            "secteursActivite": [],
            "typesTeletravail": [],
            "idNomZonesDeplacement": [],
            "positionNumbersExcluded": [],
            "typeClient": "CADRE",
            "sorts": [{"type": "DATE", "direction": "DESCENDING"}],
            "pagination": {"range": PAGE, "startIndex": start},
            "activeFiltre": True,
            "pointGeolocDeReference": {"distance": 0},
        }

    def fetch(self) -> list[Job]:
        jobs = {}
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Origin": "https://www.apec.fr", "Referer": "https://www.apec.fr/"}
        for q in self.ctx.queries:
            for page in range(MAX_PAGES):
                r = self.session.post(URL, json=self._payload(q, page * PAGE), headers=headers, timeout=30)
                r.raise_for_status()
                data = r.json()
                if "resultats" not in data:
                    raise SourceError("format de réponse APEC inattendu (le site a peut-être changé)")
                rows = data.get("resultats") or []
                too_old = False
                for o in rows:
                    oid = str(o.get("numeroOffre") or o.get("id"))
                    pub = parse_date(o.get("datePublication"))
                    if pub and pub < self.ctx.since:
                        too_old = True
                        continue
                    jobs[oid] = Job(
                        source="apec",
                        source_id=oid,
                        title=o.get("intitule", ""),
                        company=o.get("nomCommercial", "") or "",
                        location=o.get("lieuTexte", "") or "",
                        url=DETAIL.format(oid),
                        published=pub,
                        description=strip_html(o.get("texteOffre")),
                        salary=o.get("salaireTexte", "") or "",
                    )
                if too_old or len(rows) < PAGE:
                    break
        return list(jobs.values())
