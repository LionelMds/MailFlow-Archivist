# Guide utilisateur

## Principe

MailFlow Archivist scanne les dossiers Outlook projet, propose une decision d'archivage, puis exporte les mails valides en `.msg` avec leurs pieces jointes.

Avant tout archivage, l'utilisateur garde la main sur :

- la racine locale des projets ;
- le compte Outlook ;
- l'annee et les projets a scanner ;
- les decisions proposees ;
- les conflits de fichiers.

## Regles de securite

- Les mails restent dans Outlook.
- Les dossiers projet manquants bloquent l'archivage du projet.
- Les fichiers existants ne sont pas remplaces automatiquement.
- Les pieces jointes completes ne sont pas envoyees a l'IA dans le MVP.

