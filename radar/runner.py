"""Orchestration d'un passage : collecte -> filtre -> dédoublonnage -> alertes -> sauvegarde."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from .core import PARIS, iso, parse_date, utcnow
from .http import make_session
from .matching import Matcher
from .notify import Notifier
from .sources import Context, NotConfigured, configured_sources
from .store import Store

log = logging.getLogger("radar")


def profile_queries(cfg: dict, keys=("requetes", "requetes_en")) -> list[str]:
    seen, out = set(), []
    for p in (cfg.get("profils") or {}).values():
        for key in keys:
            for q in p.get(key) or []:
                if q.lower() not in seen:
                    seen.add(q.lower())
                    out.append(q)
    return out


def _health_events(name: str, st: dict, now: datetime, alert_cfg: dict) -> list[dict]:
    pannes = alert_cfg.get("pannes") or {}
    fail_n = int(pannes.get("echecs_avant_alerte", 2))
    zero_n = int(pannes.get("zero_avant_alerte", 6))
    events = []
    alerted = parse_date(st.get("alerted_at"))
    if st["status"] == "erreur" and st["consecutive_failures"] >= fail_n:
        if not alerted or now - alerted > timedelta(hours=24):
            events.append({"type": "panne", "source": name, "failures": st["consecutive_failures"],
                           "error": st.get("last_error", "")})
            st["alerted_at"] = iso(now)
    elif st["status"] == "ok":
        if alerted:
            events.append({"type": "retablie", "source": name})
            st.pop("alerted_at", None)
        hist = st.get("history", [])
        if st.get("zero_streak", 0) == zero_n and any(h > 0 for h in hist):
            events.append({"type": "silence", "source": name, "streak": zero_n})
    return events


def run(cfg: dict, data_dir: str, dry_run: bool = False, session=None, now: datetime | None = None,
        notifier: Notifier | None = None) -> dict:
    t0 = time.time()
    now = now or utcnow()
    session = session or make_session()
    coll = cfg.get("collecte") or {}
    overlap = timedelta(hours=int(coll.get("recouvrement_heures", 48)))
    first_days = timedelta(days=int(coll.get("premier_passage_jours", 7)))
    store = Store(data_dir, int(coll.get("fenetre_doublons_jours", 45)))
    matcher = Matcher(cfg)
    notifier = notifier or Notifier(cfg, session)
    queries = profile_queries(cfg)
    queries_fr = profile_queries(cfg, ('requetes',))
    queries_en = profile_queries(cfg, ('requetes_en',))

    sources = configured_sources(cfg)
    active_names = {n for n, _, _ in sources}
    for old in list(store.state["sources"]):
        if old not in active_names:
            del store.state["sources"][old]

    new_jobs, initial_counts, warnings, events = [], {}, [], []
    run_rec = {"at": iso(now), "sources": {}, "new": 0, "merged": 0}

    for name, cls, scfg in sources:
        st = store.state["sources"].setdefault(name, {"consecutive_failures": 0, "history": []})
        interval = float(scfg.get("intervalle_heures", 0) or 0)
        last_try = parse_date(st.get("last_try"))
        if interval and last_try and now - last_try < timedelta(hours=interval) - timedelta(minutes=10):
            if st.get("status") != "erreur":
                st["status"] = "en_attente"
            run_rec["sources"][name] = {"status": "en_attente"}
            continue

        last_ok = parse_date(st.get("last_ok"))
        silent = last_ok is None  # 1re synchro de la source : import sans alerte individuelle
        since = (last_ok - overlap) if last_ok else (now - first_days)
        ctx = Context(session=session, queries=queries, since=since, now=now, warnings=warnings,
                      queries_fr=queries_fr, queries_en=queries_en)
        st["last_try"] = iso(now)
        stats = {"raw": 0, "kept": 0, "new": 0, "merged": 0}
        try:
            raw = cls(name, scfg, ctx).fetch()
        except NotConfigured as e:
            st.update(status="non_configuree", last_error=str(e))
            run_rec["sources"][name] = {"status": "non_configuree", "error": str(e)}
            log.info("[%s] non configurée : %s", name, e)
            continue
        except Exception as e:  # toute erreur est tracée et alertée, jamais silencieuse
            msg = f"{type(e).__name__}: {e}"
            st.update(status="erreur", last_error=msg[:500], last_error_at=iso(now),
                      consecutive_failures=st.get("consecutive_failures", 0) + 1)
            run_rec["sources"][name] = {"status": "erreur", "error": msg[:300]}
            log.error("[%s] %s", name, msg)
            events += _health_events(name, st, now, cfg.get("alertes") or {})
            continue

        stats["raw"] = len(raw)
        for job in raw:
            m = matcher.match(job)
            if not m:
                continue
            stats["kept"] += 1
            res = store.upsert(job, m, silent=silent)
            if res == "new":
                stats["new"] += 1
                if silent:
                    initial_counts[name] = initial_counts.get(name, 0) + 1
                else:
                    new_jobs.append(store._by_id[job.uid])
            elif res == "merged":
                stats["merged"] += 1

        st.update(status="ok", last_ok=iso(now), consecutive_failures=0, last_error=None,
                  last_count=len(raw), last_kept=stats["kept"])
        st["history"] = (st.get("history", []) + [len(raw)])[-30:]
        st["zero_streak"] = st.get("zero_streak", 0) + 1 if len(raw) == 0 else 0
        st["total_new"] = st.get("total_new", 0) + stats["new"]
        events += _health_events(name, st, now, cfg.get("alertes") or {})
        run_rec["sources"][name] = {"status": "ok", **stats}
        log.info("[%s] %d brutes, %d retenues, %d nouvelles, %d doublons inter-sources",
                 name, stats["raw"], stats["kept"], stats["new"], stats["merged"])

    purged = store.purge(int(coll.get("conservation_jours", 120)))
    run_rec.update(new=len(new_jobs), initial=sum(initial_counts.values()), purged=purged,
                   merged=sum(s.get("merged", 0) for s in run_rec["sources"].values()),
                   warnings=warnings, duration_s=round(time.time() - t0, 1))

    if not dry_run:
        notifier.initial_import(initial_counts)
        notifier.new_jobs(new_jobs)
        notifier.health(events)
        notifier.warnings(warnings)
        _scheduled_reports(cfg, store, notifier, now)
    run_rec["notification_errors"] = notifier.errors
    run_rec["events"] = events
    store.runs.append(run_rec)
    store.state["last_run"] = iso(now)
    store.save()
    return run_rec


def _scheduled_reports(cfg: dict, store: Store, notifier: Notifier, now: datetime) -> None:
    alerts = cfg.get("alertes") or {}
    email_cfg = alerts.get("email") or {}
    local = now.astimezone(PARIS)
    hour = int(email_cfg.get("heure", 8))
    today = local.date().isoformat()
    state = store.state

    if email_cfg.get("mode", "quotidien") == "quotidien" and notifier.email_on \
            and local.hour >= hour and state.get("last_digest_date") != today:
        since = parse_date(state.get("last_digest_at")) or (now - timedelta(days=1))
        jobs = [r for r in store.jobs if parse_date(r["first_seen"]) > since and not r.get("initial")]
        notifier.digest(jobs, state["sources"], f"Offres du {local.strftime('%d/%m/%Y')}")
        if not any(e.startswith("Email") for e in notifier.errors):
            state["last_digest_date"] = today
            state["last_digest_at"] = iso(now)

    week = f"{local.isocalendar()[0]}-W{local.isocalendar()[1]}"
    if alerts.get("bilan_hebdo", True) and local.weekday() == 0 and local.hour >= hour \
            and state.get("last_weekly") != week:
        jobs = [r for r in store.jobs if parse_date(r["first_seen"]) > now - timedelta(days=7)
                and not r.get("initial")]
        notifier.weekly(jobs, state["sources"])
        state["last_weekly"] = week
