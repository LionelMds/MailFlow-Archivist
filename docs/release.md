# Release

Les releases sont construites par GitHub Actions depuis un tag `v*`.

## Publier une release

```powershell
git tag v0.1.0
git push origin main
git push origin v0.1.0
```

Le workflow `Build and Release` lance les tests, construit les archives Windows et macOS,
puis publie une GitHub Release avec les fichiers ZIP.

## Artefacts

- `MailFlow-Archivist-windows.zip`
- `MailFlow-Archivist-macos.zip`

## Notes plateforme

La version Windows est la cible principale du MVP, car l'intégration Outlook utilise Outlook
classique via COM/pywin32.

La version macOS est fournie comme paquet applicatif expérimental. Les fonctions qui dependent
de COM Outlook Windows ne sont pas disponibles sur macOS.
