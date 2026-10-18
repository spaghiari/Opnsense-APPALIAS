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
python -I -B -m unittest discover -s tests   # ou python3 -B -m unittest discover -s tests
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

## Catalogue v2fly (instantané embarqué)

Le paquet embarque un instantané de
[v2fly/domain-list-community](https://github.com/v2fly/domain-list-community),
référence des releases et du mode hors ligne, sous
`net/appalias/src/opnsense/scripts/OPNsense/AppAlias/catalog/` :

- `v2fly.lst` : une section `[liste]` par liste, lignes triées, commentaires
  retirés, `include:` conservés ;
- `v2fly.manifest.json` : commit, date, sha256 de l'archive et du `.lst`,
  règles d'exclusion, compteurs (le plugin refuse un `.lst` dont le sha256
  diffère du manifeste) ;
- `LICENSE.v2fly` : notice de copyright et licence MIT de l'archive.

Listes exclues : `category-*`, `geolocation-*`, `tld-*`, `cn`, `private`, et
tout nom contenant `!` (ou hors `[a-z0-9-]`).

Instantané actuel :

- commit : `4b6e9f1e16c0905e8c1f1b22f7669ceb9507da8b` (2026-10-17)
- sha256 de l'archive `archive/<commit>.tar.gz` :
  `b609b818a195352b4d79eff4b8761b4ea1cc61079df7283e7a3e43cfcd618c35`
- `v2fly.lst` : 414439 octets, 1416 listes conservées, 129 exclues.

Le pare-feu peut aussi télécharger lui-même un catalogue plus récent
(`catalog_update.py`, activé par défaut, quotidien) : il est écrit sous
`/var/db/appalias/catalog/` (`source: "online"` dans le manifeste) et n'est
employé que s'il est intègre et plus récent que l'instantané embarqué. La
génération (`lib/v2fly_snapshot.py`) est la même que celle de l'outil
ci-dessous. L'instantané embarqué reste la référence des releases et du mode
hors ligne.

### Rafraîchir le catalogue

`tools/v2fly-snapshot.py` (stdlib, non livré dans le paquet ; la génération
vit dans `lib/v2fly_snapshot.py`, livrée et partagée avec le pare-feu) télécharge
l'archive du commit, vérifie son sha256, l'extrait avec
`tarfile` `filter="data"` (seuls `data/*` et `LICENSE`, fichiers réguliers,
tailles plafonnées) et écrit les trois fichiers. L'archive est une donnée non
fiable : la placer dans un dossier vide, lancer Python avec `-I`, ne rien
exécuter qui en vienne.

1. Choisir le commit (40 caractères) :
   `git ls-remote https://github.com/v2fly/domain-list-community refs/heads/master`.
2. Premier téléchargement de ce commit (empreinte calculée et affichée) :

```sh
mkdir /tmp/v2fly-new   # dossier vide, hors du dépôt
python3 -I tools/v2fly-snapshot.py --commit <sha1> --fetch --tofu \
    --archive /tmp/v2fly-new/v2fly.tar.gz \
    --out net/appalias/src/opnsense/scripts/OPNsense/AppAlias/catalog
```

3. Recopier ici le commit, la date et l'empreinte affichée (`archive_sha256`),
   puis vérifier l'empreinte une seconde fois depuis l'archive locale :

```sh
python3 -I tools/v2fly-snapshot.py --commit <sha1> --sha256 <archive_sha256> \
    --archive /tmp/v2fly-new/v2fly.tar.gz \
    --out net/appalias/src/opnsense/scripts/OPNsense/AppAlias/catalog
```

4. Contrôler la taille signalée (plafond ~1,5 Mo, le script échoue au-delà
   sauf `--max-bytes`), relire le diff de `v2fly.lst` (listes ajoutées ou
   retirées), lancer les tests, puis commiter via une PR. Après la mise à
   jour du plugin, les applications qui suivent le catalogue reçoivent les
   nouveautés (`catalog_follow.php run` lancé par `+POST_INSTALL`) ; les
   autres affichent leurs « catalog updates » à valider dans la modale de
   diff.

## Tests manuels côté PHP

`php` n'est pas exécuté par la CI : à vérifier sur une box de test après
installation (`tools/install-copy.sh`) :

1. Config 0.2 existante : la page *Services > AppAlias* se charge, la
   migration du modèle (0.1.0 -> 0.2.0) passe sans erreur, l'enregistrement
   des réglages ne produit aucune erreur de validation.
2. Application avec `domain:cloudfront.net`, `net` ou `*.amazonaws.com` :
   refusée (message « use full: ») ; `domain:d123.cloudfront.net` et
   `full:cloudfront.net` acceptés. Idem avec un nom ajouté dans *Extra
   shared platforms*.
3. Onglet *Catalog* : recherche `netflix` puis `nflxvideo.net` (même liste),
   recherche avec espace ou `../` refusée.
4. Aperçu `google` : règles groupées par type, `@ads` décochées, CDN partagés
   décochés avec l'icône, `keyword:`/`regexp:` grisées, compteur et
   avertissement au-delà de 300 règles, import bloqué au-delà de 1000.
5. Import « nouvelle application » (nom proposé `NETFLIX`) puis « fusion »
   dans une application existante : `domains`, `catalog_refs`,
   `catalog_rules`, `catalog_revision` et `origin=v2fly` enregistrés ; rien
   n'est appliqué avant « Apply ».
6. `POST /api/appalias/catalog/import` avec une règle absente de la liste :
   refus côté serveur.
7. Retirer une règle de `catalog_rules` à la main (ou importer avec des
   règles décochées) : la grille affiche « N catalog updates », la modale de
   diff ajoute ou retire les règles cochées.
8. `configctl appalias export` : `config.json` contient `catalog_refs` et
   `shared_platforms_extra` ; le relais journalise une seule ligne pour les
   règles refusées et, en debug, une seule ligne pour les règles de
   découverte.
9. Journal dédié : rien à déclencher à la main. plugins.mk ajoute à
   +POST_INSTALL un `configctl template reload` par dossier de template (dont
   `OPNsense/Syslog`), et `rc.configure_plugins POST_INSTALL` appelle
   `system_syslog_start(true)`. Vérifier seulement, après installation ou mise
   à jour, que `/var/log/appalias/` reçoit les messages du démon.
10. Branchement d'Unbound (opt-in) : cocher l'option, DNS over TLS « . »
    actif, puis Apply. Vérifier :
    - `/usr/local/etc/unbound.opnsense.d/appalias.conf` et sa copie
      `/var/unbound/etc/appalias.conf` (zones avec `forward-first: yes`) ;
    - Unbound redémarré une fois (journal), puis plus du tout sur un second
      Apply sans changement de domaines ;
    - `unbound-control -c /var/unbound/unbound.conf list_forwards` montre les
      zones vers `127.0.0.1@53535` ; résoudre un domaine d'application
      remplit l'alias ; relais arrêté, le domaine se résout encore (sans
      remplir l'alias) ;
    - ajouter un Query Forwarding pour un de ces domaines : au démarrage
      d'Unbound (événement `unbound_start`), le fichier est régénéré sans ce
      domaine ; la raison s'affiche dans *Settings* après Apply ;
    - amont du relais réglé sur une IP d'interface ou une VIP, port 53 :
      aucune zone, erreur « loop » affichée ;
    - décocher l'option puis Apply : fichiers supprimés, Unbound redémarré ;
    - bouton *Restart Unbound* : confirmation demandée, résultat affiché ;
    - désinstallation : `+PRE_DEINSTALL.post` supprime les fichiers ;
    - avec un transfert « . » actif (DoT ou Forward), l'encadré affiche
      l'avertissement de repli : relais arrêté, ces domaines passent en
      récursion directe (port 53 en clair, hors DNS over TLS), SERVFAIL si le
      port 53 sortant est bloqué ;
    - redémarrage en échec (Unbound arrêté à la main par exemple) : le
      prochain Apply relance le redémarrage même sans changement
      (`restart_pending` dans `/var/db/appalias/unbound.json`).

    Limite : le bouton Restart de *Services > Unbound DNS* (et le widget des
    services) n'appelle pas `plugins_configure` ; Unbound recharge alors le
    fichier tel qu'écrit au dernier Apply d'AppAlias (ou au dernier événement
    `dns` / `unbound_start`).
11. DNS over TLS vers l'amont (0.4). Config 0.3 existante : la page se
    charge, le modèle passe en 0.3.0, *Upstream protocol* vaut « Plain DNS »
    et rien ne change. Puis régler `9.9.9.9`, protocole *DNS over TLS*,
    port `853`, nom `dns.quad9.net`, secours `149.112.112.112#dns.quad9.net`,
    puis *Save and apply*. Vérifier :
    - validation : *DNS over TLS* sans nom TLS refusé ; secours
      `149.112.112.112` (sans port), `[2620:fe::fe]`, `[2620:fe::fe]:853`,
      `192.0.2.1#dns.quad9.net` acceptés ; `192.0.2.1:`, `host.example`,
      `192.0.2.1#bad name` refusés ;
    - `config.json` contient `upstream_protocol`, `upstream_tls_name` et
      `unbound.forwarding` ; l'encadré de *Settings* affiche « Upstream: DNS
      over TLS (dns.quad9.net) », et l'avertissement du port 53 si le port est
      resté à 53 ;
    - `tcpdump -ni <wan> host 9.9.9.9 and port 53` ne montre rien pendant
      qu'un domaine d'application est résolu ; `port 853` montre une connexion
      réutilisée (pas une poignée de main par requête) ;
    - `drill @127.0.0.1 -p 53535 <domaine>` répond, l'alias se remplit, le
      journal montre `APP_X : ajout de N IP (amont .. ms, pf .. ms)` ;
    - nom TLS faux (`dns.example.invalid`) : SERVFAIL, un avertissement
      « certificat refusé » par minute au plus, jamais de requête en clair sur
      le port 53 (tcpdump) ; idem amont injoignable : bascule sur le secours ;
    - grosse réponse en UDP (`drill @127.0.0.1 -p 53535 -b 512 TXT <nom>`) :
      réponse tronquée (TC), puis `drill -t` en TCP : réponse complète ;
    - `configctl appalias status` : clé `latency` (par amont et `pf` :
      `count`, `avg_ms`, `p95_ms`), affichée dans *Settings* ;
    - *Use System Nameservers* coché dans Unbound : avertissement de transfert
      « . » affiché dans *Settings* après Apply ;
    - catalogue : règles refusées d'un diff affichées en orange
      (`alert-warning`).
11. Version 0.5 (modèle 0.4.0) :
    - badge d'état en tête de page : *Running* (PID, version), *Stopped*,
      *Config error* (config.json invalide) ; après installation de fichiers
      plus récents sans redémarrage, avertissement « restart needed » ;
    - note « forwarded domains bypass DNS over TLS » en bleu (info) relais
      démarré, en rouge relais arrêté ;
    - découverte : carte d'activation tant que *Discovery* est décoché ;
      cochée puis Apply, `discovery.json` apparaît sous `/var/db/appalias/`
      après *Refresh* ; *Learn 2 min* (compte à rebours, noms « new ») ;
      *Accept* enregistre `full:`/`domain:` dans l'application (refus de
      `domain:` sur une plateforme partagée), *Ignore* / *Stop ignoring*
      modifient `general.discovery_ignored` ; *Erase discovery data* et
      décocher *Discovery* puis Apply suppriment le fichier ; pastille
      « N proposals » dans la grille ;
    - `configctl appalias discovery_ctl 'learn:700'` et `'dump;id'` refusés ;
    - catalogue en ligne : `crontab -l` contient la tâche
      `configctl -d appalias catalog_update` (minute et heure dérivées du nom
      d'hôte) tant que l'option est cochée en *daily*, plus rien une fois
      décochée puis Apply ; *Update catalog now* affiche le nombre de listes
      et l'écart ; `ps` pendant l'extraction montre le sous-processus sous
      `nobody` ; *Revert to embedded* revient à « Embedded snapshot » ;
    - après `pkg upgrade`, `+POST_INSTALL` rend la main tout de suite (mise à
      jour du catalogue en tâche de fond, sous `daemon -f`).
12. Version 0.6 (modèle 0.5.0) :
    - migration : config 0.5 avec une application importée (`catalog_refs`
      non vide) et une application manuelle. Après installation, la page se
      charge ; l'application importée a `catalog_follow=1` et
      `catalog_declined` = règles de ses listes absentes de `domains`
      (règles décochées à l'import) ; l'application manuelle a
      `catalog_follow=0`. Catalogue illisible pendant la migration :
      `catalog_follow=0` et un avertissement dans le journal ;
    - boîte de dialogue : la case *Follow catalog automatically* n'apparaît
      que pour une application avec des listes du catalogue ; pastille
      `auto` dans la grille, plus de lien « N catalog updates » pour elle ;
      case décochée puis Save : le lien revient ;
    - import d'une liste avec des règles décochées : `catalog_follow=1`,
      `catalog_declined` contient les règles décochées ;
    - suivi : retirer une règle de `catalog_rules` à la main dans
      config.xml (simule une nouveauté du catalogue), puis
      `configctl appalias catalog_follow` : JSON `result: updated`, la règle
      revient dans `domains`, `/var/db/appalias/catalog_follow.json` a une
      entrée, le journal `appalias` une ligne info, l'alias est synchronisé
      et le relais rechargé (`onereload`), Unbound redémarré seulement si les
      zones changent. Les règles ajoutées à la main restent ; une règle
      décochée à l'import (dans `catalog_declined`), une règle `full:` sur une
      plateforme partagée, une `keyword:` ou une règle ignorée par la
      découverte ne sont pas ajoutées. Second appel : `result: unchanged` ;
    - retrait : ajouter `domain:obsolete.example` à `domains` et à
      `catalog_rules`, puis `configctl appalias catalog_follow` : retirée ;
      ajoutée seulement à `domains` (règle manuelle) : gardée ;
    - plafond : application à 1000 règles et une nouveauté : rien n'est
      appliqué, avertissement « plafond » dans le journal, `errors: 1` ;
    - liste disparue du catalogue (`catalog_refs` inconnu) : l'application
      n'est pas modifiée, erreur journalisée, les autres le sont ;
    - *Update catalog now* : le message indique le nombre d'applications
      mises à jour ; la section *Automatic updates* liste l'entrée (date,
      application, +N/-M, détail dépliable) ;
    - *Undo* : confirmation, règles ajoutées retirées et ajoutées à
      `catalog_declined`, règles retirées remises dans `domains` seulement
      (règles manuelles), application appliquée, entrée inverse « undo » au
      journal, entrée d'origine marquée « undone ». Nouveau
      `configctl appalias catalog_follow` : rien ne revient. Application
      supprimée : bouton grisé, et
      `configctl appalias catalog_follow_undo <id>` répond « was deleted » ;
      identifiant `x;id` refusé ;
    - journal borné : au-delà de 200 entrées, les plus anciennes sortent ;
    - découverte : une proposition dont la chaîne CNAME passe par un nom de
      la même application est dans la section repliée « Already covered »,
      sans *Accept* ni *Ignore*, et n'est comptée ni dans « N proposals » ni
      dans la pastille de la grille ; *Follow CNAMEs* décoché : elle redevient
      une proposition ordinaire ;
    - mise en page : onglets *Discovery* et *Catalog* à 1280 px puis sous
      768 px (lignes de proposition empilées, galerie sur une colonne).

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
  configuration puis cliquer sur « Apply ». Au rechargement (SIGHUP), une
  configuration invalide est refusée et l'ancienne reste active.
