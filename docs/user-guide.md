# Guide utilisateur

## Principe

MailFlow Archivist scanne les dossiers Outlook projet, propose une decision d'archivage, puis exporte les mails valides en `.msg` avec leurs pieces jointes.

Le logiciel peut aussi generer un journal HTML projet centralise dans `Correspondance`.
Ce fichier permet de parcourir les echanges avec recherche, filtres par sens, type et
interlocuteur. Les pieces jointes sont placees dans un dossier commun a cote du HTML.

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

## Export HTML projet

1. Scanner le dossier Outlook projet.
2. Verifier ou corriger la previsualisation.
3. Cliquer sur `Exporter HTML projet`.
4. Confirmer la mise a jour si le journal HTML existe deja.

Sortie attendue :

```text
[Projet]\Correspondance\2025-4893 - Correspondance projet.html
[Projet]\Correspondance\2025-4893 - pieces jointes\
  1-R-Offre garde-corps - plan.pdf
  2-E-Reponse offre - devis.xlsx
```

Le fichier HTML est mis a jour uniquement apres confirmation. Les pieces jointes
existantes sont conservees.

## Surveillance Outlook

La case `Surveillance Outlook` garde l'application active pendant la journee et relance
un scan toutes les 5 minutes. Lorsqu'un nouvel `EntryID` Outlook apparait, une
confirmation demande si le journal HTML doit etre mis a jour.

Si Outlook est ferme pendant un scan, l'erreur est affichee dans les logs et la
surveillance reprendra au scan suivant lorsque Outlook sera de nouveau disponible.
