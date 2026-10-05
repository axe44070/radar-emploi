# Radar emploi

Veille automatique d'offres d'emploi (premier poste en CDI : finance, banque, marchés et sales, à Paris et dans les grandes places financières) qui tourne toute seule sur GitHub :

- **collecte toutes les heures** (6 h – 22 h) sur France Travail, l'APEC, Adzuna, Google Jobs (option) et les pages carrières de vos entreprises cibles ;
- **filtre** selon vos mots-clés, exclusions et zones géographiques ;
- **supprime les doublons**, y compris la même offre publiée sur plusieurs sites ;
- **alerte sur Telegram** à chaque nouvelle offre et envoie un **récapitulatif par email** chaque matin ;
- publie un **tableau de bord web** (filtres, suivi de candidature, export CSV, état des sources).

Coût : 0 € avec les sources gratuites. Google Jobs (SerpAPI) est la seule option payante.

---

## Comment le système évite de rater des offres

| Risque | Parade |
|---|---|
| Une source ne couvre pas tout | 4 types de sources croisées + pages carrières directes des entreprises cibles |
| Le radar tombe en panne quelques heures | Chaque passage relit les **48 dernières heures** : tout ce qui a été publié pendant la panne est rattrapé |
| France Travail plafonne à 1 150 résultats par recherche | La période est **découpée automatiquement** jusqu'à passer sous le plafond |
| Une source change son format ou bloque | **Alerte Telegram « source en panne »** après 2 échecs, puis « rétablie » |
| Une source répond mais ne renvoie plus rien | **Alerte « ne renvoie plus rien »** après 6 passages vides alors qu'elle était active |
| Le workflow plante entièrement | Alerte Telegram immédiate + email automatique de GitHub |
| Le planificateur GitHub s'arrête | Le tableau de bord affiche un bandeau rouge si aucun passage depuis plus de 3 h ; l'email quotidien sert aussi de preuve de vie |
| Un mot-clé trop strict | Matching insensible aux accents et à la casse ; option `mots_cles_description` pour attraper les titres atypiques (marqués « à vérifier ») |
| Lieu non renseigné | Gardé par défaut (`garder_lieu_inconnu: true`) |

Ce qui reste hors de portée : LinkedIn et Indeed interdisent le scraping direct dans leurs conditions d'utilisation. Leurs offres sont couvertes indirectement par Google Jobs (option SerpAPI) et, souvent, par France Travail qui agrège des sites partenaires. Vous pouvez aussi ajouter les pages carrières des entreprises qui vous intéressent.

---

## Installation (environ 30 minutes, une seule fois)

### 1. Créer le dépôt GitHub

1. Créez un compte sur [github.com](https://github.com) si besoin.
2. **New repository**, nommé `radar-emploi`.
   - **Public** : le tableau de bord est hébergé gratuitement (GitHub Pages). Les offres collectées et votre `config.yaml` sont alors visibles par qui connaît l'adresse. Vos clés restent secrètes.
   - **Privé** : GitHub Pages exige alors un abonnement GitHub Pro (environ 4 $/mois).
3. Déposez tous les fichiers de ce dossier dans le dépôt : **Add file → Upload files**, en gardant l'arborescence, notamment le dossier caché `.github`. Le plus simple est d'utiliser GitHub Desktop ou `git push`.

### 2. Obtenir les accès aux sources (gratuit)

**France Travail** (source principale) :
1. Créez un compte sur [francetravail.io](https://francetravail.io/), puis **Mon espace → Créer une application**.
2. Ajoutez l'API **« Offres d'emploi v2 »** à l'application.
3. Notez l'**identifiant client** et la **clé secrète**.

**Adzuna** (agrégateur) :
1. Inscription sur [developer.adzuna.com](https://developer.adzuna.com/).
2. Notez l'**App ID** et l'**App Key**.

**Google Jobs** (optionnel, payant) : compte sur [serpapi.com](https://serpapi.com/), notez la clé API, puis passez `google_jobs.actif` à `true` dans `config.yaml`. Il est réglé sur 2 interrogations par jour pour ménager le quota.

L'APEC et les pages carrières ne demandent aucune clé.

### 3. Créer le bot Telegram

1. Dans Telegram, ouvrez **@BotFather**, envoyez `/newbot` et suivez les instructions. Notez le **token** (`123456:ABC…`).
2. Ouvrez la conversation avec votre nouveau bot et envoyez-lui « bonjour ».
3. Dans un navigateur, ouvrez `https://api.telegram.org/bot<VOTRE_TOKEN>/getUpdates`. Repérez `"chat":{"id": 123456789` : c'est votre **chat ID**.

### 4. Préparer l'envoi d'emails

Avec Gmail :
1. Activez la validation en deux étapes sur votre compte Google.
2. Créez un **mot de passe d'application** sur [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Paramètres : hôte `smtp.gmail.com`, port `587`.

N'importe quel autre fournisseur SMTP (OVH, Outlook, Brevo…) fonctionne aussi.

### 5. Enregistrer les secrets dans GitHub

Dans le dépôt : **Settings → Secrets and variables → Actions → New repository secret**. Créez :

| Nom | Valeur |
|---|---|
| `FRANCETRAVAIL_CLIENT_ID` | identifiant client France Travail |
| `FRANCETRAVAIL_CLIENT_SECRET` | clé secrète France Travail |
| `ADZUNA_APP_ID` | App ID Adzuna |
| `ADZUNA_APP_KEY` | App Key Adzuna |
| `TELEGRAM_BOT_TOKEN` | token du bot |
| `TELEGRAM_CHAT_ID` | votre chat ID |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | votre adresse Gmail |
| `SMTP_PASSWORD` | le mot de passe d'application |
| `EMAIL_TO` | l'adresse qui reçoit les alertes |
| `SERPAPI_KEY` | *(optionnel)* clé SerpAPI |

Une source sans ses secrets est simplement marquée « non configurée ». Le reste fonctionne.

### 6. Activer le tableau de bord

1. **Settings → Pages → Build and deployment** : *Deploy from a branch*, branche `main`, dossier `/docs`, puis **Save**.
2. Une minute plus tard, l'adresse s'affiche (`https://<compte>.github.io/radar-emploi/`). Reportez-la dans `config.yaml` → `tableau_de_bord_url`.

### 7. Autoriser le robot à enregistrer ses données

**Settings → Actions → General → Workflow permissions** : cochez **Read and write permissions**.

### 8. Premier lancement

1. Onglet **Actions → Radar emploi → Run workflow**.
2. Au premier passage, les offres des 7 derniers jours sont importées **sans alerte individuelle**. Vous recevez un seul message récapitulatif sur Telegram, et les offres sont visibles sur le tableau de bord.
3. Ensuite, chaque nouvelle offre déclenche une alerte.

Pour tester les alertes seules, lancez en local `python -m radar test-alertes` avec les variables d'environnement définies.

---

## Personnaliser la veille

Tout se règle dans **`config.yaml`**. Modifiez-le directement sur GitHub (icône crayon), il est commenté ligne par ligne.

- **profils** : pour chaque métier, les `requetes` (français) et `requetes_en` (anglais) envoyées aux moteurs, et les `mots_cles_titre` qui filtrent.
- **sources.adzuna.pays** : une ligne par pays et ville (France, Royaume-Uni, États-Unis, Allemagne, Suisse, Singapour). Luxembourg et Hong Kong ne sont pas couverts par Adzuna : utilisez Google Jobs.
- **exclure_titre** : stage, alternance… à retirer si ces offres vous intéressent.
- **localisation.lieux** : `["Paris", "92", "Lyon"]`, ou vide pour toute la France.
- **entreprises_prioritaires** : mises en avant avec ⭐.
- **sources.entreprises** : ajoutez vos entreprises cibles. Repérez l'outil dans l'URL de leur page carrières :
  - `boards.greenhouse.io/xxx` → `{nom: X, ats: greenhouse, id: xxx}`
  - `jobs.lever.co/xxx` → `ats: lever`
  - `jobs.eu.lever.co/xxx` → `ats: lever`, avec en plus `region: eu`
  - `jobs.ashbyhq.com/xxx` → `ats: ashby`
  - `jobs.smartrecruiters.com/xxx` → `ats: smartrecruiters`
  - `apply.workable.com/xxx` → `ats: workable`
- **sources.rss / sources.pages** : flux RSS, ou pages carrières simples (cabinets, PME).
- **alertes** : seuils de panne, mode email (`quotidien` ou `chaque_passage`), heure du digest.

Pour changer la fréquence, modifiez la ligne `cron` dans `.github/workflows/radar.yml`. Les horaires sont en UTC.

---

## Tableau de bord

- Indicateurs : nouvelles offres sur 24 h et 7 jours, candidatures en cours, sources opérationnelles.
- Histogramme des nouvelles offres par jour.
- État de chaque source, avec le message d'erreur en cas de panne.
- Liste filtrable (texte, profil, source, période, statut, ⭐), badge « Nouveau » depuis votre dernière visite.
- **Statut de candidature** par offre : à traiter, intéressant, postulé, entretien, refusé, ignoré. Il est enregistré dans votre navigateur.
- **Export CSV** de la sélection, lisible dans Excel.

Les statuts sont propres à chaque navigateur. Exportez en CSV pour les conserver ailleurs.

---

## Structure

```
config.yaml                 ← vos réglages
radar/
  runner.py                 orchestration d'un passage
  matching.py               filtres (profils, exclusions, lieux)
  store.py                  stockage + dédoublonnage
  notify.py                 Telegram + email
  sources/                  un fichier par source
docs/
  index.html                tableau de bord
  data/                     données (mises à jour par le robot)
.github/workflows/radar.yml planification
tests/                      tests automatiques (hors ligne)
```

En local : `pip install -r requirements.txt pytest`, puis `python -m pytest`, puis `python -m radar run --dry-run`.

## Limites à connaître

- **GitHub Actions** peut retarder les tâches planifiées de quelques minutes à heure de pointe.
- **GitHub désactive les tâches planifiées** d'un dépôt public sans activité pendant 60 jours. Les commits de données du robot maintiennent normalement l'activité. Si vous recevez l'email « scheduled workflow disabled », réactivez-le dans l'onglet Actions.
- **L'APEC** n'a pas d'API officielle : le radar utilise le service interne de son site. S'il change, vous serez alerté. Il faudra alors mettre à jour `radar/sources/apec.py`.
