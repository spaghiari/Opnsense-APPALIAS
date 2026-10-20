# Copyright (C) 2026 spaghiari
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
# OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Lecture de config.json (produit par export_config.php).

Format attendu (les valeurs peuvent être typées ou sous forme de chaînes,
comme dans le modèle MVC) :

{
  "general": {"enabled": true, "listen_address": "127.0.0.1", "listen_port": 53535,
              "upstream_address": "...", "upstream_port": 53,
              "upstream_protocol": "dns|dot", "upstream_tls_name": "dns.example",
              "upstream_fallback": ["ip[:port][#tls-name]", ...], "follow_cname": true,
              "ttl_min": 300, "ttl_margin": 3600, "max_entries_per_app": 20000,
              "exclude_networks": ["cidr", ...], "alias_prefix": "APP_",
              "loglevel": "info", "shared_platforms_extra": ["name", ...],
              "unbound_autoforward": false,
              "proactive_enabled": true, "proactive_interval_min": 300,
              "proactive_interval_max": 3600, "proactive_prefixes": ["www"],
              "proactive_deep": true, "unbound_cache_seed": true,
              "cache_enabled": true, "client_networks": ["cidr", ...],
              "client_rate_limit": 100, "pf_backend": "auto|pfctl|ioctl",
              "asn_enabled": true},
  "apps": [{"uuid": "...", "enabled": true, "name": "X", "description": "...",
            "origin": "manual", "domains": [...], "asns": [...],
            "networks": [...], "families": "both", "catalog_refs": [...],
            "proactive": true}],
  "unbound": {"enabled": true, "port": 53, "forwarding": false, "local_addresses": ["ip", ...],
              "dots": [{"domain": "...", "type": "dot|forward", "enabled": true}],
              "local_domains": ["...", ...]}
}

Amonts : upstreams est une liste d'Upstream(host, port, tls_name), l'amont
principal en tête. tls_name vaut None en DNS clair ; en DNS over TLS
(« dot »), c'est le nom vérifié dans le certificat et envoyé en SNI, obligatoire
pour chaque amont (celui de l'amont principal par défaut pour les secours).

Résolution proactive (activée par défaut, absente des anciens config.json) :
proactive_interval_min <= proactive_interval_max, ce dernier restant strictement
sous ttl_min + ttl_margin (sinon les entrées expireraient entre deux passes) ;
proactive_prefixes ne garde que des labels DNS valides, sans doublon (8 au plus).

Découverte (0.10, clés absentes des anciens config.json, vraies par défaut) :
proactive_deep ajoute aux sondes de chaque « domain:x » les labels courants
du dictionnaire (data/probe_labels.txt), hors jokers DNS détectés ;
unbound_cache_seed amorce les tables depuis le cache d'Unbound (au démarrage,
après chaque rechargement, puis toutes les heures) quand Unbound est activé.

Relais ouvrable au LAN (0.9, clés absentes des anciens config.json) :
listen_address accepte une adresse, une liste ou un texte « a,b » (export PHP) ;
Config.listen_addresses porte la liste complète (sans doublon, 127.0.0.1 si
vide) et Config.listen_address l'adresse de transfert d'Unbound (la première
adresse de bouclage, sinon la première). cache_enabled (défaut vrai) active le
cache de réponses ; client_networks liste les réseaux clients autorisés (vide =
défauts de lib/clients.py) ; client_rate_limit est le débit par client en
requêtes par seconde (0 = illimité, 100 par défaut, 100000 au plus) ;
pf_backend choisit le pilote pf (auto, pfctl ou ioctl, « auto » si inconnu) ;
asn_enabled (défaut vrai) active les sources ASN. La détection de boucle
écoute/amont reste à lib/unboundfwd.py (plan), pas ici.
"""

import ipaddress
import json
import logging
import re
from collections import namedtuple
from dataclasses import dataclass, field

from .pftable import valid_table

CONFIG_PATH = "/var/db/appalias/config.json"
STATE_PATH = "/var/db/appalias/state.json"
PIDFILE = "/var/run/appaliasd.pid"
CHILD_PIDFILE = "/var/run/appaliasd.child.pid"

FAMILIES = ("both", "inet", "inet6")
PROTOCOLS = ("dns", "dot")
DEFAULT_PORTS = {"dns": 53, "dot": 853}

# Résolution proactive : bornes (défaut, minimum, maximum) en secondes.
PROACTIVE_INTERVAL_MIN = (300, 60, 86400)
PROACTIVE_INTERVAL_MAX = (3600, 300, 86400)
PROACTIVE_MAX_PREFIXES = 8
PROACTIVE_DEFAULT_PREFIXES = ["www"]
PF_BACKENDS = ("auto", "pfctl", "ioctl")
CLIENT_RATE_LIMIT = (100, 0, 100000)
LABEL_RE = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?$")

# Nom TLS : nom d'hôte (labels LDH) ou adresse IP.
TLS_NAME_RE = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

Upstream = namedtuple("Upstream", "host port tls_name")
LOGLEVELS = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

log = logging.getLogger("appalias")


class ConfigError(Exception):
    """Configuration absente ou invalide."""


def as_bool(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def as_int(value, default, minimum=None, maximum=None):
    try:
        out = int(str(value).strip())
    except (TypeError, ValueError):
        out = default
    if minimum is not None and out < minimum:
        out = minimum
    if maximum is not None and out > maximum:
        out = maximum
    return out


def as_list(value):
    """Liste de chaînes non vides ; accepte liste ou texte séparé par , ou retour ligne."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        items = str(value).replace("\r", "\n").replace("\n", ",").split(",")
    return [i.strip() for i in items if i and i.strip()]


def parse_hostport(text, default_port=None):
    """« ip », « ip:port », « [ipv6] », « [ipv6]:port » ou IPv6 nue -> (ip, port).

    Sans port, default_port est employé (ConfigError s'il vaut None).
    """
    return _parse_hostport(text, default_port)[:2]


def _parse_hostport(text, default_port=None):
    """Comme parse_hostport, mais renvoie (ip, port, port_explicite)."""
    text = str(text).strip()
    reject_wildcard(text, "amont")
    host, port = text, None
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise ConfigError("amont invalide : %s" % text)
        host = text[1:end]
        rest = text[end + 1:]
        if rest:
            if not rest.startswith(":") or rest == ":":
                raise ConfigError("amont invalide : %s" % text)
            port = rest[1:]
    elif text.count(":") == 1:
        host, port = text.split(":", 1)
        if port == "":
            raise ConfigError("port d'amont invalide : %s" % text)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ConfigError("adresse d'amont invalide : %s" % text) from exc
    explicit = port is not None
    if port is None:
        if default_port is None:
            raise ConfigError("port d'amont manquant : %s" % text)
        port = default_port
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise ConfigError("port d'amont invalide : %s" % text) from exc
    if not 1 <= port <= 65535:
        raise ConfigError("port d'amont hors plage : %s" % text)
    return (str(ip), port, explicit)


def normalize_protocol_port(protocol, port, what):
    """Port du bon protocole : 53 en « dot » devient 853, 853 en « dns » devient 53.

    Une config 0.4 éditée à la main ou enregistrée avant 0.4.1 ne doit pas
    empêcher le démon de démarrer : correction avec un warning (le modèle
    refuse désormais ces combinaisons à l'enregistrement).
    """
    if protocol == "dot" and port == 53:
        log.warning("%s: port 53 with DNS over TLS replaced by 853", what)
        return 853
    if protocol == "dns" and port == 853:
        log.warning("%s: port 853 with plain DNS replaced by 53", what)
        return 53
    return port


def valid_tls_name(name):
    """Vrai si name est un nom d'hôte (labels LDH, 253 caractères au plus) ou une IP."""
    name = str(name or "").strip()
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    name = name.lower().rstrip(".")
    return 0 < len(name) <= 253 and TLS_NAME_RE.match(name) is not None


def norm_tls_name(name):
    """Nom TLS normalisé (minuscules, sans point final), ou None si vide."""
    name = str(name or "").strip()
    if not name:
        return None
    try:
        return str(ipaddress.ip_address(name))
    except ValueError:
        return name.lower().rstrip(".")


def parse_upstream(text, default_port, default_tls_name=None, protocol="dns"):
    """Amont de secours « ip[:port][#nom-tls] » (syntaxe à la Unbound) -> Upstream.

    Port par défaut : default_port. Nom TLS : celui du suffixe « #nom », sinon
    default_tls_name ; en « dot », un nom est obligatoire. En « dns », le nom
    est contrôlé puis ignoré (tls_name=None).
    """
    text = str(text).strip()
    name = None
    if "#" in text:
        text, name = text.split("#", 1)
        name = name.strip()
        if not valid_tls_name(name):
            raise ConfigError("nom TLS d'amont invalide : %s" % name)
    host, port, explicit = _parse_hostport(text, default_port)
    if explicit:
        port = normalize_protocol_port(protocol, port, "upstream_fallback %s" % text)
    if protocol != "dot":
        return Upstream(host, port, None)
    tls_name = norm_tls_name(name) or norm_tls_name(default_tls_name)
    if tls_name is None:
        raise ConfigError("DNS over TLS : nom TLS manquant pour l'amont %s "
                          "(upstream_tls_name ou suffixe #nom)" % text)
    return Upstream(host, port, tls_name)


def parse_prefixes(value):
    """Préfixes de la résolution proactive -> liste de labels DNS (minuscules).

    Un label invalide est écarté avec un avertissement, les doublons sont
    ignorés, 8 au plus. Clé absente : ["www"] ; liste vide : aucun préfixe.
    """
    if value is None:
        return list(PROACTIVE_DEFAULT_PREFIXES)
    out = []
    for item in as_list(value):
        label = item.strip().lower()
        if not LABEL_RE.match(label):
            log.warning("proactive_prefixes : préfixe écarté (label DNS invalide) : %s", item)
            continue
        if label in out:
            continue
        if len(out) >= PROACTIVE_MAX_PREFIXES:
            log.warning("proactive_prefixes : %d préfixes au plus, %s écarté", PROACTIVE_MAX_PREFIXES, label)
            continue
        out.append(label)
    return out


def proactive_intervals(gen, ttl_min, ttl_margin):
    """(intervalle min, intervalle max, actif) de la résolution proactive, cohérents.

    interval_max reste strictement sous ttl_min + ttl_margin (expiration d'une
    entrée, cf. state.py) sinon les entrées clignotent ; interval_min est
    ramené à interval_max s'il le dépasse. Chaque correction est journalisée.
    Si ttl_min + ttl_margin - 1 tombe sous le plancher d'intervalle
    (PROACTIVE_INTERVAL_MIN[1]), aucun intervalle sûr n'existe (rafale DNS) :
    le proactif est désactivé (actif = False) avec un avertissement unique.
    """
    floor = PROACTIVE_INTERVAL_MIN[1]
    imin = as_int(gen.get("proactive_interval_min"), *PROACTIVE_INTERVAL_MIN)
    imax = as_int(gen.get("proactive_interval_max"), *PROACTIVE_INTERVAL_MAX)
    limit = ttl_min + ttl_margin - 1
    enabled = True
    if limit < floor:
        log.warning("résolution proactive désactivée : ttl_min + ttl_margin (%d) trop faible, "
                    "l'intervalle devrait rester sous %d s (minimum %d s) ; augmenter ttl_min ou ttl_margin",
                    ttl_min + ttl_margin, limit, floor)
        enabled = False
    elif imax > limit:
        log.warning("proactive_interval_max (%d) doit rester sous ttl_min + ttl_margin (%d) : ramené à %d",
                    imax, ttl_min + ttl_margin, limit)
        imax = limit
    if imin > imax:
        if enabled:
            log.warning("proactive_interval_min (%d) supérieur à proactive_interval_max (%d) : ramené à %d",
                        imin, imax, imax)
        imin = imax
    return imin, imax, enabled


def reject_wildcard(value, what):
    """« any » (joker OPNsense) n'a pas de sens ici : refus explicite."""
    if str(value).strip().lower() in ("any", "*"):
        raise ConfigError("%s : « %s » n'est pas accepté, indiquer une adresse ou un réseau précis"
                          % (what, str(value).strip()))


def parse_networks(values, what):
    nets = []
    for item in as_list(values):
        reject_wildcard(item, what)
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            log.warning("%s : réseau ignoré (invalide) : %s", what, item)
    return nets


def parse_listen_addresses(value):
    """Adresses d'écoute (texte « a,b », liste ou adresse seule) -> liste canonique sans doublon.

    Vide : ["127.0.0.1"]. « any » et toute adresse invalide lèvent ConfigError.
    """
    out = []
    for item in as_list(value):
        reject_wildcard(item, "listen_address")
        try:
            addr = str(ipaddress.ip_address(item))
        except ValueError as exc:
            raise ConfigError("listen_address invalide : %s" % item) from exc
        if addr not in out:
            out.append(addr)
    return out or ["127.0.0.1"]


def unbound_address(addresses):
    """Adresse de transfert d'Unbound : première adresse de bouclage, sinon la première."""
    for addr in addresses:
        if ipaddress.ip_address(addr).is_loopback:
            return addr
    return addresses[0]


@dataclass
class App:
    uuid: str
    name: str
    table: str
    enabled: bool
    description: str = ""
    origin: str = "manual"
    domains: list = field(default_factory=list)
    asns: list = field(default_factory=list)
    networks: list = field(default_factory=list)
    families: str = "both"
    catalog_refs: list = field(default_factory=list)
    proactive: bool = True

    def family_ok(self, ip):
        if self.families == "inet":
            return ip.version == 4
        if self.families == "inet6":
            return ip.version == 6
        return True

    def covered(self, ip):
        """Vrai si l'IP est déjà couverte par un CIDR manuel."""
        return any(ip.version == n.version and ip in n for n in self.networks)


@dataclass
class Config:
    enabled: bool
    listen_address: str
    listen_port: int
    upstreams: list          # [Upstream(host, port, tls_name)]
    follow_cname: bool
    ttl_min: int
    ttl_margin: int
    max_entries_per_app: int
    exclude_networks: list
    alias_prefix: str
    loglevel: str
    apps: list
    shared_platforms_extra: list = field(default_factory=list)
    unbound_autoforward: bool = False
    unbound: dict = field(default_factory=dict)
    upstream_protocol: str = "dns"
    upstream_tls_name: str = None
    proactive_enabled: bool = True
    proactive_interval_min: int = PROACTIVE_INTERVAL_MIN[0]
    proactive_interval_max: int = PROACTIVE_INTERVAL_MAX[0]
    proactive_prefixes: list = field(default_factory=lambda: list(PROACTIVE_DEFAULT_PREFIXES))
    listen_addresses: list = field(default_factory=list)   # toutes les adresses d'écoute
    cache_enabled: bool = True
    client_networks: list = field(default_factory=list)    # vide = défauts de lib/clients.py
    client_rate_limit: int = CLIENT_RATE_LIMIT[0]          # requêtes/s par client, 0 = illimité
    pf_backend: str = "auto"
    asn_enabled: bool = True
    proactive_deep: bool = True          # dictionnaire de labels (découverte profonde)
    unbound_cache_seed: bool = True      # amorçage depuis le cache d'Unbound

    def __post_init__(self):
        # Construction directe (tests) : liste cohérente avec listen_address.
        if not self.listen_addresses:
            self.listen_addresses = [self.listen_address]

    def excluded(self, ip):
        return any(ip.version == n.version and ip in n for n in self.exclude_networks)

    @property
    def log_level(self):
        return LOGLEVELS.get(self.loglevel, logging.INFO)

    def app_by_table(self):
        return {a.table: a for a in self.apps}


def from_dict(data):
    if not isinstance(data, dict):
        raise ConfigError("config.json : objet JSON attendu")
    gen = data.get("general") or {}
    if not isinstance(gen, dict):
        raise ConfigError("config.json : section general invalide")

    listen_addresses = parse_listen_addresses(gen.get("listen_address"))
    listen_address = unbound_address(listen_addresses)
    listen_port = as_int(gen.get("listen_port"), 53535)
    if not 1 <= listen_port <= 65535:
        raise ConfigError("listen_port hors plage (1-65535) : %s" % gen.get("listen_port"))

    enabled = as_bool(gen.get("enabled"), False)
    protocol = str(gen.get("upstream_protocol") or "dns").strip().lower()
    if protocol not in PROTOCOLS:
        # jamais de repli silencieux vers le DNS clair
        raise ConfigError("upstream_protocol invalide : %s (dns ou dot)" % gen.get("upstream_protocol"))
    tls_name_raw = str(gen.get("upstream_tls_name") or "").strip()
    if tls_name_raw and not valid_tls_name(tls_name_raw):
        raise ConfigError("upstream_tls_name invalide : %s" % tls_name_raw)
    tls_name = norm_tls_name(tls_name_raw)
    default_port = DEFAULT_PORTS[protocol]
    upstream_address = str(gen.get("upstream_address") or "").strip()
    upstream_port = as_int(gen.get("upstream_port"), default_port, 1, 65535)
    upstreams = []
    if upstream_address:
        reject_wildcard(upstream_address, "upstream_address")
        if protocol == "dot" and tls_name is None:
            raise ConfigError("upstream_tls_name est obligatoire en DNS over TLS")
        host, port, explicit = _parse_hostport(upstream_address, upstream_port)
        port = normalize_protocol_port(protocol, port, "upstream_address" if explicit else "upstream_port")
        upstreams.append(Upstream(host, port, tls_name if protocol == "dot" else None))
    elif enabled:
        raise ConfigError("upstream_address est obligatoire quand le service est activé")
    # service désactivé : amont vide toléré (valeur par défaut du modèle)
    for item in as_list(gen.get("upstream_fallback")):
        upstreams.append(parse_upstream(item, default_port, tls_name, protocol))

    pf_backend = str(gen.get("pf_backend") or "auto").strip().lower()
    if pf_backend not in PF_BACKENDS:
        log.warning("pf_backend inconnu « %s » : « auto » employé", gen.get("pf_backend"))
        pf_backend = "auto"

    prefix = str(gen.get("alias_prefix") if gen.get("alias_prefix") is not None else "APP_")
    loglevel = str(gen.get("loglevel") or "info").strip().lower()
    if loglevel not in LOGLEVELS:
        loglevel = "info"

    apps = []
    seen = set()
    raw_apps = data.get("apps") or []
    if not isinstance(raw_apps, list):
        raise ConfigError("config.json : section apps invalide")
    for raw in raw_apps:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        table = prefix + name
        if not name or not valid_table(table):
            log.warning("application ignorée : nom de table invalide « %s »", table)
            continue
        if table in seen:
            log.warning("application ignorée : table en double « %s »", table)
            continue
        seen.add(table)
        families = str(raw.get("families") or "both").strip()
        if families not in FAMILIES:
            families = "both"
        apps.append(App(
            uuid=str(raw.get("uuid") or ""),
            name=name,
            table=table,
            enabled=as_bool(raw.get("enabled"), True),
            description=str(raw.get("description") or ""),
            origin=str(raw.get("origin") or "manual"),
            domains=as_list(raw.get("domains")),
            asns=as_list(raw.get("asns")),
            networks=parse_networks(raw.get("networks"), table),
            families=families,
            catalog_refs=as_list(raw.get("catalog_refs")),
            proactive=as_bool(raw.get("proactive"), True),
        ))

    ttl_min = as_int(gen.get("ttl_min"), 300, 0)
    ttl_margin = as_int(gen.get("ttl_margin"), 3600, 0)
    proactive_min, proactive_max, proactive_ok = proactive_intervals(gen, ttl_min, ttl_margin)

    return Config(
        enabled=enabled,
        listen_address=listen_address,
        listen_port=listen_port,
        upstreams=upstreams,
        follow_cname=as_bool(gen.get("follow_cname"), True),
        ttl_min=ttl_min,
        ttl_margin=ttl_margin,
        max_entries_per_app=as_int(gen.get("max_entries_per_app"), 20000, 1),
        exclude_networks=parse_networks(gen.get("exclude_networks"), "exclude_networks"),
        alias_prefix=prefix,
        loglevel=loglevel,
        apps=apps,
        shared_platforms_extra=as_list(gen.get("shared_platforms_extra")),
        unbound_autoforward=as_bool(gen.get("unbound_autoforward"), False),
        unbound=data.get("unbound") if isinstance(data.get("unbound"), dict) else {},
        upstream_protocol=protocol,
        upstream_tls_name=tls_name,
        proactive_enabled=as_bool(gen.get("proactive_enabled"), True) and proactive_ok,
        proactive_interval_min=proactive_min,
        proactive_interval_max=proactive_max,
        proactive_prefixes=parse_prefixes(gen.get("proactive_prefixes")),
        listen_addresses=listen_addresses,
        cache_enabled=as_bool(gen.get("cache_enabled"), True),
        client_networks=parse_networks(gen.get("client_networks"), "client_networks"),
        client_rate_limit=as_int(gen.get("client_rate_limit"), *CLIENT_RATE_LIMIT),
        pf_backend=pf_backend,
        asn_enabled=as_bool(gen.get("asn_enabled"), True),
        proactive_deep=as_bool(gen.get("proactive_deep"), True),
        unbound_cache_seed=as_bool(gen.get("unbound_cache_seed"), True),
    )


def load(path=CONFIG_PATH):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError("config absente : %s (lancer « configctl appalias export »)" % path) from exc
    except (OSError, ValueError) as exc:
        raise ConfigError("config illisible : %s : %s" % (path, exc)) from exc
    return from_dict(data)
