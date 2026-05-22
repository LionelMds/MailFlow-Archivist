# MailFlow Archivist

<img src="src/mailflow/assets/mailflow-logo.png" alt="MailFlow logo" width="220">

MailFlow Archivist est une application desktop Windows pour preparer et archiver les e-mails Outlook classes par projet vers les dossiers locaux Balz Metal Sa.

Le MVP est non destructif :

- aucun mail Outlook n'est supprime ;
- aucun fichier existant n'est ecrase sans confirmation explicite ;
- les decisions passent par une previsualisation ;
- SQLite garde une trace des mails archives pour eviter les doublons.

## Etat de cette base

Cette premiere tranche met en place :

- architecture `src/mailflow` ;
- modeles metier typés avec `pydantic` ;
- parseur de numeros projet ;
- configuration JSON et stockage securise de cle OpenAI via `keyring` ;
- classification locale par regles ;
- moteur de decision ;
- stockage SQLite ;
- scanner et exporteur Outlook mockables ;
- service de scan Outlook par compte, racine, annee et projet optionnel ;
- pipeline de previsualisation regles + IA + decision ;
- hierarchie limitee a `Correspondance`, `Fournisseurs/Demande de prix`, `Fournisseurs/Commande`,
  puis entreprise ;
- annuaire SQLite evolutif alimente par les domaines et contacts Outlook ;
- previsualisateur d'arborescence avec renommage et fusion avant archivage ;
- parametrage IA dans l'interface avec cle OpenAI stockee dans `keyring` et test visuel ;
- recherche de mises a jour depuis l'application avec lancement de l'installateur ;
- logo officiel MailFlow utilise dans l'application, l'icone Windows et l'app macOS ;
- resume projet automatique dans l'inspecteur et dans le journal HTML ;
- export HTML projet centralise dans `Correspondance` avec pieces jointes liees ;
- surveillance Outlook par scan regulier avec confirmation avant mise a jour HTML ;
- export CSV de rapport sans corps de mails ;
- squelette UI PySide6 ;
- tests unitaires et smoke tests.

## Export HTML projet

Le bouton `Exporter HTML projet` cree un journal par projet scanne :

```text
[Projet]\Correspondance\2025-4893 - Correspondance projet.html
[Projet]\Correspondance\2025-4893 - pieces jointes\
```

Le HTML regroupe les mails envoyes et recus dans la meme arborescence que la
previsualisation MailFlow. Un panneau lateral permet de naviguer par branche de
dossiers, et les mails sont affiches sous leur dossier cible. La recherche, les filtres
et les liens relatifs vers les pieces jointes restent disponibles. Les liens s'ouvrent
dans un nouvel onglet/fenetre pour mieux fonctionner aussi sur macOS. Le HTML contient
aussi un lien local direct, et l'export demande a OneDrive/Windows de garder les pieces
jointes disponibles localement quand elles sont dans un dossier OneDrive. Les pieces
jointes deja presentes sont reutilisees et ne sont pas ecrasees.

Un bloc `Resume projet` est ajoute en haut du HTML et dans l'inspecteur MailFlow. Il
donne une synthese globale des echanges, separe les points clients et fournisseurs,
liste les commandes/suivis de commande detectes et remonte les problemes ou
reclamations quand des indices existent. Cette synthese est construite depuis les
decisions de classement, l'annuaire, les sujets, les extraits et les resumes IA deja
disponibles ; elle ne declenche pas d'envoi supplementaire a l'IA.

Les destinations proposees sont limitees a trois dossiers metier :
`Correspondance/Entreprise`, `Fournisseurs/Demande de prix/Entreprise` et
`Fournisseurs/Commande/Entreprise`.
`Correspondance` est reserve aux clients, intervenants et echanges internes. Un mail
fournisseur ne part jamais automatiquement en `Correspondance` : il va en demande de
prix, en commande, ou en `A verifier` si MailFlow ne peut pas choisir avec certitude.
L'annuaire local peut etre alimente depuis tout l'historique Outlook projet :
une adresse ou un domaine connu, par exemple `@gva.ch`, prend le pas sur les
heuristiques de nom et permet de classer directement sous l'entreprise officielle.
L'onglet `Annuaire` permet aussi d'attribuer un role par projet a chaque entreprise ;
ce role est reapplique a toutes les lignes du projet scanne.
Le journal HTML permet aussi de filtrer par dossier cible.

Apres le scan, le panneau `Arborescence` affiche les dossiers proposes avec le nombre
de mails. L'utilisateur peut renommer un dossier ou fusionner deux dossiers detectes
comme doublons avant toute creation de fichiers.

Les images integrees dans le corps des mails, comme les logos de signature, ne sont pas
exportees comme pieces jointes. Elles sont affichees directement dans le journal HTML.

La case `Surveillance Outlook` relance un scan toutes les 5 minutes tant que
l'application reste ouverte. Si une previsualisation est deja ouverte dans la fenetre,
la surveillance se met en attente afin de ne pas ecraser les corrections manuelles.
En cas de nouveaux mails detectes lors d'un scan autorise, MailFlow affiche la
previsualisation et l'arborescence mises a jour, puis demande confirmation avant de
mettre a jour le journal HTML.

Quand la surveillance est active, fermer la fenetre masque MailFlow dans la zone de
notification au lieu de l'arreter. Le menu de l'icone permet de rouvrir l'application,
d'activer ou desactiver la surveillance, ou de quitter completement.

## Mode IA

Le mode IA se configure dans le bloc `Configuration` :

- `desactivee` : seules les regles locales sont utilisees ;
- `ambigu seulement` : l'IA intervient lorsque les regles manquent de confiance ;
- `tout classifier` : chaque mail est aussi classe par IA.

Le modele par defaut est `gpt-5.4-nano`, choisi pour un usage de classification
rapide et economique. Le champ `Modele IA` propose aussi des modeles plus puissants
comme `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, `gpt-4o-mini` et `gpt-4o`, tout en
restant editable pour saisir un autre modele compatible Structured Outputs.

La cle OpenAI est enregistree dans le coffre du systeme via `keyring`, jamais dans le
fichier JSON ni dans les logs. Le bouton `Tester IA` lance un mini appel structure sur
un mail fictif et affiche un statut colore. L'option d'envoi du corps peut etre
desactivee pour n'envoyer que sujet, metadonnees et noms des pieces jointes.

Quand l'IA intervient, l'apercu du mail affiche la decision IA, son resume court et
l'explication en quelques mots.

## Commandes utiles

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m mypy src tests
python -m mailflow --diagnose-outlook
python -m mailflow --import-contact-directory --account "lionel@balzmetal.ch" --outlook-root "Boite de reception"
```

Sur ce poste, si `python` pointe vers l'alias Microsoft Store, utiliser un Python 3.11+ explicite ou le runtime configure dans Codex.

## Installation et releases

Le workflow GitHub Actions `.github/workflows/release.yml` construit des installateurs
directement utilisables :

- Windows : `MailFlow-Archivist-Setup.exe` ;
- macOS : `MailFlow-Archivist.dmg`.

Dans l'application, le bouton `Rechercher mise a jour` interroge la derniere release
GitHub, telecharge l'installateur adapte a la plateforme et le lance apres confirmation.

Pour publier une release :

```powershell
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
```

Voir `docs/release.md` pour les details.
