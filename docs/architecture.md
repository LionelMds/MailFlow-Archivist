# Architecture

Le code separe les zones a risque des fonctions pures :

- `core` : chemins, noms de fichiers, nettoyage de corps.
- `classifier` : regles, IA et fusion de decision.
- `outlook` : adaptateurs `pywin32`, scanner et exporteur mockables.
- `storage` : journal SQLite.
- `ui` : interface PySide6.

Les tests unitaires ciblent d'abord les fonctions pures. Outlook et OpenAI sont accessibles par injection de dependances afin de pouvoir les mocker.

