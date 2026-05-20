# Release

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
