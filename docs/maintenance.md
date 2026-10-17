# Maintenance d'os-appalias

Document pour le propriétaire. L'installation courante est dans le
[README](../README.md).

## Installation par copie (test sur une box)

Copier le projet sur la box, puis en root :

```sh
sh tools/install-copy.sh -n   # voir ce qui sera fait
sh tools/install-copy.sh
```

Désinstallation : `sh tools/uninstall-copy.sh` (config.xml et
`/var/db/appalias` sont conservés).

## Tests

- Tests unitaires (stdlib), depuis la racine du dépôt :

```sh
python -I -B -m unittest discover -s tests
```

- `tools/phase0.sh` : vérifications manuelles sur une box (survie d'un alias
  External au `filter reload`, latence de `pfctl -T add`, imbrication dans un
  groupe, transmission d'Unbound). Lecture seule ou réversible.

## Bits exécutables

Git sous Windows n'enregistre pas les bits exécutables. Avant de construire un
paquet à la main, ou une fois avant le premier commit :

```sh
sh tools/fix-modes.sh -n   # liste des fichiers concernés
sh tools/fix-modes.sh      # chmod 0755
git update-index --chmod=+x <fichiers listés par fix-modes.sh -n>
```

## Prérequis GitHub (bloquants avant la première publication)

1. *Settings > Environments > github-pages > Deployment branches and tags* :
   ajouter la règle de tag `v*`.
2. Créer l'environnement `release` avec **Required reviewers** (le
   propriétaire), limité à `main` et aux tags `v*`.
3. Dans cet environnement, créer le secret `PKG_SIGNING_KEY` (clé privée RSA
   4096, PEM), puis **supprimer le secret de niveau dépôt** du même nom.

Autres réglages GitHub :

- Ruleset de tags `v*` : création, mise à jour et suppression réservées au
  propriétaire.
- Ruleset `main` : suppression et force-push (non-fast-forward) bloqués, pull
  request obligatoire, selon la politique de validation de `CONTRIBUTING.md`.
- Auto-merge désactivé, fusion en squash seulement.
- Checks requis : `lint`, `test`, `build (FreeBSD:14:amd64)` et
  `build (FreeBSD:15:amd64)` (noms stables, indépendants des SHA d'Actions).
- Secret scanning et push protection.
- « Dependabot alerts » et « Dependabot security updates ».
- *Actions > General > Workflow permissions* : « Read repository contents and
  packages permissions », et « Allow GitHub Actions to create and approve pull
  requests » décoché.
- « Require approval for all external contributors » pour les PR de forks.
- Actions autorisées : uniquement les Actions GitHub, `vmactions/freebsd-vm`
  et `zizmorcore/zizmor-action`, avec épinglage par SHA obligatoire.
- Environnements `github-pages` et `release` : branches `main` et tags `v*`,
  sans contournement par les administrateurs ; le propriétaire est approbateur
  de `release`.
- Signalement privé des vulnérabilités activé.

## Publier une version

1. Augmenter `PLUGIN_VERSION` dans `net/appalias/Makefile`, via une PR.
2. Poser puis pousser le tag `vX.Y` correspondant. Le job `publish` échoue si
   le tag diffère de `v` + `PLUGIN_VERSION`.
3. Le workflow vérifie (`lint`, `test`), construit un paquet non signé par ABI
   (job `build`, sans secret), attend l'approbation de l'environnement
   `release`, signe (job `sign`), puis déploie sur Pages (job `publish`). Les
   PR ne font que `lint`, `test` et `build`. Un `workflow_dispatch` depuis
   `main` republie aussi.

Le paquet attendu s'appelle `os-appalias-<VERSION>.pkg` (suivi de
`_<PLUGIN_REVISION>` si défini). Le build passe `PLUGIN_DEVEL=` pour retirer
le suffixe `-devel` ; tout autre nom fait échouer le build.

**Toujours publier les deux ABI** (`FreeBSD:14:amd64`, `FreeBSD:15:amd64`) :
si le répertoire de l'ABI du client manque, `pkg update` échoue. Chaque paquet
est construit sur la base FreeBSD de son ABI. Ces répertoires, qui contiennent
des `:`, ne sont créés que par la CI : ne jamais les commiter.

## Mise à jour des commits épinglés

- **opnsense/plugins** : le champ `plugins_commit` de la matrice du job
  `build` (`.github/workflows/build.yml`) épingle un commit par ABI
  (`stable/26.1`, `stable/26.7`). Pour les relever :

```sh
git ls-remote https://github.com/opnsense/plugins refs/heads/stable/26.1 refs/heads/stable/26.7
```

  Recopier chaque SHA de 40 caractères, puis commiter via une PR.
  `scripts/build-packages.sh` récupère exactement ce commit et vérifie
  `git rev-parse HEAD`.
- **Actions** : chaque `uses:` est épinglé par SHA de 40 caractères (version
  en commentaire). Dependabot propose les mises à jour ; elles sont relues et
  fusionnées à la main.

## Rotation de clé

1. Générer la nouvelle paire **hors du dépôt** (par exemple `~/.appalias/`) :
   `openssl genrsa -out ~/.appalias/appalias.key 4096`, puis
   `openssl rsa -in ~/.appalias/appalias.key -pubout -out keys/appalias.pub`.
   Les fichiers `*.key` et `*.pem` sont ignorés par git.
2. Remplacer le secret `PKG_SIGNING_KEY` de l'environnement `release`.
3. Calculer l'empreinte (`sha256sum keys/appalias.pub`, ou
   `sha256 -q keys/appalias.pub` sous FreeBSD), puis mettre à jour **les
   trois** : `EXPECTED_SHA256` dans `tools/add-repo.sh`, l'empreinte du
   `README.md` (seule sur sa ligne, sans autre texte : la CI la cherche avec
   `grep -qx`) et `keys/appalias.pub`. Le job `publish` échoue si l'une
   diffère.
4. Détruire la copie locale de la clé privée (ou l'archiver hors ligne).
5. Republier. Chaque client relance la commande d'installation ; tant qu'il
   garde l'ancienne clé, `pkg update` échoue sur la signature.

## Chaîne de sécurité et modèle de confiance

- **Build sans secret** : le job `build` ne reçoit aucun secret et ne produit
  qu'un `.pkg` non signé.
- **Signature isolée** : le job `sign` ne tourne que sur un tag `v*` ou un
  `workflow_dispatch` depuis `main`, jamais sur une PR. Il exige l'approbation
  de l'environnement `release`, n'exécute que `scripts/sign-repo.sh` (ni clone
  ni make), et `PKG_SIGNING_KEY` n'est passé qu'à l'étape de signature. La clé
  est écrite hors du workspace en 0600 puis supprimée.
- **Actions épinglées** par SHA ; le job `lint` lance zizmor sur les
  workflows et fait échouer la CI.
- **opnsense/plugins épinglé** par commit.
- **Côté client** : `add-repo.sh` refuse toute clé dont l'empreinte diffère de
  `EXPECTED_SHA256`, puis `pkg` vérifie la signature du catalogue avec cette
  clé.
- **Modèle de confiance** : la première installation fait confiance au script
  récupéré en HTTPS sur GitHub Pages, donc à la sécurité du compte GitHub du
  propriétaire (confiance à la première utilisation). Un compte compromis peut
  publier un script modifié, donc une autre empreinte ; seule une comparaison
  avec l'empreinte du README, obtenue par un autre canal, protège contre cela.
  La signature protège les mises à jour suivantes, pas la première
  installation.

## Dépannage

- **Network groups et alias imbriqués** : ils peuvent ne pas suivre les ajouts
  dynamiques d'un alias `APP_*`. À vérifier avec `tools/phase0.sh`.
- **Renommer une application** (ou changer le préfixe) est refusé tant que
  l'alias est utilisé (règle, NAT, groupe d'alias) : retirer d'abord ces
  références. Supprimer une application dont l'alias est utilisé ne supprime
  pas l'alias, il est seulement vidé.
- **Code de sortie 78** : configuration invalide ou absente du démon (amont
  manquant, « any », port hors plage). Le démon journalise l'erreur dans
  syslog et `daemon(8)` le relance toutes les 10 s : corriger la
  configuration puis cliquer sur « Appliquer ». Au rechargement (SIGHUP), une
  configuration invalide est refusée et l'ancienne reste active.
