# Confidentialite

Le MVP limite les donnees envoyees a l'IA :

- metadonnees du mail ;
- sujet ;
- expediteur et destinataires ;
- noms des pieces jointes ;
- extrait nettoye du corps, limite a 8000 caracteres.

La cle API OpenAI est stockee via `keyring`, dans le coffre du systeme, et n'est
jamais ecrite dans les logs ou le fichier de configuration JSON.

Le bouton `Tester IA` utilise uniquement un mail fictif de diagnostic. Aucun mail
Outlook reel ni piece jointe n'est envoye pour verifier la validite de la cle.

L'import annuaire Outlook reste local. Il stocke dans SQLite les adresses e-mail,
domaines, noms affiches et projets associes pour ameliorer le tri, sans envoyer ces
donnees a un service externe.

Le mode IA peut etre desactive. L'utilisateur peut aussi ne pas envoyer l'extrait du
corps du mail et masquer les numeros de telephone avant appel a l'API.
