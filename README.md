# MailFlow Archivist

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
- hierarchie par entreprise et dossier metier pour les destinations ;
- previsualisateur d'arborescence avec renommage et fusion avant archivage ;
- parametrage IA dans l'interface avec cle OpenAI stockee dans `keyring` et test visuel ;
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
dans un nouvel onglet/fenetre pour mieux fonctionner aussi sur macOS. Les pieces jointes
deja presentes sont reutilisees et ne sont pas ecrasees.

Les destinations proposees sont hierarchisees par dossier metier puis entreprise. Les
correspondances client vont par defaut dans `Correspondance/Entreprise/Approbation`.
Les correspondances fournisseur vont dans `Fournisseurs/Demande de prix/Entreprise`
jusqu'a la derniere offre du cycle, puis dans `Fournisseurs/Commande/Entreprise`, sauf
nouvelle demande d'offre.
Le journal HTML permet aussi de filtrer par dossier cible.

Apres le scan, le panneau `Arborescence` affiche les dossiers proposes avec le nombre
de mails. L'utilisateur peut renommer un dossier ou fusionner deux dossiers detectes
comme doublons avant toute creation de fichiers.

Les images integrees dans le corps des mails, comme les logos de signature, ne sont pas
exportees comme pieces jointes. Elles sont affichees directement dans le journal HTML.

La case `Surveillance Outlook` relance un scan toutes les 5 minutes tant que
l'application reste ouverte. En cas de nouveaux mails, MailFlow affiche la
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
rapide et economique. Il peut etre remplace dans le champ `Modele IA`.

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
```

Sur ce poste, si `python` pointe vers l'alias Microsoft Store, utiliser un Python 3.11+ explicite ou le runtime configure dans Codex.

## Releases

Le workflow GitHub Actions `.github/workflows/release.yml` construit les artefacts Windows et macOS.

Pour publier une release :

```powershell
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
```

Voir `docs/release.md` pour les details.
