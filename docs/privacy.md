# Confidentialite

Le MVP limite les donnees envoyees a l'IA :

- metadonnees du mail ;
- sujet ;
- expediteur et destinataires ;
- noms des pieces jointes ;
- extrait nettoye du corps, limite a 8000 caracteres.

La cle API OpenAI est stockee via `keyring` et n'est jamais ecrite dans les logs ou le fichier de configuration JSON.

Le mode IA peut etre desactive. L'utilisateur peut aussi ne pas envoyer l'extrait du
corps du mail et masquer les numeros de telephone avant appel a l'API.
