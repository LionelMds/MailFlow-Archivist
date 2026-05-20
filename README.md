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

Le HTML regroupe les mails envoyes et recus, avec recherche, filtres et liens relatifs
vers les pieces jointes. Les pieces jointes deja presentes sont reutilisees et ne sont
pas ecrasees.

La case `Surveillance Outlook` relance un scan toutes les 5 minutes tant que
l'application reste ouverte. En cas de nouveaux mails, MailFlow demande confirmation
avant de mettre a jour le journal HTML.

Quand la surveillance est active, fermer la fenetre masque MailFlow dans la zone de
notification au lieu de l'arreter. Le menu de l'icone permet de rouvrir l'application,
d'activer ou desactiver la surveillance, ou de quitter completement.

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
