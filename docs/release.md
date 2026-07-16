# Release

## Version 0.8.0

- classification semantique IA limitee a `Correspondance`, `Demande de prix` et
  `Commande` ;
- suppression complete du classement et de l'apprentissage par mots-cles ;
- entreprise et role verrouilles par l'annuaire et le destinataire externe prioritaire ;
- contexte chronologique fournisseur et exemples manuels verifies transmis a l'IA ;
- correction manuelle simplifiee et reclassification IA du projet depuis l'interface ;
- nouveau logo et nouvelles icones multi-resolution Windows/macOS ;
- progression de scan plus visible et delai OpenAI borne.

Les releases sont construites par GitHub Actions depuis un tag `v*`.

## Publier une release

```powershell
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
```

Le workflow `Build and Release` lance les tests, construit les installateurs Windows et
macOS, puis publie une GitHub Release avec les fichiers finaux directement utilisables.

Le build installe les dependances runtime, PyInstaller, puis les outils de packaging
propres a chaque plateforme. Il ne collecte pas tout PySide6 : les modules Qt lourds non
utilises, comme WebEngine, QML, Quick, Multimedia, Designer et PDF, sont exclus pour
garder des artefacts raisonnables.

Le logo officiel est embarque depuis `src/mailflow/assets` sous forme de PNG source
transparent 1024 px. Les icones plateforme sont generees en multi-resolution :
`mailflow-icon.ico` pour Windows et `mailflow-icon.icns` pour macOS. PyInstaller
applique l'icone adaptee a chaque OS, et l'installateur Windows Inno Setup reprend la
meme icone `.ico`.

## Artefacts

- `MailFlow-Archivist-Setup.exe` : installateur Windows par utilisateur, genere avec
  Inno Setup.
- `MailFlow-Archivist.dmg` : image disque macOS contenant l'application.

## Mises a jour

L'application interroge la derniere release GitHub depuis le bouton `Rechercher mise a
jour`. Si une version plus recente existe, elle selectionne automatiquement
`MailFlow-Archivist-Setup.exe` sous Windows ou `MailFlow-Archivist.dmg` sous macOS,
telecharge le fichier dans le dossier de telechargements utilisateur, puis lance
l'installateur apres confirmation.

## Notes plateforme

Les installateurs publies par GitHub Actions ne sont pas encore signes avec un
certificat editeur ni notarises Apple. Windows SmartScreen ou Gatekeeper macOS peuvent
donc afficher un avertissement au premier lancement. La signature pourra etre ajoutee
des qu'un certificat de code signing et un compte Apple Developer seront disponibles.

La version Windows est la cible principale du MVP, car l'intégration Outlook utilise Outlook
classique via COM/pywin32.

La version macOS est fournie comme paquet applicatif expérimental. Les fonctions qui dependent
de COM Outlook Windows ne sont pas disponibles sur macOS.
