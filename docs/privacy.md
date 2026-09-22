# Confidentialite

Le MVP limite les donnees envoyees a l'IA :

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

Les appels Responses utilisent `store=False` pour ne pas demander la conservation
de la réponse comme ressource API. Les éventuelles obligations de conservation ou
journaux côté fournisseur dépendent des conditions du compte OpenAI ; cette option
ne signifie pas « aucune rétention ». Les erreurs de classification affichées dans
la prévisualisation utilisent des messages locaux sans recopier la requête distante.

La cle API OpenAI est stockee via `keyring`, dans le coffre du systeme, et n'est
jamais ecrite dans les logs ou le fichier de configuration JSON.

Le bouton `Tester IA` utilise uniquement un mail fictif de diagnostic. Aucun mail
Outlook reel ni piece jointe n'est envoye pour verifier la validite de la cle.

L'import annuaire Outlook reste local. Il stocke dans SQLite les adresses e-mail,
domaines, noms affiches et projets associes pour ameliorer le tri, sans envoyer ces
donnees a un service externe.

Le mode IA peut etre desactive. L'utilisateur peut aussi ne pas envoyer l'extrait du
corps du mail et masquer les numeros de telephone avant appel a l'API.
