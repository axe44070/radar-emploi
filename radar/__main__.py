"""Ligne de commande.

  python -m radar run            un passage complet (collecte + alertes)
  python -m radar run --dry-run  collecte sans envoyer d'alerte
  python -m radar test-alertes   envoie un message de test Telegram + email
  python -m radar sources        liste les sources actives et leur état
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

from .notify import Notifier, source_label
from .runner import run

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("commande", choices=["run", "test-alertes", "sources"])
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--data", default=str(ROOT / "docs" / "data"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)

    if args.commande == "test-alertes":
        n = Notifier(cfg)
        print(f"Telegram configuré : {n.telegram_on} — Email configuré : {n.email_on}")
        n.telegram("✅ <b>Radar emploi</b> : test de notification réussi.")
        n.email("Radar emploi : test", "<p>✅ Test de notification réussi.</p>")
        for e in n.errors:
            print("ERREUR", e)
        return 1 if n.errors or not (n.telegram_on or n.email_on) else 0

    if args.commande == "sources":
        state = json.loads((Path(args.data) / "state.json").read_text()) if (Path(args.data) / "state.json").exists() else {}
        for k, v in (state.get("sources") or {}).items():
            print(f"{v.get('status', '?'):15} {source_label(k):35} dernier OK : {v.get('last_ok')}  {v.get('last_error') or ''}")
        return 0

    rec = run(cfg, args.data, dry_run=args.dry_run)
    print(json.dumps({k: rec[k] for k in ("new", "initial", "merged", "duration_s")}, ensure_ascii=False))
    statuses = [s["status"] for s in rec["sources"].values()]
    all_failed = statuses and all(s in ("erreur", "non_configuree") for s in statuses) and "erreur" in statuses
    # un code de sortie non nul fait échouer le workflow GitHub -> email de GitHub + alerte Telegram
    return 1 if all_failed or rec["notification_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
