# os-appalias

Plugin OPNsense : **un alias pare-feu par application**, rempli tout seul.

Un relais DNS local (`appaliasd`) voit passer les réponses DNS. Quand un nom
correspond à une application, il ajoute ses adresses à l'alias pf
`APP_<NOM>`, avant de rendre la réponse au client. Le plugin ne crée aucune
règle : vous employez les alias dans les vôtres (blocage, routage par
passerelle, etc.).

## Installation

En root sur le pare-feu :

```sh
fetch -o /tmp/add-repo.sh https://spaghiari.github.io/Opnsense-APPALIAS/add-repo.sh && sh /tmp/add-repo.sh
```

Puis, dans *System > Firmware > Plugins*, « Check for updates » et installer
`os-appalias`.

Le script affiche l'empreinte SHA256 de la clé publique et refuse la clé si
elle diffère de celle-ci :

325cde334cd45a513bbf8598d51627c991b78bc20379d5648fc4a499ac40a4b3

Vérifiez vous-même la clé sur le pare-feu avec
`sha256 -q /usr/local/etc/pkg/keys/appalias.pub` : le résultat doit être
identique à l'empreinte ci-dessus. Un script ou un compte GitHub compromis
afficherait une autre empreinte, alors que celle publiée ici peut être
vérifiée par un autre canal.

Pour retirer le dépôt (clé et configuration) :

```sh
fetch -o /tmp/add-repo.sh https://spaghiari.github.io/Opnsense-APPALIAS/add-repo.sh && sh /tmp/add-repo.sh -u
```

Installation par copie, publication et maintenance : voir
[docs/maintenance.md](docs/maintenance.md).

## Configuration

1. *Services > AppAlias > Settings* : indiquer le résolveur DNS amont (celui
   que le pare-feu utilisait jusque-là), activer, enregistrer. Pour chiffrer
   aussi les requêtes du relais, voir [DNS over TLS](#dns-over-tls-vers-lamont).
2. **Unbound, mode automatique (opt-in)** : cocher *Forward application
   domains from Unbound* dans *Settings*. **Apply** écrit alors une zone de
   transfert par domaine des applications activées
   (`/usr/local/etc/unbound.opnsense.d/appalias.conf`, vers
   `127.0.0.1@53535`, avec `forward-first: yes`) et redémarre Unbound
   (`configctl unbound restart`) seulement si la liste des domaines a changé.
   Seuls ces domaines passent par le relais : les autres requêtes gardent leur
   chemin habituel (DNS over TLS, Query Forwarding).
   - Les noms déjà présents dans Unbound (Query Forwarding, DNS over TLS,
     overrides de domaine, actifs ou non), leurs sous-domaines et les noms
     locaux (domaine du système, host overrides, `lan`, `home.arpa`…) sont
     écartés, car Unbound ignore une zone en double (la première chargée
     gagne) ; un sous-domaine d'un domaine déjà transféré est fusionné. La
     liste des noms écartés et leur raison s'affichent dans *Settings*.
   - Une règle `full:x` transfère toute la zone `x`, sous-domaines compris
     (sur-ensemble sans effet sur les alias, qui suivent les règles) ; le
     garde-fou des plateformes partagées s'applique donc aussi aux `full:`.
     `keyword:` et `regexp:` ne sont jamais transférés.
   - `forward-first` : si le relais est arrêté ou planté, Unbound se rabat
     pour ces domaines sur une **récursion directe** vers les serveurs faisant
     autorité (DNS en clair sur le port 53), qui **contourne DNS over TLS et
     le Query Forwarding « . »**. Si le port 53 sortant est bloqué, ces
     domaines répondent SERVFAIL tant que le relais est arrêté. Pendant la
     panne, leurs adresses n'entrent pas dans les alias. *Settings* affiche un
     avertissement quand un transfert « . » (DoT ou Forward) est actif.
   - Après l'ajout d'un Query Forwarding pour un domaine aussi utilisé par
     AppAlias, cliquer sur **Apply** dans AppAlias. Le fichier est aussi
     régénéré, sans redémarrage, à chaque démarrage d'Unbound.
   - Les pages Unbound affichent alors une bannière « manual overwrites » :
     elle est attendue (fichier hors du core).
   - L'amont du relais doit être un résolveur réel : jamais une adresse du
     pare-feu (interfaces, VIP, bouclage) sur le port 53 ou celui d'Unbound
     (boucle ; le plugin ne transfère alors aucune zone et l'indique).
   - *Settings* propose aussi un redémarrage manuel d'Unbound, après
     confirmation.

   **Mode manuel (par défaut)** : option décochée, dans *Services >
   Unbound DNS > Query Forwarding* ajouter, pour chaque domaine, un transfert
   vers `127.0.0.1`, port `53535`. Une entrée sans domaine (tous domaines)
   est supplantée par un transfert DNS over TLS « . ». Avec Dnsmasq : serveur
   amont `127.0.0.1#53535`.
3. *Services > AppAlias > Applications* : ajouter une application (nom,
   domaines, réseaux CIDR éventuels), puis **Apply**. L'alias `APP_<NOM>`
   apparaît dans *Pare-feu > Alias*.

L'interface du plugin est en anglais (textes source OPNsense) ; les
traductions viennent du projet de traduction d'OPNsense.

## DNS over TLS vers l'amont

Par défaut, le relais interroge son amont en DNS clair. Si Unbound fait déjà
du DNS over TLS pour « . », les domaines des applications, qui passent par le
relais, partent eux en clair : réglez le relais en DNS over TLS (RFC 7858)
pour tout chiffrer. Dans *Settings*, recommandé avec Quad9 :

| Réglage            | Valeur                          |
|--------------------|---------------------------------|
| Upstream resolver  | `9.9.9.9`                       |
| Upstream protocol  | DNS over TLS                    |
| Upstream port      | `853`                           |
| Upstream TLS name  | `dns.quad9.net`                 |
| Fallback upstreams | `149.112.112.112#dns.quad9.net` |

- Le port n'est pas modifié automatiquement : passez-le de 53 à 853.
  *Settings* le signale s'il est resté à 53.
- Le certificat de l'amont est toujours vérifié (magasin de CA du système,
  `/usr/local/etc/ssl/cert.pem`, le même que celui d'Unbound), ainsi que son
  nom (*Upstream TLS name*, aussi envoyé en SNI). TLS 1.2 au minimum.
- **Jamais de repli en clair** : en cas d'échec (certificat, nom, délai), le
  relais essaie l'amont de secours suivant, puis répond SERVFAIL. Chaque
  échec est journalisé en avertissement, une fois par minute au plus pour un
  même amont et une même raison.
- Amonts de secours : `ip`, `ip:port`, `[ipv6]` ou `[ipv6]:port`, suivis
  éventuellement de `#nom-tls` (syntaxe d'Unbound). Port par défaut : 53 en
  DNS clair, 853 en DNS over TLS ; nom par défaut : celui de l'amont
  principal.
- Les connexions TLS sont persistantes (8 au plus par amont, une requête à
  la fois par connexion, fermées après 30 s d'inactivité) : pas de poignée de
  main par requête.
- Une réponse plus grande que ce qu'accepte un client UDP (taille EDNS, 512
  octets sans EDNS) lui est rendue tronquée : il la redemande en TCP. Les
  adresses sont ajoutées à l'alias dès la première réponse.
- *Settings* affiche « Upstream: DNS over TLS (dns.quad9.net) » ou
  « Upstream: plain DNS ».

## Catalogue

L'onglet *Catalog* cherche dans un instantané embarqué de
[v2fly/domain-list-community](https://github.com/v2fly/domain-list-community)
(commit épinglé, empreinte vérifiée ; aucun téléchargement sur le pare-feu),
par nom de liste (`netflix`) ou par domaine (`nflxvideo.net`). L'aperçu
regroupe les règles par type et laisse choisir celles à importer, dans une
nouvelle application ou par fusion dans une application existante :

- les règles marquées `@ads` et celles qui touchent une plateforme partagée
  (CDN, cloud) sont décochées par défaut ;
- une règle `domain:` sur une plateforme partagée entière, sur l'un de ses
  parents ou sur un domaine de premier niveau seul est refusée (utiliser
  `full:` ou un sous-domaine dédié) ; la liste se complète dans les réglages
  (*Extra shared platforms*) ;
- l'import enregistre l'application, sans rien appliquer : il faut toujours
  cliquer sur **Apply**.

Après une mise à jour du plugin (nouvel instantané), la grille indique pour
chaque application importée le nombre de « catalog updates » : règles
ajoutées ou retirées par le catalogue, à cocher dans une modale avant
enregistrement. Les règles ajoutées à la main sont conservées.

**Règles de découverte** : `keyword:` et `regexp:` sont conservées dans la
configuration mais jamais appliquées par le relais (une seule ligne de
journal, en debug, indique leur nombre).

## Exemple

Dans *Pare-feu > Règles > LAN*, au-dessus de la règle d'autorisation par
défaut, pour faire passer une application par un VPN :

| Action | Famille | Source  | Destination | Passerelle     |
|--------|---------|---------|-------------|----------------|
| Pass   | IPv4    | LAN net | `APP_X`     | passerelle VPN |
| Reject | IPv6    | LAN net | `APP_X`     | -              |

La règle IPv6 évite la fuite hors du VPN. Elle doit être en **Reject** et non
en Block : un paquet bloqué est ignoré sans réponse, et le navigateur attend
un délai d'expiration avant de basculer en IPv4 ; avec Reject, le refus est
immédiat et la bascule aussi.

## Performances

- Le relais journalise, pour chaque réponse qui ajoute des adresses, le
  temps de l'amont et celui de pf (`APP_X : ajout de N IP (amont 28 ms, pf
  12 ms)`). *Settings* affiche la moyenne et le p95 des 256 dernières
  mesures, par amont et pour pf.
- Activer **Prefetch Support** dans *Services > Unbound DNS > Advanced* :
  Unbound rafraîchit les entrées populaires avant leur expiration, la
  réponse passe par le relais et l'alias reste à jour sans faire attendre le
  client.
- Ne **pas** activer **Serve Expired** : Unbound pourrait rendre une adresse
  expirée, déjà sortie de l'alias, sans passer par le relais ; le trafic vers
  cette adresse n'est alors plus couvert par vos règles.

## Limites

- **DoH / DoT des clients** : un client qui résout lui-même en chiffré ne
  passe pas par le relais (le DNS over TLS du relais vers son amont n'y
  change rien).
- **CDN partagés** : une IP peut servir beaucoup de sites ; bloquer ou router
  une application peut en toucher d'autres.
- **Cache des clients** : une IP déjà en cache sur un poste n'est vue qu'à la
  prochaine résolution.
- **Écoute** : laisser le relais sur `127.0.0.1` (défaut). Ouvert au LAN, un
  client pourrait remplir les alias jusqu'au plafond par application.
- **État actuel (0.4.2)** : catalogue v2fly et DNS over TLS vers l'amont
  disponibles (port 853 et nom TLS préremplis, 53 refusé en DNS over TLS) ; les sources ASN ne sont pas encore utilisées et les règles `keyword:` / `regexp:` restent des
  règles de découverte.

## Contribuer, sécurité, licence

- Contribuer : [CONTRIBUTING.md](CONTRIBUTING.md)
- Signaler une vulnérabilité : [SECURITY.md](SECURITY.md)
- Licence : BSD-2-Clause, voir [LICENSE](LICENSE)

Le catalogue embarqué provient de
[v2fly/domain-list-community](https://github.com/v2fly/domain-list-community),
Copyright (c) 2018-2019 V2Ray, distribué sous licence MIT. La notice de
copyright et le texte de la licence MIT sont joints à la distribution :
`net/appalias/src/opnsense/scripts/OPNsense/AppAlias/catalog/LICENSE.v2fly`
(installé sous `/usr/local/opnsense/scripts/OPNsense/AppAlias/catalog/`).
