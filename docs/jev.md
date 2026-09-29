# Classification avec Jev (TypeSafe)

[Jev](https://docs.typesafe.ai/introduction) est un modèle de décision « System One »
publié par TypeSafe en septembre 2026. Il ne rédige pas de texte : il lit un contenu
et répond à une question fermée en donnant une probabilité pour chaque option
proposée. C'est exactement la forme du travail de MailFlow, qui ne doit choisir
qu'une phase commerciale parmi des options fixes.

## Mise en service

1. Créer une clé API sur [console.typesafe.ai](https://console.typesafe.ai).
2. Dans MailFlow, ouvrir **Réglages** et choisir **Jev (TypeSafe) — API de classification**.
3. Coller la clé dans **Clé API Jev (TypeSafe)** puis cliquer sur **Enregistrer la clé**.
   La clé va dans le coffre du système (`keyring`), jamais dans `config.json`.
4. Cliquer sur **Tester Jev** : un mail fictif, sans corps, est classé. Le statut
   passe au vert et le journal indique le modèle qui a répondu.
5. Cliquer sur **Enregistrer les réglages**.

Le modèle `jev-latest` suit la dernière version de Jev. Pour figer une version,
saisir dans **Modèle Jev** le nom affiché par le test. Les réglages OpenAI et Ollama
restent conservés et on peut revenir à ces moteurs à tout moment.

## Ce que MailFlow demande à Jev

L'annuaire fixe l'entreprise et son rôle ; Jev ne voit donc que les options permises
par ce rôle. Chaque mail donne lieu à un seul appel, avec les mêmes données que les
autres moteurs (métadonnées, sujet, extrait nettoyé du corps, noms des pièces jointes,
historique récent de l'entreprise et exemples vérifiés).

| Rôle dans l'annuaire | Options proposées à Jev | Catégorie |
|---|---|---|
| Fournisseur | Consultation, Offre du fournisseur | Demande de prix |
| Fournisseur | Commande passée, Suivi de commande, Facture, Réclamation ou problème | Commande |
| Client | Offre, Commande client, Technique, Exécution, Facturation, Réclamation, Autre | Correspondance |
| Inconnu | Toutes les options fournisseur et « Échange client » | Toujours à vérifier |

Les phases détaillées sont plus faciles à distinguer que trois catégories larges. La
probabilité d'une catégorie est la somme des probabilités de ses phases : par exemple
« Suivi de commande 70 % + Commande passée 10 % + Réclamation 10 % + Facture 5 % »
donne **Commande 95 %**. Cette somme est la confiance comparée au seuil de
vérification (80 % au minimum) : en dessous, le mail reste **À vérifier**.

Pour un client, la règle métier impose `Correspondance` ; la confiance affichée est
donc 100 % et Jev sert uniquement à décrire l'échange. Un fournisseur ne peut jamais
être classé en `Correspondance`, puisque cette option ne lui est pas proposée.

Comme Jev ne rédige rien, MailFlow construit lui-même les textes affichés :

- **Résumé** : la phase retenue suivie du sujet, par exemple
  `Suivi de commande : Confirmation de votre commande 4521`. Il alimente le bilan
  projet (commandes, problèmes signalés) et l'historique transmis aux mails suivants ;
- **Pourquoi** : la phase retenue et la répartition entre catégories, par exemple
  `Jev : Suivi de commande (70%). Commande 95%, Demande de prix 5%.`

Aucun extrait du mail n'est surligné comme preuve : Jev ne cite pas le texte.

## Rôles suggérés et application immédiate

Un mail n'est archivé automatiquement que si l'annuaire connaît le rôle de l'entreprise.
Pour une entreprise encore inconnue, le même appel à Jev pose trois questions de plus :

- **quel est son rôle ?** fournisseur, client, ou ni l'un ni l'autre ;
- **quelle serait la phase si c'est un fournisseur ?**
- **quelle serait la nature de l'échange si c'est un client ?**

L'onglet **Annuaire** affiche alors une colonne **Rôle suggéré**, par exemple
`Fournisseur · 96% · 12 mails` : c'est la moyenne des estimations sur les mails de cette
entreprise. Rien n'est enregistré sans vous :

- **Valider**, sur la ligne, enregistre le rôle de cette entreprise ;
- **Valider les suggestions sûres** enregistre, après confirmation, toutes les
  suggestions fournisseur ou client d'au moins 80 %.

Un rôle validé, ou choisi dans la colonne **Rôle global**, s'applique **aussitôt** aux
mails affichés, sans nouvel appel à Jev : MailFlow reprend la réponse déjà donnée pour ce
rôle, puis les mêmes contrôles (entreprise confirmée, confiance d'au moins 80 %). Les
mails sûrs passent en **Prêt** dans le bon dossier ; les autres restent à vérifier. Vos
corrections manuelles ne sont jamais modifiées.

Le bouton **Actualiser les rôles des mails** (aussi dans **Plus** → *Actualiser les rôles
(sans IA)*) réapplique l'annuaire à tous les mails affichés, sans appeler l'IA : utile
après avoir modifié plusieurs rôles. **Reclasser avec l'IA** reste disponible pour les
mails analysés avant cette version, ou avec un autre moteur, qui n'ont pas de réponse
par rôle.

## Suggestion de projet pour le rangement de la boîte mail

Sur la page **Boîte mail**, l'option **Proposer un projet avec Jev** s'active dès
qu'une clé Jev est enregistrée, quel que soit le moteur de classification choisi.
Elle ne concerne que les mails où aucun numéro `20XX-XXXX` n'a été trouvé.

MailFlow cherche dans l'annuaire les projets où les interlocuteurs externes du mail
ont déjà échangé, garde ceux dont le dossier Outlook existe (douze au plus) et pose
une question fermée : l'un de ces projets, ou `aucun_projet`. Sans projet candidat,
Jev n'est pas appelé. Une suggestion n'est affichée que si le projet retenu atteint
50 % et dépasse `aucun_projet` ; elle reste décochée jusqu'à votre validation.

La première erreur Jev (clé, quota, réseau, réponse invalide) arrête les suggestions
pour cette analyse ; les mails dont le numéro a été trouvé restent proposés.

## Erreurs et réseau

MailFlow appelle directement `https://api.typesafe.ai/v1/systemone` avec `httpx`, déjà
utilisé par l'application ; aucun composant supplémentaire n'est installé. Les
redirections sont refusées pour que la clé ne parte jamais vers une autre adresse. Le
proxy configuré sur le poste est respecté, comme pour OpenAI.

Un délai dépassé, une coupure réseau, une limite de débit (429) ou une erreur serveur
déclenchent un seul nouvel essai après une courte attente (5 secondes au plus). Ensuite,
ou pour une clé refusée, un modèle introuvable ou une réponse invalide, le mail reste
**À vérifier** avec un message local. Le texte renvoyé par le serveur n'est jamais
affiché, car il peut recopier le contenu du mail. Une panne Jev ne bascule jamais vers
OpenAI ni vers Ollama. Le délai par appel est de 20 secondes (`jev_timeout_seconds`
dans `config.json`).

## Mesurer Jev avant de l'adopter

Sur les dix mails synthétiques fournis avec le projet (aucun mail réel envoyé) :

```powershell
.venv312\Scripts\python.exe scripts/validate_jev.py --run --output
```

Sur vos propres décisions (corrections manuelles et mails archivés), avec le même
tirage que pour les autres moteurs afin de comparer :

```powershell
.venv312\Scripts\python.exe scripts/evaluate_ai.py --run --provider jev --limit 50 --seed 1
.venv312\Scripts\python.exe scripts/evaluate_ai.py --run --provider openai --limit 50 --seed 1
```

Le chiffre le plus important est « Classés automatiquement mais FAUX ». Pour estimer
le coût, ajouter `--price-input` avec le prix d'entrée affiché sur la console TypeSafe
et `--price-output 0` : l'API indique que les tokens de sortie ne sont pas facturés.

## Limites connues

- Jev est en accès anticipé ; le format de réponse suivi par MailFlow est celui de la
  documentation publique et du SDK Python officiel `typesafe-sdk` 0.7.2.
- La qualité sur les mails de Balz Metal n'est pas encore mesurée : lancer les deux
  scripts ci-dessus avant de généraliser le moteur.
- Le résumé est un libellé de phase, pas une synthèse rédigée comme avec OpenAI.
