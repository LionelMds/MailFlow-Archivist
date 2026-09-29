# Lien avec ProjectFlow Automator

ProjectFlow Automator crée les projets Balz Metal : dossier local, fiche, ligne du
répertoire chantier et, en option, dossier Outlook `Année/2026-0150 (Désignation)`.
MailFlow range et archive les mails de ces projets. Les deux logiciels se partagent le
travail ainsi :

| Sujet | Responsable |
|---|---|
| Numéro, désignation, répertoire chantier | ProjectFlow |
| Création et nom des dossiers projet Outlook | ProjectFlow |
| Rangement des mails dans ces dossiers | MailFlow |
| Archivage `.msg` dans les dossiers locaux | MailFlow |

MailFlow ne crée jamais de dossier projet lui-même. Un dossier projet absent de la
boîte mail arrive quand le projet a été créé sur un autre poste ou sans l'option Outlook
de ProjectFlow.

## Dossiers absents

Sur la page **Boîte mail**, après une analyse, les mails dont le numéro n'a pas de
dossier Outlook ont l'état **Dossier absent**. Le bouton
**Créer les dossiers absents avec ProjectFlow** envoie ces numéros à ProjectFlow, après
confirmation. Pour chaque numéro, ProjectFlow :

1. cherche le projet dans le répertoire chantier (onglet de l'année) ; une ligne vide ou
   absente n'est jamais créée ;
2. crée le dossier Outlook avec son arborescence et son nom habituels, dans le compte
   choisi dans ses paramètres ;
3. répond projet par projet : prêt, absent du répertoire, ou erreur.

MailFlow relit ensuite les dossiers projet, sans relire les mails : les mails concernés
passent en **Prêt à ranger** et sont cochés. Le rangement reste une étape séparée, avec
sa propre confirmation. Si ProjectFlow a créé le dossier dans un autre compte ou hors du
dossier source analysé, MailFlow le signale.

## Mise en service

- ProjectFlow **0.1.57** ou plus récent, installé sur le même poste, avec
  `Paramètres` → `Outlook` activé sur le même compte que MailFlow.
- MailFlow trouve le programme installé tout seul. Sinon, indiquer
  `ProjectFlowAutomator.exe` dans **Réglages** → **ProjectFlow Automator**. La ligne
  d'état indique la version détectée ; une version trop ancienne désactive le bouton.
- ProjectFlow lit le répertoire chantier avec sa connexion Microsoft enregistrée. La
  demande n'ouvre jamais de page de connexion : si la session a expiré, MailFlow affiche
  « Connexion Microsoft à renouveler » ; ouvrir ProjectFlow, se reconnecter, puis
  relancer.

## Échange technique

MailFlow lance ProjectFlow sans fenêtre :

```text
ProjectFlowAutomator.exe --mailflow-request demande.json --mailflow-result resultat.json
```

Les deux fichiers sont dans un dossier temporaire privé, effacé après lecture. Ce mode ne
passe pas par l'instance déjà ouverte de ProjectFlow et ne l'interrompt pas.

Demande (protocole 1) :

```json
{"protocol": 1, "action": "ensure_outlook_folders", "numbers": ["2026-0150"]}
```

Réponse :

```json
{
  "protocol": 1,
  "ok": true,
  "error": null,
  "projectflow_version": "0.1.57",
  "outlook": {"mailbox": "lionel@balzmetal.ch", "base_folder": "inbox"},
  "projects": [
    {"number": "2026-0150", "status": "ok", "designation": "Halle",
     "societe": "Morges SA", "folder_paths": [["2026", "2026-0150 (Halle)"]], "message": ""},
    {"number": "2026-0999", "status": "unknown",
     "message": "Projet absent du repertoire chantier: creez-le d'abord dans ProjectFlow."}
  ]
}
```

Seuls des numéros de projet principal (`AAAA-NNNN`, 50 au plus par demande) sont
acceptés ; MailFlow découpe les listes plus longues. Une erreur générale (`ok: false`)
porte un message local, affiché tel quel. Sans réponse après 2 minutes, MailFlow
s'arrête d'attendre sans fermer ProjectFlow. Côté ProjectFlow, le code est dans
`src/projectflow/bridge.py` ; côté MailFlow, dans `src/mailflow/core/projectflow_link.py`.
Toute évolution du format change `protocol` des deux côtés.
