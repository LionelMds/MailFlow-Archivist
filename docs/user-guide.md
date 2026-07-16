# Guide utilisateur

## Principe

MailFlow Archivist scanne les dossiers Outlook projet, propose une decision d'archivage, puis exporte les mails valides en `.msg` avec leurs pieces jointes.

Le logiciel peut aussi generer un journal HTML projet centralise dans `Correspondance`.
Ce fichier reprend la meme arborescence que la previsualisation MailFlow. Il permet de
parcourir les echanges par dossier, avec recherche et filtres par sens, type,
interlocuteur et dossier cible. Les pieces jointes sont placees dans un dossier commun
a cote du HTML.

L'inspecteur MailFlow et le journal HTML affichent aussi un `Resume projet` : points
globaux, partie clients, partie fournisseurs, commandes detectees et problemes ou
reclamations lorsqu'il y en a. Ce recap est calcule depuis les mails scannes et les
decisions de classement deja visibles.

Avant tout archivage, l'utilisateur garde la main sur :

- la racine locale des projets ;
- le compte Outlook ;
- l'annee et les projets a scanner ;
- les decisions proposees ;
- les conflits de fichiers.

## Hierarchie des dossiers

Apres la classification, MailFlow propose une destination par entreprise
d'interlocuteur, limitee a trois dossiers metier :

- correspondance client : `Correspondance/Entreprise` ;
- demandes de prix, demandes d'offre, RFQ, devis et offres fournisseur :
  `Fournisseurs/Demande de prix/Entreprise` ;
- commandes, confirmations, factures, livraisons et suivis directement lies a une
  commande fournisseur :
  `Fournisseurs/Commande/Entreprise`.

`Correspondance` n'est pas utilise pour les fournisseurs. Si un mail fournisseur n'est
pas clairement une demande de prix/offre ou une commande/suivi de commande, la ligne
reste `A verifier` afin d'etre rangee manuellement dans le bon dossier fournisseur.
L'annuaire decide de l'entreprise et de son role. L'IA decide uniquement de la phase
metier parmi les trois categories, puis MailFlow ajoute le dossier de l'entreprise.

L'entreprise est d'abord resolue depuis l'annuaire local. Celui-ci associe les domaines
et adresses e-mail aux entreprises, par exemple `gva.ch -> AIG`. Si aucun lien n'est
connu, MailFlow utilise le texte entre parentheses quand Outlook le fournit, le nom
d'entreprise explicite, puis le domaine e-mail. Pour les fournisseurs, MailFlow evite
de nommer le dossier avec le nom d'une personne et privilegie le nom de l'entreprise.
La destination reste modifiable manuellement dans la previsualisation. Les sous-dossiers
de destination sont crees si le dossier projet existe deja ; le dossier projet lui-meme
n'est jamais cree automatiquement.

Le role d'une entreprise peut etre fixe par projet dans l'onglet `Annuaire`. Seuls les
roles `client` et `fournisseur` permettent l'archivage automatique; les autres restent
a verifier. Ce role prime sur la reponse IA pour toutes les lignes du projet scanne.
Pour un mail envoye, le premier destinataire externe prime sur les collegues Balz Metal
places ensuite en copie.

## Arborescence proposee

Apres le scan, le panneau `Arborescence` montre les dossiers proposes avec le nombre de
mails par branche. Cette etape ne cree encore aucun fichier.

L'interface principale est scrollable : les panneaux ouverts gardent une hauteur lisible
et les panneaux reduits ne prennent que leur en-tete. Les separateurs restent
deplacables pour ajuster la place donnee a la previsualisation, a l'arborescence, a
l'apercu du mail et aux logs.

Actions possibles :

- `Renommer dossier` corrige le nom du dossier selectionne, par exemple
  `METAL-FACTORY` vers `Metal Factory` ;
- `Fusionner vers...` deplace tous les mails du dossier selectionne vers un autre
  dossier deja propose, utile lorsqu'un doublon accidentel a ete detecte.
- `Ignorer selection` marque seulement les lignes selectionnees comme ignorees ;
- `Tout remettre a archiver` remet les lignes archivables en action `Archiver`, sans
  toucher aux lignes deja archivees ni aux lignes qui exigent une verification. Cette
  action force aussi la decision interne d'archivage pour permettre de re-archiver un
  mail deja vu dans SQLite ou marque comme archive dans Outlook.

Les changements sont appliques a la previsualisation et au futur export/archivage.
Ils restent modifiables tant que l'utilisateur n'a pas confirme l'archivage ou l'export.

## Regles de securite

- Les mails restent dans Outlook.
- Les dossiers projet manquants bloquent l'archivage du projet.
- Les fichiers existants ne sont pas remplaces automatiquement.
- Les pieces jointes completes ne sont pas envoyees a l'IA dans le MVP.

## Annuaire

Le bouton `Importer annuaire Outlook` scanne tous les dossiers projet sous la racine
Outlook selectionnee. L'import est non destructif : il lit les mails, extrait les
adresses, domaines, noms affiches et numeros projet, puis alimente la base SQLite
locale. Les domaines internes Balz Metal sont ignores. Les domaines generiques comme
`gmail.com`, `outlook.com` ou `icloud.com` ne sont pas generalises a toute une
entreprise sauf si le nom affiche contient clairement une societe.

L'import peut aussi etre lance en ligne de commande :

```powershell
python -m mailflow --import-contact-directory --account "lionel@balzmetal.ch" --outlook-root "Boite de reception"
```

## Mode IA

Dans `Configuration`, choisir `activee` pour demander une classification IA sur tous
les mails. Le mode `desactivee` ne tente aucune classification locale : les lignes
restent a verifier manuellement.

Coller la cle OpenAI dans `Cle API OpenAI`, puis cliquer sur `Enregistrer cle`.
La cle est stockee dans le coffre du systeme et n'est pas sauvegardee dans le JSON.
Le modele par defaut est `gpt-5.4-nano`. La liste `Modele IA` propose aussi
`gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, `gpt-4o-mini` et `gpt-4o`; elle reste
editable pour saisir un autre modele compatible Structured Outputs.

Le bouton `Tester IA` verifie la cle et le modele avec un mail fictif. Le statut
s'affiche directement a cote du champ : non testee, test en cours, valide ou invalide.
Ce test ne lit aucun mail Outlook.

Dans l'apercu du mail selectionne, MailFlow affiche aussi la decision IA lorsqu'elle a
ete appelee : action proposee, type, interlocuteur, dossier cible, confiance, resume et
raison courte.

Options de confidentialite :

- `Envoyer l'extrait nettoye du corps a l'IA` peut etre decoche ;
- `Masquer les numeros de telephone avant IA` remplace les numeros detectes.

Si le mode IA est actif mais qu'aucune cle n'est disponible, MailFlow conserve les
lignes en verification et affiche un avertissement dans les logs.

## Mises a jour

Dans `Configuration`, le bouton `Rechercher mise a jour` verifie la derniere release
publiee sur GitHub. Si une version plus recente existe, MailFlow propose de telecharger
et lancer l'installateur adapte :

- Windows : `MailFlow-Archivist-Setup.exe` ;
- macOS : `MailFlow-Archivist.dmg`.

Le fichier est telecharge dans le dossier de telechargements utilisateur, dans un
sous-dossier `MailFlow Archivist Updates`. Fermer MailFlow pendant l'installation si
l'installateur le demande.

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

Le fichier HTML est mis a jour uniquement apres confirmation. Il affiche un panneau
`Arborescence` a gauche et les mails groupes sous leurs dossiers cibles a droite. Un
clic sur une branche, par exemple `Fournisseurs/Demande de prix`, affiche aussi les sous-dossiers et
mails contenus dans cette branche. L'ordre d'affichage reprend l'ordre metier :
correspondance client, demandes de prix fournisseurs, puis commandes fournisseurs.

Le bloc `Resume projet` en haut du HTML reprend les elements utiles du scan :
periode couverte, interlocuteurs principaux, derniers points marquants, points clients,
points fournisseurs, commandes/suivis de commande et alertes de type probleme,
reclamation, retard ou non-conformite. Il utilise les sujets, extraits nettoyes,
dossiers cibles, roles d'annuaire et resumes IA deja generes quand ils existent.

Les pieces jointes existantes sont conservees. Les liens vers les pieces jointes sont
relatifs au fichier HTML, prefixes par `./` et ouverts dans un nouvel onglet/fenetre
pour mieux fonctionner sur Windows et macOS. L'export ajoute aussi un lien local direct
dans le HTML et, sous Windows, demande a OneDrive de rendre les pieces jointes exportees
disponibles localement lorsqu'elles sont dans un dossier OneDrive.

Les images integrees au corps du mail sont ignorees dans la liste des pieces jointes
et affichees directement dans le HTML. Les vraies images jointes, par exemple une photo
de chantier ajoutee comme fichier, restent exportees comme pieces jointes.

## Surveillance Outlook

La case `Surveillance Outlook` garde l'application active pendant la journee et relance
un scan toutes les 5 minutes. Si la fenetre MailFlow est ouverte avec une previsualisation
en cours, la surveillance se met en attente pour ne pas ecraser les corrections manuelles.
Lorsqu'elle peut scanner et qu'un nouvel `EntryID` Outlook apparait, MailFlow met a jour
la previsualisation et le panneau `Arborescence`, puis affiche la fenetre. L'utilisateur
peut choisir de mettre a jour le journal HTML immediatement, ou refuser pour verifier et
corriger l'arborescence avant export.

Si Outlook est ferme pendant un scan, l'erreur est affichee dans les logs et la
surveillance reprendra au scan suivant lorsque Outlook sera de nouveau disponible.

Lorsque la surveillance est active, le bouton de fermeture de la fenetre masque
l'application dans la zone de notification. Pour arreter MailFlow, utiliser `Quitter`
depuis le menu de l'icone. Un clic sur l'icone rouvre la fenetre.
