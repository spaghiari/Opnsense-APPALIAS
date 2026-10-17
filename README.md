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
