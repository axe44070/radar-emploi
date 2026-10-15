"""Stockage JSON (lu directement par le tableau de bord) et déduplication."""
from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

from .core import Job, iso, parse_date, utcnow
from .matching import Match


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class Store:
    def __init__(self, data_dir: str | Path, dup_window_days: int = 45):
        self.dir = Path(data_dir)
        self.jobs: list[dict] = _load(self.dir / "jobs.json", [])
        self.state: dict = _load(self.dir / "state.json", {})
        self.runs: list[dict] = _load(self.dir / "runs.json", [])
        self.state.setdefault("sources", {})
        self.dup_window = timedelta(days=dup_window_days)
        self._by_id = {}
        self._by_fuzzy = {}
        for rec in self.jobs:
            self._index(rec)

    def _index(self, rec: dict) -> None:
        for sid in rec.get("ids", [rec["id"]]):
            self._by_id[sid] = rec
        if rec.get("fuzzy"):
            self._by_fuzzy[rec["fuzzy"]] = rec

    @property
    def is_empty(self) -> bool:
        return not self.jobs

    def upsert(self, job: Job, m: Match, silent: bool = False) -> str:
        """Retourne 'new', 'merged' (vue sur une autre source) ou 'seen'."""
        now = iso(utcnow())
        rec = self._by_id.get(job.uid)
        if rec:
            rec["last_seen"] = now
            return "seen"

        fz = job.fuzzy_key
        rec = self._by_fuzzy.get(fz) if fz else None
        if rec and utcnow() - parse_date(rec["first_seen"]) <= self.dup_window:
            rec["last_seen"] = now
            rec.setdefault("ids", [rec["id"]]).append(job.uid)
            if not any(s["source"] == job.source for s in rec["sources"]):
                rec["sources"].append({"source": job.source, "url": job.url})
            # complète les champs manquants
            for k, v in (("salary", job.salary), ("contract", job.contract), ("location", job.location)):
                if v and not rec.get(k):
                    rec[k] = v
            self._by_id[job.uid] = rec
            return "merged"

        rec = {
            "id": job.uid,
            "ids": [job.uid],
            "fuzzy": fz,
            "title": job.title.strip(),
            "company": job.company.strip(),
            "location": job.location.strip(),
            "url": job.url,
            "published": iso(job.published),
            "first_seen": now,
            "last_seen": now,
            "sources": [{"source": job.source, "url": job.url}],
            "profiles": m.profiles,
            "score": m.score,
            "to_check": m.to_check,
            "priority": m.priority,
            "contract": job.contract,
            "salary": job.salary,
            "snippet": job.description[:400],
            "initial": silent,
        }
        self.jobs.append(rec)
        self._index(rec)
        return "new"

    def purge(self, retention_days: int) -> int:
        limit = utcnow() - timedelta(days=retention_days)
        before = len(self.jobs)
        self.jobs = [r for r in self.jobs if parse_date(r["last_seen"]) >= limit]
        return before - len(self.jobs)

    def save(self, max_runs: int = 500) -> None:
        self.jobs.sort(key=lambda r: r["first_seen"], reverse=True)
        _save(self.dir / "jobs.json", self.jobs)
        _save(self.dir / "state.json", self.state)
        _save(self.dir / "runs.json", self.runs[-max_runs:])
