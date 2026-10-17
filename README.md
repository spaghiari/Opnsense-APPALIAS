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

1. *Services > AppAlias > Réglages* : indiquer le résolveur DNS amont (celui
   que le pare-feu utilisait jusque-là), activer, enregistrer.
2. *Services > Unbound DNS > Query Forwarding* : ajouter un transfert sans
   domaine vers `127.0.0.1`, port `53535`. Avec Dnsmasq : serveur amont
   `127.0.0.1#53535`. Ne jamais indiquer le résolveur local comme DNS amont du
   relais (boucle).
3. *Services > AppAlias > Applications* : ajouter une application (nom,
   domaines, réseaux CIDR éventuels), puis **Appliquer**. L'alias `APP_<NOM>`
   apparaît dans *Pare-feu > Alias*.

## Exemple

Dans *Pare-feu > Règles > LAN*, au-dessus de la règle d'autorisation par
défaut, pour faire passer une application par un VPN :

| Action | Famille | Source  | Destination | Passerelle     |
|--------|---------|---------|-------------|----------------|
| Pass   | IPv4    | LAN net | `APP_X`     | passerelle VPN |
| Block  | IPv6    | LAN net | `APP_X`     | -              |

La règle IPv6 évite la fuite hors du VPN.

## Limites

- **DoH / DoT** : un client qui résout lui-même en chiffré ne passe pas par
  le relais.
- **CDN partagés** : une IP peut servir beaucoup de sites ; bloquer ou router
  une application peut en toucher d'autres.
- **Cache des clients** : une IP déjà en cache sur un poste n'est vue qu'à la
  prochaine résolution.
- **Écoute** : laisser le relais sur `127.0.0.1` (défaut). Ouvert au LAN, un
  client pourrait remplir les alias jusqu'au plafond par application.
- **État actuel (0.2)** : sources ASN, catalogue d'applications, règles
  `keyword:` et `regexp:` ne sont pas encore disponibles.

## Contribuer, sécurité, licence

- Contribuer : [CONTRIBUTING.md](CONTRIBUTING.md)
- Signaler une vulnérabilité : [SECURITY.md](SECURITY.md)
- Licence : BSD-2-Clause, voir [LICENSE](LICENSE)

Les listes v2fly utilisées plus tard (`geosite`) viendront de
[v2fly/domain-list-community](https://github.com/v2fly/domain-list-community)
(licence MIT) ; leur notice de copyright et le texte de la licence MIT seront
joints à la distribution.
