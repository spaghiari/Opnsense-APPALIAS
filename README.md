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

4. **Résolution proactive** (activée par défaut, y compris après une mise à
   jour vers 0.7) : en complément du relais, le démon résout lui-même, à
   intervalle régulier, les noms des applications activées, pour que leurs
   alias soient déjà remplis avant que le premier client ne les demande.
   - Elle sonde les règles `full:x` (le nom `x`) et `domain:x` (`x` et
     `www.x`, selon *Proactive prefixes*), en A et en AAAA selon les
     familles de l'application. Les réponses passent par le même chemin
     que celles du relais (exclusions, plafonds, expiration des entrées).
   - Réglages (*Settings*) : *Proactive resolution* (activation générale),
     intervalle minimal (300 s par défaut) et maximal (3600 s), préfixes
     (`www` par défaut, 8 au plus). L'intervalle maximal reste sous
     `ttl_min + ttl_margin`, sinon les entrées expireraient entre deux
     passes (valeur ramenée ou fonction désactivée, avec un
     avertissement). Chaque nom est ensuite replanifié selon le TTL de la
     réponse, borné par ces intervalles, avec un léger décalage aléatoire ;
     les échecs et les réponses vides reculent progressivement.
   - Par application : case *Proactive resolution* (cochée par défaut,
     pastille dans la grille). Une application désactivée n'est pas sondée.
   - Charge DNS sortante : environ N noms × 2 types (A et AAAA) par
     intervalle, N étant le nombre de noms sondés (256 par application et
     5000 au total au plus), étalés dans le temps, 2 sondes simultanées au
     plus.
   - Compteurs dans `metrics.json` : envoyées, réussies, vides, échouées,
     adresses ajoutées.

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
(commit épinglé, empreinte vérifiée), rafraîchi depuis GitHub (vérification
horaire, voir *Mise à jour du catalogue*), par nom de liste (`netflix`) ou
par domaine (`nflxvideo.net`). L'ajout est un seul geste : choisir
l'application, saisir son nom et cliquer **Add**, sans case à cocher. Le
serveur calcule la sélection par défaut :

- les règles marquées `@ads` et celles qui touchent une plateforme partagée
  (CDN, cloud) en sont exclues ;
- une règle `domain:` sur un domaine parent entier d'une plateforme partagée
  (`googleapis.com`…) ou sur un domaine de premier niveau seul est refusée
  (utiliser `full:` ou un sous-domaine dédié) ; les sous-domaines dédiés
  d'une plateforme (`youtubei.googleapis.com`, `yt3.googleusercontent.com`)
  sont en revanche inclus. Les espaces multi-locataires (`s3.amazonaws.com`,
  `blob.core.windows.net`, `storage.googleapis.com`, `github.io`,
  `workers.dev`) ne peuvent pas être pris en bloc ; la liste se complète dans
  les réglages (*Extra shared platforms*) ;
- l'application créée suit le catalogue (voir ci-dessous) et la
  configuration est appliquée automatiquement après l'ajout, sans clic sur
  **Apply**.

### Suivi automatique du catalogue

Une application créée ou fusionnée depuis le catalogue **suit le catalogue**
(case *Follow catalog automatically* de l'application, pastille `auto` dans la
grille). Après chaque mise à jour réussie du catalogue (téléchargement
horaire, **Update catalog now**, mise à jour du plugin avec un instantané
embarqué plus récent) :

- les nouvelles règles de ses listes sont ajoutées, les règles retirées du
  catalogue sont retirées, puis tout est appliqué sans clic (export complet,
  zones d'Unbound, rechargement du relais, sans recharger le filtre
  pare-feu), en passant par le modèle et ses validations. Si le plugin est
  désactivé, rien n'est appliqué : la configuration est enregistrée et
  reste annulable ;
- les règles ajoutées à la main ne sont jamais touchées ; les règles
  décochées à l'import, les noms de plateformes partagées, les règles
  `keyword:` / `regexp:`, les règles ignorées par la découverte et les règles
  `@ads` exclues par la référence (`netflix@-ads`) ne sont jamais ajoutées ;
- au-delà de 1000 règles pour une application, rien n'est appliqué pour elle,
  l'événement est journalisé et la grille affiche l'avertissement
  « Catalog update paused » ; une application en erreur n'est pas
  enregistrée, les autres le sont ;
- chaque changement est noté dans `/var/db/appalias/catalog_follow.json`
  (200 derniers) et dans le journal système (niveau info). La section
  *Automatic updates* de l'onglet *Catalog* liste les 20 derniers, avec le
  détail des règles et un bouton **Undo** : les règles ajoutées sont retirées
  (et ne reviendront pas), les règles retirées reviennent comme règles
  ajoutées à la main, puis c'est appliqué.

Une application qui ne suit pas le catalogue (case décochée, ou application
créée à la main) garde le fonctionnement manuel : la grille indique le nombre
de « catalog updates », à cocher dans une modale avant enregistrement, puis
**Apply**.

À la mise à jour vers 0.6, les applications existantes qui ont des listes du
catalogue suivent le catalogue ; les règles de leurs listes qui n'avaient pas
été importées jusque-là restent écartées.

**Règles de découverte** : `keyword:` et `regexp:` sont conservées dans la
configuration mais jamais appliquées par le relais (une seule ligne de
journal, en debug, indique leur nombre). Elles alimentent le score de
l'onglet *Discovery*.

### Mise à jour du catalogue

Par défaut, le pare-feu vérifie GitHub **toutes les heures** (à une minute
tirée du nom d'hôte) et juste après l'installation ou la mise à jour du
plugin. Chaque vérification coûte un appel API ; l'archive de
v2fly/domain-list-community n'est téléchargée que si le dernier commit a
changé. La fréquence se règle dans *Settings* : `hourly` (défaut), `daily`
ou `manual`. À la mise à jour, les installations en `daily` passent à
`hourly`. Le bouton **Update catalog now** de l'onglet *Catalog* vérifie et
télécharge tout de suite ; **Revert to embedded** revient à l'instantané du
paquet. L'encadré de l'onglet indique la provenance (« Embedded snapshot »
ou « Downloaded », date et commit).

- **Ce qui s'applique tout seul** : seulement les applications qui suivent le
  catalogue (voir *Suivi automatique du catalogue*). L'application se fait en
  export complet, **sans recharger le filtre pare-feu**, et **pas du tout
  quand le plugin est désactivé** : la configuration est alors seulement
  enregistrée (annulable). Pour les autres applications, les
  nouvelles règles apparaissent dans les « catalog updates », à cocher puis
  **Apply**.
- **Sécurité** : HTTPS seulement, vers `api.github.com` et
  `codeload.github.com` (aucune autre destination, aucune redirection vers un
  autre hôte), certificat vérifié, archive plafonnée à 20 Mo. L'extraction et
  la génération (le même code que l'outil des releases) tournent sous
  l'utilisateur `nobody`, dans un dossier temporaire privé. Le catalogue
  n'est remplacé qu'après contrôle (au moins 500 listes, listes populaires
  présentes, aucune liste cassée) ; sinon le précédent est gardé et l'erreur
  journalisée. Le catalogue téléchargé n'est employé que s'il est intègre et
  plus récent que l'instantané embarqué.
- **Vie privée** : GitHub voit l'adresse IP du pare-feu à chaque
  vérification (une fois par heure par défaut).
- **Hors ligne** : décocher *Download catalog updates* (onglet *Settings*)
  revient au mode embarqué, sans aucun accès réseau : le catalogue ne change
  alors qu'avec les mises à jour du plugin.

## Découverte

L'onglet *Discovery* (désactivé par défaut, *Settings > Discovery*) propose
des règles pour vos applications à partir des noms DNS vus par le relais :
même cible CNAME qu'un nom couvert, chaîne CNAME qui passe par un nom couvert,
nom présent dans une liste v2fly importée, noms résolus ensemble
(co-occurrence), même site, règles `keyword:` / `regexp:`. Chaque proposition
affiche un score, ses raisons et une règle (`full:` par défaut, `domain:` pour
un groupe de noms du même site, jamais sur une plateforme partagée). **Accept**
enregistre la règle dans l'application ; rien ne change avant **Apply**. Le
mode *Learn 2 min* met en avant les noms vus pendant que vous utilisez une
application.

Les adresses des noms déjà couverts, y compris celles des noms de leurs
chaînes CNAME, sont ajoutées automatiquement par le relais : la découverte ne
sert qu'à trouver des noms manquants. Une proposition dont la chaîne CNAME
passe par un nom couvert par la même application est donc classée
« already covered » : elle apparaît dans une section repliée, sans **Accept**
ni **Ignore**, et n'est comptée ni dans les propositions ni dans la pastille
de la grille (avec *Follow CNAME*, réglage par défaut).

Ce que voit le relais : avec le transfert automatique d'Unbound, seulement
les domaines des applications et leurs chaînes CNAME (sous-domaines,
cibles CDN). La découverte complète donc surtout des applications
existantes ; elle ne voit d'autres domaines que si tout le DNS passe par le
relais.

### Vie privée

- **Noms seulement** : le relais ne voit que le résolveur local, jamais le
  client qui a posé la question ; seuls des noms DNS, des compteurs et des
  horodatages sont gardés (`/var/db/appalias/discovery.json`, 0640).
- **Rétention** : 7 jours par défaut (1 à 90), plafond de noms (5000 par
  défaut) ; **Erase discovery data** efface tout.
- **Désactivée par défaut** ; la désactiver efface les données.
- Les règles ignorées sont enregistrées dans `config.xml`, donc dans ses
  sauvegardes.
- Coût sur le DNS : l'observation d'une réponse est un simple ajout dans une
  file bornée, sans écriture disque ; le calcul se fait à part, toutes les
  2 secondes.

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
- La résolution proactive ajoute du trafic DNS sortant (environ N noms × 2
  types par intervalle, voir *Configuration*) ; ses compteurs (envoyées,
  réussies, vides, échouées, adresses ajoutées) sont dans `metrics.json`.
  Pour la réduire : allonger l'intervalle minimal, retirer des préfixes ou
  décocher la case des applications qui n'en ont pas besoin.
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
- **Résolution proactive** : elle ne couvre ni `regexp:` ni `keyword:`, ni les
  noms dynamiques (par exemple `rN---sn-xxx.googlevideo.com`), qui restent
  couverts par le relais, ni les plateformes partagées (jamais sondées). Les
  IP de CDN dépendent de l'emplacement du pare-feu : la sonde peut obtenir
  des adresses différentes de celles que vos clients reçoivent (biais
  géographique). Elle ne remplace pas le relais, qui reste la source
  principale pour les adresses réellement servies aux clients. Vider les
  caches (Unbound ou clients) ne change rien à son fonctionnement : le démon
  interroge directement l'amont.
- **État actuel (0.7)** : catalogue v2fly (vérifié chaque heure, option)
  suivi automatiquement par les applications importées, résolution
  proactive des noms `domain:` / `full:`, DNS over TLS vers
  l'amont, découverte (option), état du relais avec sa version en tête de
  page ; les sources ASN ne sont pas encore utilisées et les règles
  `keyword:` / `regexp:` restent des règles de découverte.

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
