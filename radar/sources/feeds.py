"""Flux RSS/Atom et surveillance de pages carrières sans API."""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin, urldefrag

from ..core import Job, parse_date, strip_html
from .base import Source, SourceError

ATOM = "{http://www.w3.org/2005/Atom}"


class Rss(Source):
    family = "rss"

    def fetch(self) -> list[Job]:
        r = self.session.get(self.cfg["url"], timeout=30)
        r.raise_for_status()
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as e:
            raise SourceError(f"flux illisible : {e}")
        company = self.cfg.get("nom", "")
        jobs = []
        for it in root.iter("item"):
            link = (it.findtext("link") or "").strip()
            jobs.append(Job(source=self.name, source_id=(it.findtext("guid") or link).strip(),
                            title=strip_html(it.findtext("title")), company=company, url=link,
                            published=parse_date(it.findtext("pubDate")),
                            description=strip_html(it.findtext("description"))))
        for it in root.iter(f"{ATOM}entry"):
            link_el = it.find(f"{ATOM}link")
            link = link_el.get("href", "") if link_el is not None else ""
            jobs.append(Job(source=self.name, source_id=(it.findtext(f"{ATOM}id") or link).strip(),
                            title=strip_html(it.findtext(f"{ATOM}title")), company=company, url=link,
                            published=parse_date(it.findtext(f"{ATOM}updated") or it.findtext(f"{ATOM}published")),
                            description=strip_html(it.findtext(f"{ATOM}summary") or it.findtext(f"{ATOM}content"))))
        return jobs


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None


class Page(Source):
    """Chaque lien de la page dont le texte correspond aux mots-clés devient une offre."""
    family = "page"

    def fetch(self) -> list[Job]:
        url = self.cfg["url"]
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        parser = _Links()
        parser.feed(r.text)
        pattern = re.compile(self.cfg["motif_lien"]) if self.cfg.get("motif_lien") else None
        jobs, seen = [], set()
        for href, text in parser.links:
            if not text or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            abs_url = urldefrag(urljoin(url, href))[0]
            if pattern and not pattern.search(abs_url):
                continue
            if abs_url in seen:
                continue
            seen.add(abs_url)
            jobs.append(Job(source=self.name, source_id=hashlib.sha1(abs_url.encode()).hexdigest()[:20],
                            title=text[:200], company=self.cfg.get("nom", ""), url=abs_url))
        if not parser.links:
            raise SourceError("aucun lien trouvé (page générée en JavaScript ?)")
        return jobs
