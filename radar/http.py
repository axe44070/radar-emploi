"""Session HTTP partagée avec reprises automatiques (429 / 5xx)."""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "Mozilla/5.0 (compatible; RadarEmploi/1.0; veille personnelle)"
TIMEOUT = 30


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"})
    return s
