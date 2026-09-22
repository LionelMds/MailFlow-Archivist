# Architecture

Le code separe les zones a risque des fonctions pures :

- `core` : chemins, noms de fichiers, nettoyage de corps.
- `core.background_watcher` : detection pure des nouveaux mails entre deux scans.
- `core.correspondence_hierarchy` : extraction de l'entreprise d'interlocuteur et
  normalisation vers les trois dossiers metier, puis ajout du dossier entreprise.
- `core.contact_directory` : extraction des contacts Outlook et preparation des
  observations pour l'annuaire.
- `core.folder_tree` : construction de l'arborescence proposee et reecriture
  non destructive des destinations lors des renommages ou fusions.
- `core.project_html_exporter` : generation du journal HTML projet et export des
  pieces jointes liees.
- `classifier.routing_context` : interlocuteur principal, autorite de l'annuaire,
  historique commercial et exemples manuels verifies.
- `classifier` : sortie IA structuree a trois categories, garde-fous metier et decision.
- `outlook` : adaptateurs `pywin32`, scanner et exporteur mockables.
- `storage` : journal SQLite, exemples de routage verifies et annuaire entreprises/domaines.
- `ui` : interface PySide6.

## Classification et réactivité

La politique de classement reste indépendante du modèle : entreprise et rôle viennent
de l'annuaire, la phase commerciale est proposée par l'IA, puis les contrôles locaux
valident la décision avant l'archivage. Le contexte est chronologique par projet et
entreprise ; il inclut les corrections manuelles vérifiées.

`AiClassifier` utilise Responses API et `AiMailClassification` comme contrat de sortie.
GPT-6 Astra est le défaut, avec `reasoning.effort=low`, `store=False` et une limite de
4096 tokens de sortie. Une réponse refusée, incomplète ou invalide ne devient jamais
une décision d'archivage. `ClassificationResult.ai_error` porte une explication locale
qui n'expose pas le contenu d'une exception distante.

`ui.background_call` exécute uniquement l'attente réseau dans un `QThread`. Une boucle
d'événements Qt locale conserve la réactivité pendant cet appel ; le thread est rejoint
avant le retour, et son résultat ou exception revient à l'appelant. Outlook COM, les
lectures SQLite et les mutations du contrôleur restent sur leur fil d'origine. Les
commandes qui modifieraient le lot et les timers de surveillance sont protégés contre
une opération concurrente par la fenêtre principale.

Cette approche préserve la chronologie du contexte IA. Elle ne parallélise pas tous
les mails et ne rend pas les opérations Outlook ou d'export asynchrones.

## Présentation et stockage

`ui.theme` centralise les styles de la fenêtre. La recherche masque les lignes du
tableau sans changer les indices du contrôleur. Les actions sur la sélection excluent
les lignes masquées ; l'archivage global indique son périmètre dans la confirmation.
Le réaffichage restaure les identifiants de mail, la sélection et le défilement.

`storage.connection` gère explicitement transaction et fermeture des connexions
SQLite. Les décisions ignorées restent ignorées après reclassification ; les
destinations déjà archivées ne sont pas réécrites lors d'une modification d'annuaire.
L'export HTML publie le document terminé par remplacement atomique. Les pièces jointes
homonymes sont comparées par contenu et reçoivent un suffixe si elles diffèrent.

Les tests unitaires ciblent d'abord les fonctions pures. Outlook et OpenAI sont accessibles par injection de dependances afin de pouvoir les mocker.
