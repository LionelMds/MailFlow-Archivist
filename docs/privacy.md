# Confidentialite

Le moteur **Ollama (local)** traite les données sur le PC. MailFlow accepte uniquement
une adresse de boucle locale (`127.0.0.1`, `localhost` ou `::1`), ignore les proxys
HTTP système et refuse les redirections. Il vérifie que le modèle est installé
localement et refuse les modèles cloud avant de transmettre un mail. Une erreur
laisse le mail à vérifier, sans bascule vers OpenAI. Le téléchargement initial
d'Ollama et du modèle nécessite Internet ; la classification locale n'en a pas besoin.

Le moteur **OpenAI (API)** transmet les mêmes données à OpenAI. Le moteur
**Jev (TypeSafe)** transmet les mêmes données à l'API de TypeSafe
(`https://api.typesafe.ai`), sans suivre de redirection. Le choix du moteur est
explicite dans les réglages ; les anciennes configurations conservent OpenAI. Une panne
d'un moteur ne déclenche jamais d'envoi à un autre.

MailFlow limite les données transmises au moteur choisi :

- metadonnees du mail ;
- sujet ;
- expediteur et destinataires ;
- noms des pieces jointes ;
- extrait nettoye du corps, limite a 8000 caracteres.

La requête inclut aussi l'entreprise et son rôle issus de l'annuaire, jusqu'à six
échanges récents de la même entreprise dans le projet (sujet, résumé, catégorie), et
jusqu'à cinq exemples de corrections vérifiées pertinents. L'import de l'annuaire
reste local, mais ces éléments sélectionnés sont transmis lors d'une classification IA.
Les fichiers joints eux-mêmes ne sont pas transmis.

En mode OpenAI, les appels Responses utilisent `store=False` pour ne pas demander la conservation
de la réponse comme ressource API. Les éventuelles obligations de conservation ou
journaux côté fournisseur dépendent des conditions du compte OpenAI ; cette option
ne signifie pas « aucune rétention ». Les erreurs de classification affichées dans
la prévisualisation utilisent des messages locaux sans recopier la requête distante.

En mode Jev, le schéma public de l'API ne documente aucune option équivalente à
`store=False` ; la conservation éventuelle des requêtes dépend des conditions du compte
TypeSafe. Les
réponses d'erreur de Jev, qui peuvent recopier la requête, ne sont jamais affichées.

Les cles API OpenAI et Jev sont stockees via `keyring`, dans le coffre du systeme, sous
deux entrees distinctes, et ne sont jamais ecrites dans les logs ou le fichier de
configuration JSON.

Les boutons `Tester IA` et `Tester Jev` utilisent uniquement un mail fictif de
diagnostic. Aucun mail Outlook reel ni piece jointe n'est envoye pour verifier la
validite de la cle.

Le rangement de la boîte mail lit les mails et leurs pièces jointes localement.
Une pièce jointe dont le texte est recherché est écrite dans un dossier temporaire
privé, lue, puis effacée ; son contenu n'est ni stocké ni transmis. L'option de
suggestion Jev, désactivée par défaut, envoie à TypeSafe les mêmes données qu'une
classification (sans contexte d'annuaire), plus les numéros et noms des dossiers
projet candidats et les entreprises qui y ont déjà échangé. Les réglages d'envoi du
corps et de masquage des téléphones s'appliquent aussi à ces suggestions.

L'import annuaire Outlook reste local. Il stocke dans SQLite les adresses e-mail,
domaines, noms affiches et projets associes pour ameliorer le tri, sans envoyer ces
donnees a un service externe.

Le mode IA peut etre desactive. L'utilisateur peut aussi ne pas envoyer l'extrait du
corps du mail et masquer les numeros de telephone avant appel a l'API.
