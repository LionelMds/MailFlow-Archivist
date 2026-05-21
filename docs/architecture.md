# Architecture

Le code separe les zones a risque des fonctions pures :

- `core` : chemins, noms de fichiers, nettoyage de corps.
- `core.background_watcher` : detection pure des nouveaux mails entre deux scans.
- `core.correspondence_hierarchy` : extraction de l'entreprise d'interlocuteur et
  calcul des trois dossiers metier selon l'entreprise et la chronologie des offres.
- `core.contact_directory` : extraction des contacts Outlook et preparation des
  observations pour l'annuaire.
- `core.folder_tree` : construction de l'arborescence proposee et reecriture
  non destructive des destinations lors des renommages ou fusions.
- `core.project_html_exporter` : generation du journal HTML projet et export des
  pieces jointes liees.
- `classifier` : regles, IA et fusion de decision.
- `outlook` : adaptateurs `pywin32`, scanner et exporteur mockables.
- `storage` : journal SQLite, apprentissage manuel et annuaire entreprises/domaines.
- `ui` : interface PySide6.

Les tests unitaires ciblent d'abord les fonctions pures. Outlook et OpenAI sont accessibles par injection de dependances afin de pouvoir les mocker.
