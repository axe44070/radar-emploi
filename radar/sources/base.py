from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

import requests

from ..core import Job

log = logging.getLogger("radar")


class NotConfigured(Exception):
    """Source activée mais identifiants absents : signalée, pas comptée comme panne."""


class SourceError(Exception):
    pass


@dataclass
class Context:
    session: requests.Session
    queries: list[str]          # requêtes FR + EN (sources françaises)
    since: datetime          # ne chercher que les offres publiées après cette date
    now: datetime
    warnings: list[str] = field(default_factory=list)
    queries_fr: list[str] = field(default_factory=list)
    queries_en: list[str] = field(default_factory=list)


class Source:
    family = ""

    def __init__(self, name: str, cfg: dict, ctx: Context):
        self.name = name
        self.cfg = cfg or {}
        self.ctx = ctx
        self.session = ctx.session

    @staticmethod
    def env(*names: str) -> list[str]:
        values = [os.environ.get(n, "").strip() for n in names]
        missing = [n for n, v in zip(names, values) if not v]
        if missing:
            raise NotConfigured("secret(s) manquant(s) : " + ", ".join(missing))
        return values

    def get_json(self, url: str, **kw):
        r = self.session.get(url, timeout=30, **kw)
        if r.status_code == 404:
            raise SourceError(f"404 introuvable : {url}")
        r.raise_for_status()
        return r.json()

    def warn(self, msg: str) -> None:
        log.warning("[%s] %s", self.name, msg)
        self.ctx.warnings.append(f"{self.name} : {msg}")

    def fetch(self) -> list[Job]:  # pragma: no cover
        raise NotImplementedError
