"""Tests hors-ligne : toutes les API sont simulées."""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from radar.core import Job
from radar.matching import Matcher
from radar.notify import Notifier
from radar.runner import run
from radar.sources import Context
from radar.sources.ats import Ats
from radar.sources.francetravail import FranceTravail

ROOT = Path(__file__).resolve().parent.parent
BASE_CFG = yaml.safe_load((ROOT / "tests" / "config_test.yaml").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 5, 7, 30, tzinfo=timezone.utc)  # lundi 9h30 à Paris


class Resp:
    def __init__(self, data=None, status=200, headers=None, text=None):
        self._data, self.status_code, self.headers = data, status, headers or {}
        self.text = text if text is not None else json.dumps(data)
        self.content = self.text.encode()

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Associe un motif d'URL à une fonction (method, url, kw) -> Resp."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def _go(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for pat, fn in self.routes.items():
            if re.search(pat, url):
                return fn(method, url, kw)
        raise ConnectionError(f"pas de route pour {url}")

    def get(self, url, **kw):
        return self._go("GET", url, **kw)

    def post(self, url, **kw):
        return self._go("POST", url, **kw)


def ft_offer(i, title, company="ACME", city="75 - Paris 8e", hours_ago=2):
    return {"id": f"FT{i}", "intitule": title, "entreprise": {"nom": company},
            "lieuTravail": {"libelle": city}, "dateCreation": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "description": "Poste en CDI", "typeContratLibelle": "CDI"}


def apec_offer(i, title, company, city="Paris 08 - 75"):
    return {"numeroOffre": f"A{i}", "intitule": title, "nomCommercial": company, "lieuTexte": city,
            "datePublication": (NOW - timedelta(hours=3)).isoformat(), "texteOffre": "..."}


def make_routes(ft_offers, apec_offers, tg_log, apec_fail=False):
    def token(m, u, kw):
        return Resp({"access_token": "tok"})

    def ft_search(m, u, kw):
        q = kw["params"]["motsCles"]
        rows = [o for o in ft_offers if q.split()[0].lower() in o["intitule"].lower()]
        return Resp({"resultats": rows}, status=200 if rows else 204,
                    headers={"Content-Range": f"offres 0-{len(rows)}/{len(rows)}"})

    def apec(m, u, kw):
        if apec_fail:
            return Resp({"message": "boom"}, status=500)
        q = kw["json"]["motsCles"]
        return Resp({"totalCount": 1, "resultats": [o for o in apec_offers if q.split()[0].lower() in o["intitule"].lower()]})

    def telegram(m, u, kw):
        tg_log.append(kw["json"]["text"])
        return Resp({"ok": True})

    return {r"oauth2/access_token": token, r"offresdemploi/v2/offres/search": ft_search,
            r"apec\.fr": apec, r"api\.telegram\.org": telegram}


@pytest.fixture
def cfg():
    c = copy.deepcopy(BASE_CFG)
    c["sources"]["adzuna"]["actif"] = False
    return c


@pytest.fixture
def env(monkeypatch):
    for k, v in {"FRANCETRAVAIL_CLIENT_ID": "id", "FRANCETRAVAIL_CLIENT_SECRET": "s",
                 "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}.items():
        monkeypatch.setenv(k, v)
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------- filtrage
def test_matching_rules(cfg):
    m = Matcher(cfg)
    j = lambda t, loc="Paris", d="": Job("x", "1", t, "Co", loc, description=d)
    assert m.match(j("Contrôleur de Gestion H/F")).profiles == ["Finance"]
    assert m.match(j("CONTROLEUR DE GESTION industriel")).profiles == ["Finance"]
    assert m.match(j("DAF groupe")).profiles == ["Finance"]
    assert m.match(j("Responsable DAFNE logistique")) is None          # mot entier
    assert m.match(j("Stage contrôleur de gestion")) is None            # exclusion
    assert m.match(j("Juriste droit social")).profiles == ["Juridique"]
    assert m.match(j("HRBP / Juriste social")).profiles == ["Juridique", "RH"]
    assert m.match(j("FP&A Manager")).profiles == ["Finance"]
    assert m.match(j("Développeur Python")) is None


def test_location_filter(cfg):
    cfg["localisation"]["lieux"] = ["Paris", "92"]
    m = Matcher(cfg)
    j = lambda loc, t="Juriste": Job("x", "1", t, "Co", loc)
    assert m.match(j("75 - Paris 8e"))
    assert m.match(j("92 - Nanterre"))
    assert m.match(j("Lyon")) is None
    assert m.match(j("Lyon (télétravail)"))
    assert m.match(j(""))                       # lieu inconnu : gardé
    cfg["localisation"]["garder_teletravail"] = False
    assert Matcher(cfg).match(j("Full remote")) is None


def test_description_match_flagged(cfg):
    cfg["profils"]["rh"]["mots_cles_description"] = ["paie"]
    m = Matcher(cfg).match(Job("x", "1", "Assistant administratif", "Co", "Paris", description="gestion de la paie"))
    assert m and m.to_check and m.profiles == ["RH"]


# ---------------------------------------------------------------- France Travail
def test_francetravail_splits_window_over_1150(monkeypatch):
    monkeypatch.setenv("FRANCETRAVAIL_CLIENT_ID", "id")
    monkeypatch.setenv("FRANCETRAVAIL_CLIENT_SECRET", "s")
    since = NOW - timedelta(hours=8)
    # 2 offres par minute sur 8 h = 960... on simule 3 000 offres réparties uniformément
    offers = [ft_offer(i, "Comptable", hours_ago=8 * i / 3000) for i in range(3000)]

    def search(m, u, kw):
        p = kw["params"]
        lo = datetime.fromisoformat(p["minCreationDate"].replace("Z", "+00:00"))
        hi = datetime.fromisoformat(p["maxCreationDate"].replace("Z", "+00:00"))
        rows = [o for o in offers if lo <= datetime.fromisoformat(o["dateCreation"]) <= hi]
        a, b = map(int, p["range"].split("-"))
        assert b <= 1149
        return Resp({"resultats": rows[a:b + 1]}, status=206,
                    headers={"Content-Range": f"offres {a}-{b}/{len(rows)}"})

    s = FakeSession({"oauth2": lambda *a: Resp({"access_token": "t"}), "offres/search": search})
    jobs = FranceTravail("francetravail", {}, Context(s, ["comptable"], since, NOW)).fetch()
    assert len(jobs) == 3000  # aucune perte malgré le plafond de 1 150


# ---------------------------------------------------------------- bout en bout
def test_end_to_end(tmp_path, cfg, env):
    tg = []
    ft = [ft_offer(1, "Contrôleur de gestion H/F", "Danone SA"), ft_offer(2, "Juriste corporate", "Vinci"),
          ft_offer(3, "Développeur Java", "X")]
    ap = [apec_offer(1, "Contrôleur de gestion H/F", "DANONE")]  # même offre que FT1
    s = FakeSession(make_routes(ft, ap, tg))

    # 1er passage : import silencieux + récapitulatif
    r1 = run(cfg, tmp_path, session=s, now=NOW, notifier=Notifier(cfg, s))
    assert r1["new"] == 0 and r1["initial"] == 2 and r1["merged"] == 1
    assert [m for m in tg if "Première synchronisation" in m] and not [m for m in tg if m.startswith("🆕")]
    jobs = json.loads((tmp_path / "jobs.json").read_text())
    assert len(jobs) == 2
    danone = next(j for j in jobs if "Danone" in j["company"])
    assert {x["source"] for x in danone["sources"]} == {"francetravail", "apec"}

    # 2e passage, 1 h plus tard : une vraie nouvelle offre -> une alerte
    tg.clear()
    ft.append(ft_offer(4, "DRH groupe", "L'Oréal", hours_ago=0))
    r2 = run(cfg, tmp_path, session=s, now=NOW + timedelta(hours=1), notifier=Notifier(cfg, s))
    assert r2["new"] == 1
    alerts = [m for m in tg if m.startswith("🆕")]
    assert len(alerts) == 1 and "DRH groupe" in alerts[0]

    # 3e passage : rien de neuf -> aucune alerte
    tg.clear()
    r3 = run(cfg, tmp_path, session=s, now=NOW + timedelta(hours=2), notifier=Notifier(cfg, s))
    assert r3["new"] == 0 and not [m for m in tg if m.startswith("🆕")]


def test_source_failure_alerts_once_and_recovers(tmp_path, cfg, env):
    tg = []
    ft = [ft_offer(1, "Juriste", "A")]
    ok = FakeSession(make_routes(ft, [apec_offer(1, "Juriste", "B")], tg))
    ko = FakeSession(make_routes(ft, [], tg, apec_fail=True))
    run(cfg, tmp_path, session=ok, now=NOW, notifier=Notifier(cfg, ok))
    tg.clear()
    for h in (1, 2, 3):
        run(cfg, tmp_path, session=ko, now=NOW + timedelta(hours=h), notifier=Notifier(cfg, ko))
    assert len([m for m in tg if "Source en panne" in m]) == 1
    tg.clear()
    run(cfg, tmp_path, session=ok, now=NOW + timedelta(hours=4), notifier=Notifier(cfg, ok))
    assert any("rétablie" in m for m in tg)


def test_overlap_window_catches_up_after_outage(tmp_path, cfg, env):
    """Une offre publiée pendant une panne est rattrapée au passage suivant."""
    tg = []
    ft = [ft_offer(1, "Juriste", "A")]
    s = FakeSession(make_routes(ft, [], tg))
    run(cfg, tmp_path, session=s, now=NOW, notifier=Notifier(cfg, s))
    tg.clear()
    ft.append(ft_offer(2, "Responsable paie", "B", hours_ago=-3))  # publiée à NOW+3h
    since_seen = []
    orig = s.routes[r"offresdemploi/v2/offres/search"]

    def spy(m, u, kw):
        since_seen.append(kw["params"]["minCreationDate"])
        return orig(m, u, kw)

    s.routes[r"offresdemploi/v2/offres/search"] = spy
    r = run(cfg, tmp_path, session=s, now=NOW + timedelta(hours=10), notifier=Notifier(cfg, s))
    assert r["new"] == 1
    assert since_seen[0] <= (NOW - timedelta(hours=47)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_daily_digest_and_weekly(tmp_path, cfg, env, monkeypatch):
    for k, v in {"SMTP_HOST": "smtp.x", "SMTP_USER": "u", "SMTP_PASSWORD": "p", "EMAIL_TO": "me@x"}.items():
        monkeypatch.setenv(k, v)
    sent = []
    monkeypatch.setattr(Notifier, "email", lambda self, subj, body: sent.append(subj))
    tg = []
    s = FakeSession(make_routes([ft_offer(1, "Juriste", "A")], [], tg))
    run(cfg, tmp_path, session=s, now=NOW, notifier=Notifier(cfg, s))          # lundi 9h30 Paris
    assert any("Offres du 05/10/2026" in x for x in sent)
    assert any("Bilan de la semaine" in m for m in tg)
    sent.clear(); tg.clear()
    run(cfg, tmp_path, session=s, now=NOW + timedelta(hours=1), notifier=Notifier(cfg, s))
    assert not sent and not any("Bilan" in m for m in tg)                     # une seule fois par jour/semaine


def test_missing_credentials_is_not_a_failure(tmp_path, cfg, monkeypatch):
    for k in ("FRANCETRAVAIL_CLIENT_ID", "FRANCETRAVAIL_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    s = FakeSession({r"apec\.fr": lambda *a: Resp({"resultats": []})})
    r = run(cfg, tmp_path, session=s, now=NOW, notifier=Notifier(cfg, s))
    assert r["sources"]["francetravail"]["status"] == "non_configuree"
    assert r["sources"]["apec"]["status"] == "ok"


# ---------------------------------------------------------------- ATS
@pytest.mark.parametrize("ats,route,payload,expected_url", [
    ("greenhouse", "greenhouse.io", {"jobs": [{"id": 1, "title": "Juriste", "location": {"name": "Paris"},
                                               "absolute_url": "https://g/1", "updated_at": "2026-10-01T00:00:00Z"}]}, "https://g/1"),
    ("lever", "lever.co", [{"id": "a", "text": "Juriste", "categories": {"location": "Paris"},
                            "hostedUrl": "https://l/a", "createdAt": 1727000000000}], "https://l/a"),
    ("ashby", "ashbyhq", {"jobs": [{"id": "b", "title": "Juriste", "location": "Paris", "jobUrl": "https://a/b"}]}, "https://a/b"),
    ("smartrecruiters", "smartrecruiters", {"content": [{"id": "c", "name": "Juriste", "location": {"city": "Paris"}}],
                                            "totalFound": 1}, "https://jobs.smartrecruiters.com/co/c"),
    ("workable", "workable", {"jobs": [{"shortcode": "d", "title": "Juriste", "city": "Paris", "url": "https://w/d"}]}, "https://w/d"),
])
def test_ats_parsers(ats, route, payload, expected_url):
    s = FakeSession({route: lambda *a: Resp(payload)})
    jobs = Ats(f"{ats}:co", {"ats": ats, "id": "co", "nom": "Co"}, Context(s, [], NOW, NOW)).fetch()
    assert len(jobs) == 1 and jobs[0].title == "Juriste" and jobs[0].url == expected_url and jobs[0].company == "Co"
