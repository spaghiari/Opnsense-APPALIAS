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
              "upstream_fallback": ["ip:port", ...], "follow_cname": true,
              "ttl_min": 300, "ttl_margin": 3600, "max_entries_per_app": 20000,
              "exclude_networks": ["cidr", ...], "alias_prefix": "APP_",
              "loglevel": "info"},
  "apps": [{"uuid": "...", "enabled": true, "name": "X", "description": "...",
            "origin": "manual", "domains": [...], "asns": [...],
            "networks": [...], "families": "both"}]
}
"""

import ipaddress
import json
import logging
from dataclasses import dataclass, field

from .pftable import valid_table

CONFIG_PATH = "/var/db/appalias/config.json"
STATE_PATH = "/var/db/appalias/state.json"
PIDFILE = "/var/run/appaliasd.pid"
CHILD_PIDFILE = "/var/run/appaliasd.child.pid"

FAMILIES = ("both", "inet", "inet6")
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
    """« ip:port », « [ipv6]:port », « ip » (avec default_port) -> (ip, port)."""
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
            if not rest.startswith(":"):
                raise ConfigError("amont invalide : %s" % text)
            port = rest[1:]
    elif text.count(":") == 1:
        host, port = text.split(":", 1)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ConfigError("adresse d'amont invalide : %s" % text) from exc
    if port is None or port == "":
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
    upstreams: list
    follow_cname: bool
    ttl_min: int
    ttl_margin: int
    max_entries_per_app: int
    exclude_networks: list
    alias_prefix: str
    loglevel: str
    apps: list

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
    upstream_address = str(gen.get("upstream_address") or "").strip()
    upstream_port = as_int(gen.get("upstream_port"), 53, 1, 65535)
    upstreams = []
    if upstream_address:
        reject_wildcard(upstream_address, "upstream_address")
        upstreams.append(parse_hostport(upstream_address, upstream_port))
    elif enabled:
        raise ConfigError("upstream_address est obligatoire quand le service est activé")
    # service désactivé : amont vide toléré (valeur par défaut du modèle)
    for item in as_list(gen.get("upstream_fallback")):
        upstreams.append(parse_hostport(item))

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
