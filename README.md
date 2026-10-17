# os-appalias

Plugin OPNsense : **un alias pare-feu par application**, rempli tout seul.

Pour chaque application déclarée (par exemple `EXEMPLE`), le plugin crée un
alias de type *External* `APP_EXEMPLE`. Un relais DNS local, `appaliasd`,
voit passer les réponses DNS. Quand un nom correspond à l'application, il
ajoute ses adresses A/AAAA à la table pf de l'alias, **avant** de rendre la
réponse au client. Des réseaux CIDR saisis à la main complètent l'alias.

Le plugin ne crée **aucune règle**. Vous employez les alias `APP_*` dans vos
propres règles : blocage, autorisation, routage par passerelle (PBR).

## Fonctionnement

```
client -> Unbound/Dnsmasq (pare-feu) -> appaliasd 127.0.0.1:53535 -> résolveur amont
                                            |
                                            +-> pfctl -t APP_X -T add <IP> (avant la réponse)
```

- Requêtes transmises **telles quelles** (UDP et TCP). Une réponse UDP
  tronquée est rendue telle quelle : le client réessaie en TCP.
- Règles de domaine : `example.com` ou `domain:example.com` (domaine et
  sous-domaines), `full:www.example.com` (ce nom exactement).
  `keyword:` et `regexp:` sont acceptés mais pas encore appliqués.
- Option « Suivre les CNAME » : les noms de la chaîne CNAME comptent aussi.
- Une IP reste dans l'alias jusqu'à `maintenant + max(TTL, TTL minimal) + marge`
  (300 s et 3600 s par défaut). Un balayage toutes les 30 s retire les IP
  expirées ; les CIDR manuels ne sont jamais retirés.
- Les IP des réseaux exclus (privés, locaux, etc.) ne sont jamais ajoutées.
- L'état (`/var/db/appalias/state.json`) est sauvegardé toutes les 60 s et à
  l'arrêt ; au démarrage, les tables sont rechargées avant d'ouvrir l'écoute.

## Installation

### Installer depuis le dépôt (recommandé)

Le dépôt pkg est publié sur GitHub Pages
(`https://spaghiari.github.io/Opnsense-APPALIAS/`), signé par clé publique.
Il sert deux ABI : `FreeBSD:14:amd64` (OPNsense 26.1) et `FreeBSD:15:amd64`
(OPNsense 26.7). En root sur le pare-feu, commande en une ligne (compatible
tcsh, le shell root d'OPNsense) :

```sh
fetch -o /tmp/add-repo.sh https://spaghiari.github.io/Opnsense-APPALIAS/add-repo.sh && sh /tmp/add-repo.sh
```

Le script refuse une ABI autre que celles ci-dessus, télécharge la clé et la
configuration du dépôt, affiche l'empreinte SHA256 de la clé publique et la
**refuse** si elle diffère de la constante `EXPECTED_SHA256` du script, puis
lance `pkg update -f -r appalias` (en cas d'échec, les deux fichiers sont
retirés). L'empreinte attendue est :

```
325cde334cd45a513bbf8598d51627c991b78bc20379d5648fc4a499ac40a4b3
```

Pour la comparer à la main : sur le pare-feu, `sha256 -q /usr/local/etc/pkg/keys/appalias.pub` ;
dans le dépôt, `sha256sum keys/appalias.pub` (Linux, Git Bash).

Ensuite, dans *System > Firmware > Plugins*, cliquer sur « Check for
updates » : `os-appalias` apparaît dans la liste (le pare-feu affiche tout
paquet `os-*` des dépôts activés, sans priorité à régler). Pour retirer le
dépôt (clé et configuration) :

```sh
fetch -o /tmp/add-repo.sh https://spaghiari.github.io/Opnsense-APPALIAS/add-repo.sh && sh /tmp/add-repo.sh -u
```

#### Prérequis GitHub (BLOQUANT avant la première publication)

Sans ces réglages, la publication échoue ou, pire, la clé reste exposée :

1. *Settings > Environments > github-pages > Deployment branches and tags* :
   ajouter la règle de tag `v*` (sinon le déploiement d'un tag est refusé).
2. Créer l'environnement `release` avec **Required reviewers** (le
   propriétaire) et *Deployment branches and tags* limité à `main` et aux tags
   `v*`.
3. Dans cet environnement, créer le secret `PKG_SIGNING_KEY` (clé privée RSA
   4096, PEM), puis **supprimer le secret de niveau dépôt** du même nom.

#### Publier une version

1. Augmenter `PLUGIN_VERSION` dans `net/appalias/Makefile`, et commiter (via
   une pull request : `main` est protégée).
2. Poser le tag `vX.Y` correspondant (par exemple `v0.2` pour `0.2`), puis
   le pousser. Le job `publish` vérifie que le tag égale `v` + `PLUGIN_VERSION`
   et échoue sinon.
3. Le workflow vérifie (`lint`, `test`), construit un paquet **non signé** par
   ABI dans une VM FreeBSD correspondante (job `build`, sans aucun secret),
   attend l'approbation manuelle de l'environnement `release`, signe (job
   `sign`), puis déploie sur Pages (job `publish`). Les pull requests ne font
   que `lint`, `test` et `build` : ni signature ni publication. Un
   `workflow_dispatch` lancé depuis `main` republie aussi.

Nom du paquet attendu : `os-appalias-<VERSION>.pkg`, où `<VERSION>` est
`PLUGIN_VERSION`, suivi de `_<PLUGIN_REVISION>` si cette variable est définie
dans le Makefile (par exemple `0.2_1`). Le build passe `PLUGIN_DEVEL=` (vide)
pour supprimer le suffixe `-devel` que `Mk/defaults.mk` ajoute par défaut ; un
autre nom de fichier fait échouer le build.

#### Changer les commits d'opnsense/plugins

Le build n'utilise pas la tête d'une branche mais un commit épinglé, un par
ABI, dans la matrice du job `build` de `.github/workflows/build.yml` (champ
`plugins_commit`) : branche `stable/26.1` pour FreeBSD 14.3, branche
`stable/26.7` pour FreeBSD 15.1. Pour les mettre à jour à la main :

```sh
git ls-remote https://github.com/opnsense/plugins refs/heads/stable/26.1 refs/heads/stable/26.7
```

Recopier chaque SHA de 40 caractères dans le champ `plugins_commit` de la
ligne correspondante, puis commiter via une pull request. Le script
`scripts/build-packages.sh` récupère exactement ce commit (`git init`,
`git fetch --depth 1 origin <sha>`, `checkout FETCH_HEAD`) et vérifie que
`git rev-parse HEAD` le confirme.

#### Rotation de clé

1. Générer la nouvelle paire **hors du dépôt**, par exemple dans
   `~/.appalias/` : `openssl genrsa -out ~/.appalias/appalias.key 4096`, puis
   `openssl rsa -in ~/.appalias/appalias.key -pubout -out keys/appalias.pub`
   (`*.key` et `*.pem` sont dans `.gitignore`, `keys/*.pub` reste suivi).
2. Remplacer le secret `PKG_SIGNING_KEY` de l'environnement `release`.
3. Calculer l'empreinte (`sha256sum keys/appalias.pub` sous Linux ou Git Bash,
   `sha256 -q keys/appalias.pub` sous FreeBSD), puis mettre à jour **à la fois**
   `EXPECTED_SHA256` dans `tools/add-repo.sh` et l'empreinte du présent README.
   Le job `publish` échoue si l'une des deux diffère de la clé.
4. Détruire la copie locale de la clé privée (ou l'archiver hors ligne).
5. Republier (nouveau tag ou `workflow_dispatch`). Chaque client relance la
   commande d'installation, qui re-télécharge le script à jour (avec la
   nouvelle `EXPECTED_SHA256`) et la nouvelle clé. Tant qu'il garde l'ancienne
   clé, `pkg update` échoue sur la signature.

#### Piège : toujours publier les deux ABI

Si le répertoire `${ABI}` du client manque sur Pages, `meta.conf` renvoie une
404 : `pkg update` échoue et la page Firmware aussi. Ne jamais publier une
seule ABI ni retirer l'une des deux (les jobs `sign` et `publish` échouent s'il
en manque une). Chaque paquet doit être construit sur la base FreeBSD de son
ABI : un paquet construit sur une base plus récente est refusé par une base
plus ancienne. Les répertoires `FreeBSD:14:amd64` et `FreeBSD:15:amd64`
contiennent des `:` et ne sont créés que par la CI : ne jamais les commiter.

#### Sécurité de la chaîne de publication

- **Build sans secret** : le job `build` (push, PR, tag) ne reçoit aucun secret
  et ne produit qu'un `.pkg` non signé.
- **Signature isolée** : le job `sign` ne tourne que sur un tag `v*` ou un
  `workflow_dispatch` depuis `main`, jamais sur une PR. Il exige l'approbation
  manuelle de l'environnement `release`, n'exécute que `scripts/sign-repo.sh`
  (pas de clone, pas de make), et `PKG_SIGNING_KEY` n'est passé qu'à l'étape
  de signature. La clé est écrite hors du workspace en 0600 puis supprimée.
- **Actions épinglées** : chaque `uses:` est épinglé par SHA de 40 caractères
  (version en commentaire) ; Dependabot (`.github/dependabot.yml`, hebdomadaire)
  propose les mises à jour. Le job `lint` lance zizmor sur les workflows et
  fait échouer la CI.
- **opnsense/plugins épinglé** par commit, une branche stable par ABI.
- **Vérification côté client** : `add-repo.sh` refuse toute clé dont
  l'empreinte diffère de `EXPECTED_SHA256`, et `pkg` vérifie ensuite la
  signature du catalogue avec cette clé.
- **Modèle de confiance, honnêtement** : la première installation fait
  confiance au script récupéré en HTTPS depuis GitHub Pages, donc à la
  sécurité du compte GitHub du propriétaire (confiance à la première
  utilisation, TOFU). Un compte compromis peut publier un script modifié, donc
  une autre empreinte ; seule une comparaison avec ce README, obtenu par un
  autre canal, protège contre cela. La signature protège les mises à jour
  suivantes, pas la première installation.

Réglages GitHub à activer :

- Protection de `main` : pull request obligatoire, pas de force-push.
- Ruleset de tags `v*` : création réservée au propriétaire, pas de
  suppression ni de mise à jour.
- Secret scanning et push protection.
- Dependabot alerts et security updates.
- *Actions > General > Workflow permissions* : « Read repository contents ».
- « Require approval for all outside collaborators » pour les PR de forks.
- Désactiver « Allow GitHub Actions to create and approve pull requests ».
- Activer « Private vulnerability reporting » (voir `SECURITY.md`).

### Par copie (test sur une box)

Copier le projet sur la box, puis en root :

```sh
sh tools/install-copy.sh -n   # voir ce qui sera fait
sh tools/install-copy.sh
```

Désinstallation : `sh tools/uninstall-copy.sh` (retire exactement les
fichiers copiés ; config.xml et `/var/db/appalias` sont conservés).

### En paquet

Placer `net/appalias` dans un clone du dépôt `opnsense/plugins`. Si le
projet a été créé ou copié depuis Windows, les bits exécutables sont perdus :
les rétablir **avant** de construire le paquet, sur la machine de build :

```sh
sh tools/fix-modes.sh -n   # liste des fichiers concernés
sh tools/fix-modes.sh      # chmod 0755 (script rc, scripts lancés par configd et rc)
```

La liste est dérivée des actions configd (`actions_appalias.conf`) et du
script rc (`src/etc/rc.d/appaliasd`, dont `appaliasd.py`). Puis :

```sh
cd net/appalias
make package
pkg add work/pkg/os-appalias-0.1.pkg
```

### Avant le premier commit

Git sous Windows n'enregistre pas les bits exécutables. Les fixer dans
l'index, une fois, pour les fichiers listés par `sh tools/fix-modes.sh -n` :

```sh
git update-index --chmod=+x     net/appalias/src/etc/rc.d/appaliasd     net/appalias/src/opnsense/scripts/OPNsense/AppAlias/appaliasd.py     net/appalias/src/opnsense/scripts/OPNsense/AppAlias/status.py     net/appalias/src/opnsense/scripts/OPNsense/AppAlias/export_config.php     net/appalias/src/opnsense/scripts/OPNsense/AppAlias/sync_aliases.php
```

Les `__pycache__/` et `*.pyc` sont ignorés (`.gitignore`).

## Configuration

1. *Services > AppAlias > Réglages* : indiquer le **résolveur amont** (celui
   que le pare-feu utilisait jusque-là), activer, enregistrer.
2. *Applications* : ajouter une application (nom, domaines, réseaux
   éventuels), puis **Appliquer**. L'alias `APP_<NOM>` apparaît dans
   *Pare-feu > Alias*, avec la description
   `[os-appalias] géré automatiquement - ne pas éditer`.
   Renommer une application, ou changer le préfixe, est **refusé** tant que
   l'alias actuel est encore utilisé (règle, NAT, groupe d'alias) : retirer
   d'abord ces références. Supprimer une application dont l'alias est encore
   utilisé ne supprime pas l'alias : il est seulement vidé.
3. Montage A : faire passer le résolveur local par le relais.
   - Unbound : *Services > Unbound DNS > Query Forwarding*, une entrée sans
     domaine vers `127.0.0.1`, port `53535`.
   - Dnsmasq : amont `127.0.0.1#53535`, sans les serveurs DNS système.
   - Ne jamais mettre le résolveur local comme amont d'AppAlias (boucle).

### Adresse d'écoute

Garder `127.0.0.1` (valeur par défaut) : seul le résolveur local doit
interroger le relais. Une écoute ouverte au LAN permettrait à n'importe quel
client d'envoyer des requêtes forgées pour remplir les tables jusqu'à
`max_entries_per_app`, ce qui bloquerait l'ajout des vraies IP de
l'application. Le joker « any » est refusé pour les adresses et réseaux.

### Erreurs de configuration

Si `config.json` est absent ou invalide (amont manquant, « any », port hors
plage…), le démon journalise l'erreur dans syslog et sort avec le code 78
(EX_CONFIG). `daemon(8)` le relance toutes les 10 s (`-R 10`) : corriger la
configuration puis cliquer sur « Appliquer » suffit. Au rechargement
(SIGHUP), une configuration invalide est refusée et l'ancienne reste active.

Après un échec de `pfctl` sur une table, le démon n'appelle plus `pfctl` pour
cette table pendant 30 s (un seul message par fenêtre) ; les réponses DNS
partent sans attendre.

### Exemple : une application par un VPN (PBR)

Dans *Pare-feu > Règles > LAN*, au-dessus de la règle d'autorisation par défaut :

| Action | Famille | Source  | Destination   | Passerelle    |
|--------|---------|---------|---------------|---------------|
| Pass   | IPv4    | LAN net | `APP_EXEMPLE` | passerelle VPN |
| Block  | IPv6    | LAN net | `APP_EXEMPLE` | -             |

La seconde règle évite la fuite par IPv6 hors du VPN.

### Exemple : bloquer une application

`Block` (ou `Reject`), IPv4+IPv6, source `LAN net`, destination `APP_EXEMPLE`.

Pour les réseaux manuels de démonstration, utiliser les plages de
documentation (`192.0.2.0/24`, `2001:db8::/32`).

## Limites

- **DoH / DoT** : un client qui résout lui-même en chiffré ne passe pas par
  le relais ; ses IP n'arrivent pas dans l'alias.
- **CDN partagés** : une IP peut servir beaucoup de sites ; bloquer ou router
  une application peut en toucher d'autres.
- **Cache des clients** : une IP déjà en cache sur un poste n'est vue qu'à la
  prochaine résolution. Vider le cache DNS du poste si besoin.
- Les alias imbriqués dans un *Network group* peuvent ne pas suivre les ajouts
  dynamiques : à vérifier avec `tools/phase0.sh` (étape 3).

## Outils et tests

- `tools/phase0.sh` : vérifications manuelles sur une box (survie d'un alias
  External au `filter reload`, latence de `pfctl -T add`, imbrication dans un
  groupe, transmission d'Unbound). Lecture seule ou réversible.
- Tests (stdlib, sous Windows ou Unix), depuis la racine du projet :

```sh
python -I -m unittest discover -s tests
```

## État : MVP (0.1)

Fait : relais DNS UDP/TCP, règles `domain:`/`full:`, suivi des CNAME, CIDR
manuels, exclusions, familles, plafond par application, état persistant,
interface (applications, réglages, aide), actions configd.

## Phases suivantes

- Phase 2 : sources ASN (champ `asns` déjà présent) et catalogue d'applications.
- Phase 3 : listes v2fly (`geosite`), règles `keyword:` et `regexp:`.

## Licence

BSD-2-Clause, voir [LICENSE](LICENSE).
