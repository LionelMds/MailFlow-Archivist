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

## Mode IA

Dans `Configuration`, choisir le mode IA :

- `desactivee` pour rester en regles locales uniquement ;
- `ambigu seulement` pour appeler l'IA seulement quand les regles sont incertaines ;
- `tout classifier` pour demander une classification IA sur tous les mails.

Coller la cle OpenAI dans `Cle API OpenAI`, puis cliquer sur `Enregistrer cle`.
La cle est stockee dans le coffre Windows et n'est pas sauvegardee dans le JSON.
Le modele par defaut est `gpt-5.4-nano`; il peut etre remplace dans `Modele IA`.

Options de confidentialite :

- `Envoyer l'extrait nettoye du corps a l'IA` peut etre decoche ;
- `Masquer les numeros de telephone avant IA` remplace les numeros detectes.

Si le mode IA est actif mais qu'aucune cle n'est disponible, MailFlow continue avec
les regles locales et affiche un avertissement dans les logs.

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

Les images integrees au corps du mail sont ignorees dans la liste des pieces jointes
et affichees directement dans le HTML. Les vraies images jointes, par exemple une photo
de chantier ajoutee comme fichier, restent exportees comme pieces jointes.

## Surveillance Outlook

La case `Surveillance Outlook` garde l'application active pendant la journee et relance
un scan toutes les 5 minutes. Lorsqu'un nouvel `EntryID` Outlook apparait, une
confirmation demande si le journal HTML doit etre mis a jour.

Si Outlook est ferme pendant un scan, l'erreur est affichee dans les logs et la
surveillance reprendra au scan suivant lorsque Outlook sera de nouveau disponible.

Lorsque la surveillance est active, le bouton de fermeture de la fenetre masque
l'application dans la zone de notification. Pour arreter MailFlow, utiliser `Quitter`
depuis le menu de l'icone. Un clic sur l'icone rouvre la fenetre.
