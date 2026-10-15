"""Alertes Telegram et email."""
from __future__ import annotations

import html
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

import requests

log = logging.getLogger("radar")
TG_LIMIT = 3900


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def source_label(src: str) -> str:
    names = {"francetravail": "France Travail", "apec": "APEC", "adzuna": "Adzuna", "google_jobs": "Google Jobs"}
    if src in names:
        return names[src]
    fam, _, rest = src.partition(":")
    return f"{rest} ({fam})" if rest else src


def job_line_html(r: dict) -> str:
    star = "⭐ " if r.get("priority") else ""
    check = " <i>(à vérifier)</i>" if r.get("to_check") else ""
    meta = " · ".join(x for x in (esc(r.get("company")), esc(r.get("location"))) if x)
    extra = " · ".join(x for x in (", ".join(r.get("profiles", [])),
                                    ", ".join(source_label(s["source"]) for s in r["sources"]),
                                    esc(r.get("salary"))) if x)
    return (f"{star}<b>{esc(r['title'])}</b>{check}\n{meta}\n<i>{esc(extra)}</i>\n"
            f"<a href=\"{esc(r['url'])}\">Voir l'offre</a>")


class Notifier:
    def __init__(self, cfg: dict, session: requests.Session | None = None):
        self.cfg = cfg
        self.alerts = cfg.get("alertes") or {}
        self.session = session or requests.Session()
        self.errors: list[str] = []
        self.sent: list[tuple[str, str]] = []  # (canal, résumé) pour les tests et le journal
        self.tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.smtp = {k: os.environ.get(k, "").strip() for k in
                     ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO", "EMAIL_FROM")}

    # ------------------------------------------------------------ canaux
    @property
    def telegram_on(self) -> bool:
        return bool((self.alerts.get("telegram") or {}).get("actif", True) and self.tg_token and self.tg_chat)

    @property
    def email_on(self) -> bool:
        s = self.smtp
        return bool((self.alerts.get("email") or {}).get("actif", True)
                    and s["SMTP_HOST"] and s["SMTP_USER"] and s["SMTP_PASSWORD"] and s["EMAIL_TO"])

    def telegram(self, text: str) -> None:
        if not self.telegram_on:
            return
        try:
            r = self.session.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", timeout=20, json={
                "chat_id": self.tg_chat, "text": text[:4096], "parse_mode": "HTML",
                "disable_web_page_preview": True})
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} {r.text[:200]}")
            self.sent.append(("telegram", text[:80]))
        except Exception as e:
            log.error("Telegram : %s", e)
            self.errors.append(f"Telegram : {e}")

    def email(self, subject: str, html_body: str) -> None:
        if not self.email_on:
            return
        s = self.smtp
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = s["EMAIL_FROM"] or s["SMTP_USER"]
        msg["To"] = s["EMAIL_TO"]
        msg.set_content("Ce message est au format HTML.")
        msg.add_alternative(html_body, subtype="html")
        try:
            port = int(s["SMTP_PORT"] or 587)
            ctx = ssl.create_default_context()
            if port == 465:
                with smtplib.SMTP_SSL(s["SMTP_HOST"], port, context=ctx, timeout=30) as srv:
                    srv.login(s["SMTP_USER"], s["SMTP_PASSWORD"])
                    srv.send_message(msg)
            else:
                with smtplib.SMTP(s["SMTP_HOST"], port, timeout=30) as srv:
                    srv.starttls(context=ctx)
                    srv.login(s["SMTP_USER"], s["SMTP_PASSWORD"])
                    srv.send_message(msg)
            self.sent.append(("email", subject))
        except Exception as e:
            log.error("Email : %s", e)
            self.errors.append(f"Email : {e}")

    # ------------------------------------------------------------ messages
    @property
    def dashboard(self) -> str:
        return self.cfg.get("tableau_de_bord_url", "")

    def new_jobs(self, jobs: list[dict]) -> None:
        if not jobs:
            return
        jobs = sorted(jobs, key=lambda r: (r.get("priority", False), r.get("score", 0)), reverse=True)
        limit = int((self.alerts.get("telegram") or {}).get("max_messages_individuels", 10))
        if len(jobs) <= limit:
            for r in jobs:
                self.telegram("🆕 " + job_line_html(r))
        else:
            header = f"🆕 <b>{len(jobs)} nouvelles offres</b>"
            if self.dashboard:
                header += f" — <a href=\"{esc(self.dashboard)}\">tableau de bord</a>"
            chunk = header
            for r in jobs:
                block = "\n\n" + job_line_html(r)
                if len(chunk) + len(block) > TG_LIMIT:
                    self.telegram(chunk)
                    chunk = "🆕 <i>(suite)</i>"
                chunk += block
            self.telegram(chunk)
        if (self.alerts.get("email") or {}).get("mode") == "chaque_passage":
            self.email(f"Radar emploi : {len(jobs)} nouvelle(s) offre(s)", self.email_html(jobs, "Nouvelles offres"))

    def initial_import(self, counts: dict[str, int]) -> None:
        total = sum(counts.values())
        if not total:
            return
        lines = "\n".join(f"• {esc(source_label(k))} : {v}" for k, v in counts.items() if v)
        txt = (f"📥 <b>Première synchronisation</b> : {total} offres existantes importées sans alerte "
               f"individuelle.\n{lines}\nLes prochaines nouvelles offres seront notifiées une par une.")
        if self.dashboard:
            txt += f"\n<a href=\"{esc(self.dashboard)}\">Voir le tableau de bord</a>"
        self.telegram(txt)

    def health(self, events: list[dict]) -> None:
        for ev in events:
            src = esc(source_label(ev["source"]))
            if ev["type"] == "panne":
                txt = (f"⚠️ <b>Source en panne : {src}</b>\n{ev['failures']} échecs consécutifs.\n"
                       f"<code>{esc(ev['error'])[:500]}</code>\nLes autres sources continuent ; "
                       f"les offres manquées seront rattrapées au rétablissement (fenêtre de recouvrement).")
            elif ev["type"] == "silence":
                txt = (f"🔇 <b>{src} ne renvoie plus rien</b> depuis {ev['streak']} passages alors qu'elle "
                       f"était active. Le site a peut-être changé : à vérifier.")
            else:
                txt = f"✅ <b>{src} est rétablie.</b>"
            self.telegram(txt)
            if not self.telegram_on:
                self.email(f"Radar emploi : {ev['type']} — {source_label(ev['source'])}", f"<p>{txt}</p>")

    def warnings(self, warnings: list[str]) -> None:
        if warnings:
            self.telegram("ℹ️ <b>Avertissements</b>\n" + "\n".join(f"• {esc(w)}" for w in warnings[:10]))

    def digest(self, jobs: list[dict], sources_state: dict, title: str) -> None:
        body = self.email_html(jobs, title, sources_state)
        n = len(jobs)
        self.email(f"Radar emploi — {title} : {n} offre{'s' if n > 1 else ''}", body)

    def weekly(self, jobs: list[dict], sources_state: dict) -> None:
        by_profile: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for r in jobs:
            for p in r.get("profiles", []):
                by_profile[p] = by_profile.get(p, 0) + 1
            for s in r["sources"]:
                by_source[s["source"]] = by_source.get(s["source"], 0) + 1
        ko = [k for k, v in sources_state.items() if v.get("status") == "erreur"]
        txt = [f"📊 <b>Bilan de la semaine : {len(jobs)} nouvelles offres</b>"]
        txt += [f"• {esc(k)} : {v}" for k, v in sorted(by_profile.items(), key=lambda x: -x[1])]
        txt.append("\n<b>Par source</b>")
        txt += [f"• {esc(source_label(k))} : {v}" for k, v in sorted(by_source.items(), key=lambda x: -x[1])]
        txt.append("\n" + ("✅ Toutes les sources fonctionnent." if not ko else
                           "⚠️ En panne : " + ", ".join(esc(source_label(k)) for k in ko)))
        if self.dashboard:
            txt.append(f"<a href=\"{esc(self.dashboard)}\">Tableau de bord</a>")
        message = "\n".join(txt)
        if self.telegram_on:
            self.telegram(message)
        else:
            self.email("Radar emploi — bilan de la semaine", "<p>" + message.replace("\n", "<br>") + "</p>")

    def email_html(self, jobs: list[dict], title: str, sources_state: dict | None = None) -> str:
        jobs = sorted(jobs, key=lambda r: (r.get("priority", False), r.get("score", 0)), reverse=True)
        rows = "".join(
            f"<tr><td style='padding:10px 8px;border-bottom:1px solid #e5e5e5'>"
            f"{'⭐ ' if r.get('priority') else ''}<a href='{esc(r['url'])}' style='color:#1a56db;font-weight:600;"
            f"text-decoration:none'>{esc(r['title'])}</a>{' <i>(à vérifier)</i>' if r.get('to_check') else ''}<br>"
            f"<span style='color:#555'>{esc(r.get('company'))} · {esc(r.get('location'))}</span><br>"
            f"<span style='color:#888;font-size:12px'>{esc(', '.join(r.get('profiles', [])))} · "
            f"{esc(', '.join(source_label(s['source']) for s in r['sources']))}"
            f"{' · ' + esc(r['salary']) if r.get('salary') else ''}</span></td></tr>"
            for r in jobs)
        if not jobs:
            rows = "<tr><td style='padding:12px 8px;color:#555'>Aucune nouvelle offre sur la période.</td></tr>"
        health = ""
        if sources_state:
            items = []
            for k, v in sources_state.items():
                st = v.get("status", "?")
                icon = {"ok": "🟢", "erreur": "🔴", "non_configuree": "⚪", "en_attente": "🟢"}.get(st, "⚪")
                items.append(f"{icon} {esc(source_label(k))}")
            health = ("<p style='color:#555;font-size:13px;margin-top:24px'><b>État des sources</b><br>"
                      + " &nbsp; ".join(items) + "</p>")
        link = (f"<p><a href='{esc(self.dashboard)}' style='color:#1a56db'>Ouvrir le tableau de bord →</a></p>"
                if self.dashboard else "")
        return (f"<div style='font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:680px'>"
                f"<h2 style='margin:0 0 12px'>{esc(title)}</h2>{link}"
                f"<table style='border-collapse:collapse;width:100%'>{rows}</table>{health}</div>")
