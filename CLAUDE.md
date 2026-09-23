# MailFlow Archivist

Application desktop PySide6 (Windows, build macOS) qui archive les mails Outlook
classés par projet vers les dossiers locaux Balz Metal Sa. Code dans `src/mailflow`,
documentation en français dans `README.md` et `docs/`.

## Commandes

Utiliser `.venv312` : `python` peut pointer vers l'alias Microsoft Store.

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv312\Scripts\python.exe -m pytest -q
.\.venv312\Scripts\python.exe -m ruff check .
.\.venv312\Scripts\python.exe -m mypy src tests
```

La CI (`.github/workflows/ci.yml`) lance ces trois contrôles sur Windows et macOS ;
`release.yml` la réutilise avant de construire les installateurs sur un tag `vX.Y.Z`.

## Règles métier à préserver

- Non destructif : aucun mail Outlook supprimé, aucun fichier écrasé sans confirmation.
- Trois catégories seulement : `Correspondance`, `Demande de prix`, `Commande`.
  L'annuaire fixe l'entreprise et son rôle ; l'IA ne choisit que la phase commerciale.
- Un client va toujours en Correspondance, un fournisseur jamais.
- Aucun classement de secours par mots-clés ; un échec IA laisse le mail à vérifier.
- Une panne Ollama ne bascule jamais vers OpenAI.
- La clé OpenAI reste dans `keyring`, jamais dans `config.json` ni dans les logs.

## Conventions

- Outlook COM, SQLite et le contrôleur restent sur le fil principal ; seule l'attente
  réseau IA passe par `ui.background_call`.
- Nouveau réglage : champ dans `AppSettings` (`config.py`). Les clés inconnues sont
  ignorées au chargement, donc un champ retiré ne casse pas les anciennes configs.
- Évolution de schéma SQLite : ajouter une migration en fin de liste dans le magasin
  concerné (voir `storage/migrations.py`), jamais modifier une migration publiée.
- `ui/main_window.py` est une grande fonction à fermetures ; les tests accèdent aux
  widgets via les attributs `window.mailflow_*`. Les conserver lors d'un refactoring.
- Textes UI en français ; beaucoup de chaînes existantes sont sans accents.
- Mypy strict et Ruff (ligne de 100) doivent rester verts.
