"""Vérifie la VRAIE configuration (config.yaml) sur des titres réalistes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from radar.core import Job
from radar.matching import Matcher
from radar.sources import Context
from radar.sources.adzuna import Adzuna

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
M = Matcher(CFG)
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)


def kept(title, loc="Paris", company="Banque X"):
    return M.match(Job("x", "1", title, company, loc)) is not None


# ---- offres à GARDER (premier poste, CDI, bonnes zones)
@pytest.mark.parametrize("title,loc", [
    ("Analyste Financier Junior H/F", "75 - Paris 8e"),
    ("Junior Financial Analyst", "Paris"),
    ("Investment Banking Analyst - M&A", "London"),
    ("M&A Analyst (Graduate)", "Paris La Défense"),
    ("Analyste Private Equity", "75 - PARIS 08"),
    ("FP&A Analyst", "92 - Courbevoie"),
    ("Analyste Risques de Crédit", "92 - Puteaux"),
    ("KYC Analyst", "Frankfurt am Main"),
    ("Graduate Programme 2027 - Banking", "Luxembourg"),
    ("Middle Office Analyst", "New York, NY"),
    ("Sales Trader Junior", "Paris"),
    ("Sales Analyst - Capital Markets", "Geneva"),
    ("Courtier en marchés H/F", "75 - Paris 9e"),
    ("Trading Assistant Fixed Income", "Singapore"),
    ("Structuring Analyst Equity Derivatives", "Hong Kong"),
    ("Jeune diplômé - Conseiller clientèle", "75 - Paris"),
    ("Analyste Marchés Financiers", ""),                  # lieu inconnu : gardé
])
def test_kept(title, loc):
    assert kept(title, loc), f"{title!r} ({loc}) aurait dû être retenue"


# ---- offres à ÉCARTER
@pytest.mark.parametrize("title,loc,why", [
    ("Stage Analyste Financier", "Paris", "stage"),
    ("Alternance - Analyste crédit", "Paris", "alternance"),
    ("Summer Analyst - Investment Banking", "London", "stage d'été"),
    ("Investment Banking Intern", "London", "stage"),
    ("V.I.E Analyste financier", "Singapore", "VIE"),
    ("Senior Financial Analyst", "Paris", "séniorité"),
    ("Directeur Financier", "Paris", "séniorité"),
    ("Head of Capital Markets", "London", "séniorité"),
    ("Vice President - M&A", "New York", "séniorité"),
    ("Responsable Middle Office", "Paris", "séniorité"),
    ("Risk Manager", "Paris", "séniorité"),
    ("Analyste Financier Junior", "Lyon", "zone"),
    ("Junior Financial Analyst", "Manchester", "zone"),
    ("Junior Financial Analyst - Remote", "Berlin", "télétravail non voulu"),
    ("Développeur Python", "Paris", "hors profil"),
    ("Chargé de recrutement", "Paris", "hors profil"),
    ("Juriste droit des affaires", "Paris", "hors profil"),
])
def test_dropped(title, loc, why):
    assert not kept(title, loc), f"{title!r} ({loc}) aurait dû être écartée : {why}"


def test_profiles_labels():
    m = M.match(Job("x", "1", "Sales Trader Junior", "B", "Paris"))
    assert m.profiles == ["Marchés et sales"]
    m = M.match(Job("x", "1", "Junior Financial Analyst", "B", "Paris"))
    assert m.profiles == ["Finance"]


# ---- Adzuna multi-pays
class _Resp:
    status_code = 200

    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


class _Sess:
    def __init__(self, fail_country=None):
        self.calls, self.fail_country = [], fail_country

    def get(self, url, **kw):
        country = url.split("/jobs/")[1].split("/")[0]
        self.calls.append((country, kw["params"]["what_phrase"], kw["params"].get("where")))
        if country == self.fail_country:
            raise ConnectionError("boom")
        n = len(self.calls)
        return _Resp({"results": [{"id": f"{country}{n}", "title": "Junior Financial Analyst",
                                   "company": {"display_name": "Bank"}, "location": {"display_name": "X"},
                                   "redirect_url": "https://a/b", "created": NOW.isoformat(),
                                   "salary_min": 40000, "salary_max": 50000}]})


def _ctx(sess):
    return Context(sess, ["fr q", "en q"], NOW - timedelta(days=2), NOW, queries_fr=["fr q"], queries_en=["en q"])


def test_adzuna_multi_country_uses_right_language(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "i"); monkeypatch.setenv("ADZUNA_APP_KEY", "k")
    sess = _Sess()
    cfg = CFG["sources"]["adzuna"]
    jobs = Adzuna("adzuna", cfg, _ctx(sess)).fetch()
    by_country = {}
    for country, q, where in sess.calls:
        by_country.setdefault(country, set()).add(q)
    assert by_country["fr"] == {"fr q"}
    assert by_country["gb"] == {"en q"} and by_country["us"] == {"en q"}
    assert {c for c, _, _ in sess.calls} == {"fr", "gb", "us", "de", "ch", "sg"}
    assert len(jobs) == len(sess.calls) and any("£" in j.salary for j in jobs)


def test_adzuna_one_country_failing_does_not_block_others(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "i"); monkeypatch.setenv("ADZUNA_APP_KEY", "k")
    ctx = _ctx(_Sess(fail_country="gb"))
    jobs = Adzuna("adzuna", CFG["sources"]["adzuna"], ctx).fetch()
    assert jobs and not any("gb" in j.source_id for j in jobs)
    assert any("gb" in w for w in ctx.warnings)


def test_adzuna_all_countries_failing_raises(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "i"); monkeypatch.setenv("ADZUNA_APP_KEY", "k")

    class Down(_Sess):
        def get(self, url, **kw):
            raise ConnectionError("down")

    with pytest.raises(RuntimeError):
        Adzuna("adzuna", CFG["sources"]["adzuna"], _ctx(Down())).fetch()


def test_adzuna_legacy_config_still_works(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "i"); monkeypatch.setenv("ADZUNA_APP_KEY", "k")
    sess = _Sess()
    Adzuna("adzuna", {"lieu": "Paris"}, _ctx(sess)).fetch()
    assert {c for c, _, _ in sess.calls} == {"fr"}
