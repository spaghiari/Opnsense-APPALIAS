# Contribuer à os-appalias

Merci de votre intérêt. Toute contribution est relue par le propriétaire du
dépôt (`@spaghiari`) ; rien n'est jamais validé ni fusionné automatiquement.

## Proposer une issue

Utilisez les formulaires proposés : bug, demande d'évolution, ou demande
d'ajout d'une application au catalogue. Les issues vierges sont désactivées.

- Retirez des journaux les IP, les domaines internes et tous les secrets.
- Pour une application du catalogue, les domaines suivent le format v2fly
  (`domain:` / `full:`). Un ASN n'est jamais accepté sans source vérifiable
  (lien RIPEstat ou équivalent). Une demande est toujours relue à la main.
- Une vulnérabilité ne se signale **pas** dans une issue : voir
  [SECURITY.md](SECURITY.md).

## Proposer une pull request

1. Forkez le dépôt et créez une branche dédiée.
2. Gardez la PR petite et centrée sur un seul sujet.
3. Lancez les tests (voir plus bas).
4. Ouvrez la PR et remplissez la checklist du modèle.

## Politique de validation

- Aucune fusion automatique : l'auto-merge est désactivé.
- Une approbation du propriétaire (code owner) est requise, selon
  `.github/CODEOWNERS`.
- Les approbations sont invalidées à chaque nouveau push : toute modification
  après relecture appelle une nouvelle approbation.
- L'approbation est exigée sur le dernier push, et elle ne peut pas venir de
  l'auteur de ce push.
- Les checks requis sont `lint`, `test`, `build (FreeBSD:14:amd64)` et
  `build (FreeBSD:15:amd64)`. La branche doit être à jour avec `main` avant
  la fusion.
- Tous les fils de discussion doivent être résolus avant la fusion.
- Fusion en squash uniquement.
- Pour une PR venant d'un fork, les workflows ne tournent qu'après une
  approbation manuelle.
- Les PR Dependabot sont relues et fusionnées à la main.
- Toute modification de `.github/`, `scripts/`, `keys/`, `repo/` ou
  `tools/add-repo.sh` est relue ligne à ligne : ces zones forment la chaîne de
  publication signée. Signalez-les explicitement dans la PR.

Par transparence : le propriétaire du dépôt peut contourner ces règles sur le
dépôt.

## Aucun secret

Ne mettez jamais dans une PR ou une issue : clé privée, jeton, mot de passe,
adresse IP, domaine interne, ni aucune information propre à votre installation.
Si un secret a fuité, considérez-le comme compromis et prévenez le
propriétaire via [SECURITY.md](SECURITY.md).

## Style

- Tous les textes (code commenté, documentation, messages) sont en français.
- Fichiers en UTF-8, fins de ligne LF.
- Tests en bibliothèque standard Python uniquement, sans dépendance externe.

## Lancer les tests

Depuis la racine du dépôt :

```sh
python -I -B -m unittest discover -s tests
```

## Licence

En contribuant, vous acceptez que votre contribution soit publiée sous
licence BSD-2-Clause, comme le reste du projet (voir [LICENSE](LICENSE)).

## Vulnérabilités

Voir [SECURITY.md](SECURITY.md).
