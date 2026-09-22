# IA locale avec Ollama

MailFlow propose deux moteurs : OpenAI via API et Ollama sur le PC. Le choix se fait
dans **Réglages**. Chaque moteur conserve son modèle ; la clé OpenAI reste dans le
coffre système et n'est pas nécessaire en mode local. Les anciennes installations
conservent leur moteur OpenAI jusqu'à un changement explicite.

## Installation Windows

1. Installer [Ollama depuis son site officiel](https://ollama.com/download/windows).
   L'installateur s'exécute dans le compte Windows et fournit la désinstallation
   standard. Laisser Ollama démarrer avec la session pour qu'il soit disponible
   quand MailFlow en a besoin.
2. Télécharger le modèle dans PowerShell :

   ```powershell
   ollama pull qwen3.5:4b
   ```

3. Dans MailFlow, choisir **Ollama (local)**, garder l'adresse
   `http://127.0.0.1:11434`, puis choisir `qwen3.5:4b`.
4. Cliquer sur **Tester IA locale**, puis enregistrer les réglages.

Les modèles sont stockés par Ollama dans `%USERPROFILE%\.ollama\models`, en dehors
du dépôt de code et des dossiers de mails. Internet est nécessaire pour
l'installation et le téléchargement ; les classifications fonctionnent ensuite
sur la machine. Le premier appel charge le modèle et peut être plus lent.

## Choix du modèle

[Qwen3.5 4B](https://ollama.com/library/qwen3.5:4b), quantifié en Q4_K_M, représente
environ 3,4 Go à télécharger. C'est le modèle local proposé par MailFlow. La mémoire
nécessaire à l'exécution est supérieure à la taille du fichier et dépend du contexte.
Ollama peut répartir le travail entre la carte graphique et la RAM ; une carte
déjà occupée par d'autres logiciels réduit la vitesse disponible.

Le modèle reste modifiable : la liste affiche les modèles locaux installés et
accepte un nom saisi manuellement. Les modèles cloud sont refusés. Un autre modèle
doit accepter les sorties JSON structurées de l'API Ollama. Le test intégré vérifie
la compatibilité du format, pas la qualité de toutes les classifications.

## Confidentialité et erreurs

MailFlow ne se connecte qu'à une adresse de boucle locale, ignore les proxys et
refuse les redirections. Il vérifie les métadonnées locales du modèle avant
d'envoyer le contenu du mail. Il n'utilise aucun secours automatique OpenAI.
Les mêmes données et garde-fous métier sont utilisés par les deux moteurs.

Une connexion impossible, un modèle absent, un délai dépassé ou une réponse invalide
laisse le mail **À vérifier**. Une erreur ne devient jamais une décision d'archivage.
L'interface reste réactive durant l'attente. Le délai local par défaut est de
180 secondes par requête.

Pour désactiver également les fonctionnalités cloud dans Ollama lui-même, ajouter
`"disable_ollama_cloud": true` dans `%USERPROFILE%\.ollama\server.json` puis redémarrer
Ollama, en préservant les autres clés du fichier. Voir la
[documentation officielle](https://docs.ollama.com/faq#how-do-i-disable-ollama-cloud-features).

## Diagnostic reproductible

`ollama list` affiche les modèles installés ; `ollama ps` indique le modèle chargé
et la répartition CPU/GPU. Ouvrir Ollama si le serveur n'est plus disponible.
Le bouton d'actualisation de MailFlow recharge la liste sans analyser de mail.

Depuis le dépôt, le test réel suivant utilise uniquement dix mails synthétiques en
français et mesure les durées, les catégories et les demandes de vérification :

```powershell
.venv312\Scripts\python.exe scripts/validate_ollama.py --run --model qwen3.5:4b --output build/ollama-validation.json
```

Ce contrôle optionnel nécessite Ollama et le modèle installé. Les tests automatisés
habituels utilisent un transport HTTP simulé et n'installent aucun modèle en CI.

### Validation sur le PC cible, 22 septembre 2026

- Core i9-12900KF, 32 Go de RAM, NVIDIA Quadro P2200 de 5 Go, pilote 582.16.
- Ollama 0.34.2, `qwen3.5:4b` Q4_K_M, contexte 8192 tokens.
- Modèle chargé : environ 3,9 Go, répartition observée 28 % CPU / 72 % GPU.
- Dix scénarios synthétiques validés sur dix ; médiane 10,0 secondes par mail,
  total 102,4 secondes après chargement du modèle.
- 364 tests automatisés, Ruff et mypy strict réussis avant publication.

Ces mesures portent sur le jeu fourni, avec les autres logiciels du PC ouverts.
Elles ne garantissent ni un temps constant ni un classement parfait sur tous les mails.
Les contraintes connues de l'annuaire sont imposées dans le schéma JSON et vérifiées
au retour ; les situations incertaines continuent à demander une vérification humaine.

## Références

- [Installation Windows](https://docs.ollama.com/windows)
- [Compatibilité du matériel](https://docs.ollama.com/gpu)
- [Sorties structurées](https://docs.ollama.com/capabilities/structured-outputs)
