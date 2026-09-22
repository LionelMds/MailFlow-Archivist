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

## Parcours de travail

1. Ouvrir **Réglages** pour définir le dossier local des projets et tester l'IA.
2. Choisir le compte Outlook, le dossier source et l'année, puis **Scanner Outlook**.
   Cocher les projets à analyser dans la fenêtre qui apparaît.
3. Dans **Mails**, utiliser la recherche et le filtre **À vérifier**. Sélectionner un
   mail pour lire son contenu, sa destination et l'explication du classement dans
   l'aperçu. **Vérifier la sélection** ouvre la correction manuelle.
4. Sélectionner les mails prêts et utiliser **Archiver**. Le menu du bouton propose
   également **Archiver tous les mails prêts**, après confirmation sur l'ensemble du
   lot. Cette action globale inclut les mails prêts masqués par un filtre ; l'action
   sur la sélection ne traite que les lignes visibles sélectionnées.

Les filtres **Prêts à archiver**, **Ignorés** et **Archivés** permettent de retrouver
chaque état. **Effacer les filtres** réaffiche le lot. Une recherche ne relance pas
l'IA. Les corrections conservent la ligne active et la position de défilement.

**Ctrl+F** place le curseur dans la recherche. **Ctrl+Entrée** archive la sélection
admissible après confirmation. Les détails du mail et le **Bilan du projet** sont
séparés dans l'inspecteur ; les séparateurs permettent d'ajuster leur largeur.

Pendant une analyse, l'interface reste réactive à l'attente de l'IA. Les commandes
de modification sont désactivées jusqu'à la fin pour protéger le lot en cours.

## Choisir une IA locale

Dans **Réglages**, choisir **Ollama (local)**, puis le modèle installé sur le PC.
L'adresse habituelle est `http://127.0.0.1:11434`. Actualiser la liste des modèles
si nécessaire, lancer **Tester IA locale**, puis enregistrer les réglages.
Aucune clé API n'est nécessaire. Le test utilise un mail fictif et laisse
l'interface réactive pendant le chargement du modèle.

Si Ollama est arrêté ou le modèle absent, le statut indique le problème ; les
mails restent à vérifier. Ouvrir Ollama et refaire le test. Le guide
[Ollama](ollama.md) explique l'installation et les diagnostics.
Pour revenir à l'API, choisir **OpenAI (API)** ; son modèle et sa clé sont conservés.

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

Le role d'une entreprise est fixe globalement dans l'onglet `Annuaire`. Il sert de
regle de base pour tous les projets existants et futurs. Seuls les
roles `client` et `fournisseur` permettent l'archivage automatique; les autres restent
a verifier. Une correction manuelle depuis une ligne peut modifier le role du mail
concerne sans modifier le role global de l'annuaire ni les autres mails du projet.
Pour un mail envoye, le premier destinataire externe prime sur les collegues Balz Metal
places ensuite en copie.

## Arborescence proposee

Apres le scan, le panneau `Arborescence` montre les dossiers proposes avec le nombre de
mails par branche. Cette etape ne cree encore aucun fichier.

La navigation latérale sépare les mails, l'arborescence, l'annuaire et les réglages.
Le journal d'activité est repliable et les réglages restent accessibles par défilement
sur les fenêtres réduites.

Actions possibles :

- `Renommer dossier` corrige le nom du dossier selectionne, par exemple
  `METAL-FACTORY` vers `Metal Factory` ;
- `Fusionner vers...` deplace tous les mails du dossier selectionne vers un autre
  dossier deja propose, utile lorsqu'un doublon accidentel a ete detecte.
- `Ignorer la sélection` marque seulement les lignes sélectionnées comme ignorées ;
- `Rétablir les mails ignorés` remet les lignes archivables en action `Archiver`, sans
  toucher aux lignes déjà archivées ni aux lignes qui exigent une vérification.
  Seule cette action explicite réactive les mails ignorés encore admissibles.
  L'archivage global et la reclassification respectent les décisions d'ignorer.

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

La colonne `Role global` permet de definir une entreprise comme cliente, fournisseur,
intervenante externe, interne ou inconnue. Cette valeur s'applique a tous les projets.
Les anciens roles projet coherents sont repris automatiquement lors de la mise a jour.

L'import peut aussi etre lance en ligne de commande :

```powershell
python -m mailflow --import-contact-directory --account "lionel@balzmetal.ch" --outlook-root "Boite de reception"
```

## Mode IA

Dans `Réglages`, choisir `activee` pour demander une classification IA sur les
les mails. Le mode `desactivee` ne tente aucune classification locale : les lignes
restent a verifier manuellement.

Coller la cle OpenAI dans `Cle API OpenAI`, puis cliquer sur `Enregistrer cle`.
La cle est stockee dans le coffre du systeme et n'est pas sauvegardee dans le JSON.
Le modèle par défaut est **GPT-6 Astra** (`gpt-6-astra`). L'ancien défaut
`gpt-5.4-nano` est migré au chargement d'une configuration ancienne, puis cette
migration est mémorisée à l'enregistrement des paramètres. Un autre modèle déjà
configuré et le mode IA désactivé sont conservés. Le sélecteur reste modifiable.
Le délai réseau par défaut passe à 60 secondes, avec un nouvel essai possible par
le SDK. Le raisonnement est réglé sur `low` pour Astra.

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

L'enregistrement des réglages applique immédiatement le modèle, le mode IA et les
options de confidentialité aux prochaines analyses, y compris en surveillance.
Les décisions déjà affichées ne sont pas recalculées automatiquement.

En cas de refus d'accès, de quota atteint, de délai dépassé ou de réponse inexploitable,
la ligne reste à vérifier et l'aperçu indique la marche à suivre. Corriger le problème
dans **Réglages**, tester l'IA puis relancer la classification du lot. Les corrections
de classement et les contrôles métier restent nécessaires : une sortie structurée
ne garantit pas l'exactitude d'une décision.

## Mises a jour

Dans `Réglages`, le bouton `Rechercher mise a jour` verifie la derniere release
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

Avant un scan manuel, une fenetre liste tous les dossiers projet Outlook trouves pour
l'annee selectionnee. Cocher uniquement les projets a classer, ou utiliser `Tout
selectionner` et `Tout deselectionner`. Le champ `Projet` sert de preselection lorsqu'il
contient un numero, mais tous les dossiers restent visibles.

La case `Surveillance Outlook` garde l'application active pendant la journee et
controle toutes les 5 minutes tous les dossiers projet de l'annee selectionnee. La
selection effectuee pour un scan manuel ne limite jamais la surveillance. Le controle
periodique compare uniquement les `EntryID` ; MailFlow ne charge les autres metadonnees,
le corps, les pieces jointes et la classification IA que pour les nouveaux mails.

Si la fenetre MailFlow est ouverte avec une previsualisation en cours, la surveillance
se met en attente pour ne pas ecraser les corrections manuelles. Lorsqu'un nouvel
`EntryID` Outlook apparait, MailFlow l'ajoute a la previsualisation existante sans
reclassifier les mails deja connus.

Si Outlook est ferme pendant un scan, l'erreur est affichee dans les logs et la
surveillance reprendra au scan suivant lorsque Outlook sera de nouveau disponible.

Lorsque la surveillance est active, le bouton de fermeture de la fenetre masque
l'application dans la zone de notification. Pour arreter MailFlow, utiliser `Quitter`
depuis le menu de l'icone. Un clic sur l'icone rouvre la fenetre.
