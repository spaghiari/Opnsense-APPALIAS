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
              "unbound_autoforward": false},
  "apps": [{"uuid": "...", "enabled": true, "name": "X", "description": "...",
            "origin": "manual", "domains": [...], "asns": [...],
            "networks": [...], "families": "both", "catalog_refs": [...]}],
  "unbound": {"enabled": true, "port": 53, "forwarding": false, "local_addresses": ["ip", ...],
              "dots": [{"domain": "...", "type": "dot|forward", "enabled": true}],
              "local_domains": ["...", ...]}
}

Amonts : upstreams est une liste d'Upstream(host, port, tls_name), l'amont
principal en tête. tls_name vaut None en DNS clair ; en DNS over TLS
(« dot »), c'est le nom vérifié dans le certificat et envoyé en SNI, obligatoire
pour chaque amont (celui de l'amont principal par défaut pour les secours).
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
    return (str(ip), port)


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
    host, port = parse_hostport(text, default_port)
    if protocol != "dot":
        return Upstream(host, port, None)
    tls_name = norm_tls_name(name) or norm_tls_name(default_tls_name)
    if tls_name is None:
        raise ConfigError("DNS over TLS : nom TLS manquant pour l'amont %s "
                          "(upstream_tls_name ou suffixe #nom)" % text)
    return Upstream(host, port, tls_name)


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

    listen_address = str(gen.get("listen_address") or "127.0.0.1").strip()
    reject_wildcard(listen_address, "listen_address")
    try:
        listen_address = str(ipaddress.ip_address(listen_address))
    except ValueError as exc:
        raise ConfigError("listen_address invalide : %s" % listen_address) from exc
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
    # Port enregistré respecté tel quel (53 en « dot » : l'interface le signale).
    upstream_port = as_int(gen.get("upstream_port"), default_port, 1, 65535)
    upstreams = []
    if upstream_address:
        reject_wildcard(upstream_address, "upstream_address")
        if protocol == "dot" and tls_name is None:
            raise ConfigError("upstream_tls_name est obligatoire en DNS over TLS")
        host, port = parse_hostport(upstream_address, upstream_port)
        upstreams.append(Upstream(host, port, tls_name if protocol == "dot" else None))
    elif enabled:
        raise ConfigError("upstream_address est obligatoire quand le service est activé")
    # service désactivé : amont vide toléré (valeur par défaut du modèle)
    for item in as_list(gen.get("upstream_fallback")):
        upstreams.append(parse_upstream(item, default_port, tls_name, protocol))

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
        ))

    return Config(
        enabled=enabled,
        listen_address=listen_address,
        listen_port=listen_port,
        upstreams=upstreams,
        follow_cname=as_bool(gen.get("follow_cname"), True),
        ttl_min=as_int(gen.get("ttl_min"), 300, 0),
        ttl_margin=as_int(gen.get("ttl_margin"), 3600, 0),
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
