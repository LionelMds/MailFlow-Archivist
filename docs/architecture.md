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
- `core.project_references` : detection pure des numeros `20XX-XXXX` et de leur
  provenance (objet, corps, nom ou contenu de piece jointe).
- `core.attachment_text` : texte des PDF (pypdf), fichiers Office Open XML et
  texte, avec limites de taille ; un fichier illisible ne donne aucun texte.
- `core.mailbox_sorting` : plan de rangement de la boite mail, suggestions de
  projet et execution (copies puis deplacement).
- `core.projectflow_link` : detection de ProjectFlow Automator et demande de creation
  des dossiers projet Outlook manquants (fichiers JSON, processus sans fenetre).
- `classifier.jev_project_matcher` : question Jev fermee sur les projets connus
  des interlocuteurs, pour les mails sans numero.
- `outlook` : adaptateurs `pywin32`, scanner et exporteur mockables ;
  `outlook.mailbox` lit les dossiers sources et deplace ou copie les mails.
- `storage` : journal SQLite, exemples de routage verifies et annuaire entreprises/domaines.
- `ui` : interface PySide6.

## Classification et réactivité

La politique de classement reste indépendante du modèle : entreprise et rôle viennent
de l'annuaire, la phase commerciale est proposée par l'IA, puis les contrôles locaux
valident la décision avant l'archivage. Le contexte est chronologique par projet et
entreprise ; il inclut les corrections manuelles vérifiées.

`AppSettings.ai_provider` sélectionne explicitement `openai`, `ollama` ou `jev`.
`build_ai_classifier` construit uniquement le moteur choisi et ne lit que sa propre
clé. Les adaptateurs partagent `build_ai_payload` et le contrat
`AiMailClassification`, puis les mêmes garde-fous et contrôles d'archivage.

`OllamaClassifier` appelle l'API native `/api/chat` avec un schéma JSON, sans streaming
ni raisonnement exposé. Le schéma reprend les contraintes déjà imposées par l'annuaire
(rôle, entreprise confirmée, Correspondance pour un client et vérification si rôle
inconnu). Ces valeurs sont aussi vérifiées au retour ; le modèle choisit la phase
commerciale du fournisseur à partir du mail et de son historique.
Le client HTTP ignore les proxys et redirections, valide
l'adresse locale et les métadonnées du modèle. Une sortie invalide ou tronquée,
un modèle absent ou une indisponibilité n'entraîne aucune bascule vers OpenAI.

`JevClassifier` appelle `POST /v1/systemone` de TypeSafe avec `httpx`. Jev renvoie des
probabilités sur une liste fermée : une seule question `choice` propose les phases
permises par le rôle de l'annuaire (`SUPPLIER_PHASES`, `CLIENT_PHASES` ou, rôle
inconnu, `UNKNOWN_ROLE_PHASES`). La probabilité d'une catégorie est la somme de ses
phases et sert de confiance ; un client reçoit `Correspondance` avec une confiance de
1. Le résumé et la raison sont écrits localement à partir de la phase retenue. Les
codes HTTP sont traduits en messages locaux (`JevError`) sans reprendre le corps de
réponse ; un seul nouvel essai est fait pour 408, 429, 5xx, délai ou coupure réseau.

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

## Rangement de la boîte mail

`MailboxSortService.analyze` lit les mails posés directement dans la boîte de
réception, le dossier à classer et les éléments envoyés, sur le fil principal comme
tout accès Outlook. L'index des dossiers projet vient du parcours récursif déjà
utilisé par l'import de l'annuaire. Un numéro ne désigne une destination que si son
dossier Outlook existe ; les autres sont signalés. L'analyse ne modifie rien.

`execute` n'accepte que les destinations proposées par l'analyse. Pour un mail qui
cite plusieurs projets, les copies sont créées avant le déplacement de l'original,
dont la référence Outlook peut changer une fois déplacé. Aucune suppression n'est
possible par ce chemin.

Les suggestions Jev passent par `ui.background_call.ResponsiveProjectSuggester` :
seules les métadonnées détachées et les numéros candidats traversent le fil de
travail. Les candidats viennent de `SQLiteDirectoryStore.projects_for_email`,
limités aux projets dont le dossier Outlook existe. Une erreur Jev arrête les
suggestions sans bascule vers un autre moteur.

## Lien avec ProjectFlow

ProjectFlow reste le seul à nommer et créer les dossiers projet. `ProjectFlowLink`
lance `ProjectFlowAutomator.exe --mailflow-request ... --mailflow-result ...` et
attend sa fin dans un fil de travail (`ui.background_call.run_with_event_loop`) :
aucun objet Outlook ne traverse ce fil. Ensuite, `refresh_project_folders` relit
l'index des dossiers sur le fil principal et `replan_proposal` recalcule les
destinations à partir des numéros déjà trouvés, sans relire les mails. La version
installée vient de l'enregistrement de l'installateur ; une version trop ancienne
désactive le bouton. Le format d'échange est décrit dans [projectflow.md](projectflow.md).

## Présentation et stockage

`ui.theme` centralise les styles de la fenêtre. La recherche masque les lignes du
tableau sans changer les indices du contrôleur. Les actions sur la sélection excluent
les lignes masquées ; l'archivage global indique son périmètre dans la confirmation.
Le réaffichage restaure les identifiants de mail, la sélection et le défilement.

`storage.connection` gère explicitement transaction et fermeture des connexions
SQLite. `storage.migrations` versionne le schéma de chaque magasin (`archive`,
`directory`, `learning`) dans la table `schema_versions` du fichier partagé : une
évolution de schéma s'ajoute en fin de liste, reste idempotente et ne modifie jamais
une migration déjà publiée. Une base plus récente que l'application est laissée intacte.

Les réglages sont écrits par remplacement atomique. Un `config.json` illisible est
renommé en `config.corrupt-<date>.json` et l'application démarre avec les réglages par
défaut en l'indiquant à l'utilisateur ; les clés retirées ou inconnues sont ignorées.
`ui.single_instance` empêche deux fenêtres MailFlow de surveiller et d'archiver en
parallèle. Les décisions ignorées restent ignorées après reclassification ; les
destinations déjà archivées ne sont pas réécrites lors d'une modification d'annuaire.
L'export HTML publie le document terminé par remplacement atomique. Les pièces jointes
homonymes sont comparées par contenu et reçoivent un suffixe si elles diffèrent.

Les tests unitaires ciblent d'abord les fonctions pures. Outlook et OpenAI sont accessibles par injection de dependances afin de pouvoir les mocker.
