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

"""Préfixes BGP annoncés par les ASN des applications (source RIPEstat).

Module pur, sauf load_cache / save_cache (cache JSON) et fetch (réseau).
asn_update.py (cron quotidien, après Apply) tient le cache à jour ; le démon
le lit et place les préfixes dans les tables pf des applications.

Format du cache (CACHE_PATH) :
  {"version": 1, "asns": {"2906": {"prefixes": ["198.51.100.0/24", ...],
                                   "fetched": 1700000000, "error": null,
                                   "attempted": 1700003600}}}
« fetched » est l'heure du dernier succès (null si jamais réussi) ;
« attempted » (présent seulement après un échec) celle de la dernière
tentative, en erreur ; « error » le message de cet échec, null après un
succès.

Préfixes retenus (prefixes_for) : pas plus larges que /8 en IPv4 ni /19 en
IPv6, et jamais privés, de bouclage, lien local, multicast, réservés ou non
spécifiés (une réponse aberrante ne doit pas ouvrir tout Internet ni le LAN).

Réseau : HTTPS seulement, vers stat.ripe.net, sans redirection vers un autre
hôte, corps plafonné à MAX_BODY octets pendant la lecture.
"""

import ipaddress
import json
import logging
import os
import tempfile
import urllib.parse
import urllib.request

from lib import dot
from lib import pftable

CACHE_PATH = "/var/db/appalias/asn.json"
CACHE_VERSION = 1
API_URL = "https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS%d"
ALLOWED_HOSTS = frozenset(("stat.ripe.net",))
MAX_BODY = 4 * 1024 * 1024
TIMEOUT = 30
REFRESH = 24 * 3600
RETRY_AFTER = 3600
MAX_PREFIXES_PER_APP = 20000
ASN_MAX = 4294967295
MIN_PREFIXLEN = {4: 8, 6: 19}   # préfixes plus larges refusés
USER_AGENT = "os-appalias-asn-update"

log = logging.getLogger("appalias")


class AsnError(Exception):
    """Récupération des préfixes refusée ou impossible."""


# --- ASN -----------------------------------------------------------------
def parse_asn(text):
    """« AS2906 », « as2906 », « 2906 » -> 2906 ; valeur invalide -> None."""
    if isinstance(text, bool):
        return None
    if isinstance(text, int):
        return text if 0 < text <= ASN_MAX else None
    if not isinstance(text, str):
        return None
    value = text.strip()
    if value[:2].lower() == "as":
        value = value[2:]
    if not value or not value.isascii() or not value.isdigit():
        return None
    number = int(value)
    return number if 0 < number <= ASN_MAX else None


def app_asns(app):
    """ASN valides d'une application, dédoublonnés, ordre conservé."""
    raw = app.get("asns") if isinstance(app, dict) else getattr(app, "asns", None)
    out = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        number = parse_asn(item)
        if number is not None and number not in out:
            out.append(number)
    return out


def needed_asns(cfg):
    """ASN de toutes les applications (activées ou non)."""
    out = set()
    for app in cfg.apps:
        out.update(app_asns(app))
    return out


# --- cache -----------------------------------------------------------------
def _clean_entry(entry):
    if not isinstance(entry, dict):
        return None
    prefixes = entry.get("prefixes")
    if not isinstance(prefixes, list):
        return None
    fetched = entry.get("fetched")
    if isinstance(fetched, bool) or not isinstance(fetched, (int, float)):
        fetched = None
    error = entry.get("error")
    out = {"prefixes": [p for p in prefixes if isinstance(p, str)],
           "fetched": int(fetched) if fetched is not None else None,
           "error": error if isinstance(error, str) else None}
    attempted = entry.get("attempted")
    if not isinstance(attempted, bool) and isinstance(attempted, (int, float)):
        out["attempted"] = int(attempted)
    return out


def load_cache(path=CACHE_PATH):
    """Cache relu ; absent, illisible ou mal formé = cache vide valide."""
    cache = {"version": CACHE_VERSION, "asns": {}}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return cache
    except (OSError, ValueError) as exc:
        log.warning("cache ASN illisible (%s), ignoré : %s", path, exc)
        return cache
    asns = data.get("asns") if isinstance(data, dict) else None
    if not isinstance(asns, dict):
        return cache
    for key, entry in asns.items():
        number = parse_asn(key) if isinstance(key, str) else None
        clean = _clean_entry(entry)
        if number is not None and clean is not None:
            cache["asns"][str(number)] = clean
    return cache


def save_cache(data, path=CACHE_PATH):
    """Écriture atomique : temporaire dans le même dossier, fsync, puis rename."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".asn.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, 0o640)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def is_stale(entry, now, refresh=REFRESH, retry=RETRY_AFTER):
    """Entrée à rafraîchir : jamais récupérée, trop ancienne, ou en erreur depuis retry.

    En erreur, le délai retry part de la dernière tentative (« attempted »,
    sinon « fetched ») : un ASN en échec n'est pas réinterrogé à chaque appel.
    """
    if not isinstance(entry, dict):
        return True
    fetched = entry.get("fetched")
    if entry.get("error") is not None:
        last = entry.get("attempted")
        if last is None:
            last = fetched
        return last is None or now - last >= retry
    if fetched is None:
        return True
    return now - fetched >= refresh


def acceptable_prefix(net):
    """Vrai si le préfixe peut entrer dans un alias (largeur, plage routable)."""
    if net.prefixlen < MIN_PREFIXLEN[net.version]:
        return False
    # is_global couvre aussi l'espace partagé 100.64.0.0/10 (RFC 6598), que
    # is_private ne signale pas selon la version de Python.
    return net.is_global and not (net.is_private or net.is_loopback or net.is_link_local
                                  or net.is_multicast or net.is_reserved or net.is_unspecified)


# --- préfixes ---------------------------------------------------------------
def prefixes_for(asns, cache):
    """Union agrégée des préfixes des ASN présents dans le cache (IPv4 puis IPv6)."""
    entries = cache.get("asns", {}) if isinstance(cache, dict) else {}
    nets = {4: [], 6: []}
    skipped = 0
    for number in asns:
        entry = entries.get(str(number))
        for prefix in entry.get("prefixes", []) if isinstance(entry, dict) else []:
            try:
                net = ipaddress.ip_network(str(prefix).strip(), strict=False)
            except ValueError:
                continue
            if not acceptable_prefix(net):
                skipped += 1
                continue
            nets[net.version].append(net)
    if skipped:
        log.debug("ASN %s : %d préfixes écartés (trop larges ou non routables)",
                  ",".join(str(a) for a in asns), skipped)
    out = []
    for version in (4, 6):
        out.extend(pftable.normalize_entry(n) for n in ipaddress.collapse_addresses(nets[version]))
    if len(out) > MAX_PREFIXES_PER_APP:
        log.warning("ASN %s : %d préfixes, tronqué à %d",
                    ",".join(str(a) for a in asns), len(out), MAX_PREFIXES_PER_APP)
        out = out[:MAX_PREFIXES_PER_APP]
    return out


def prefixes_by_table(cfg, cache):
    """{table: préfixes} des applications activées ayant des ASN."""
    out = {}
    for app in cfg.apps:
        if not app.enabled:
            continue
        asns = app_asns(app)
        if asns:
            out[app.table] = prefixes_for(asns, cache)
    return out


# --- réseau ------------------------------------------------------------------
def check_url(url):
    """HTTPS vers un hôte autorisé, sans port ni identifiants ; AsnError sinon."""
    parts = urllib.parse.urlsplit(str(url))
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS \
            or parts.port is not None or parts.username or parts.password:
        raise AsnError("URL refusée (HTTPS uniquement vers %s) : %s"
                       % (", ".join(sorted(ALLOWED_HOSTS)), parts.hostname or url))
    return url


class _SameHostsRedirect(urllib.request.HTTPRedirectHandler):
    """Redirection suivie seulement vers un hôte autorisé, en HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def make_opener(cafile=dot.CA_BUNDLE):
    ctx = dot.make_context(cafile)
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _SameHostsRedirect())


def http_get(url, max_bytes, opener, timeout=TIMEOUT):
    """Corps de url, plafonné à max_bytes pendant la lecture."""
    check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    chunks = []
    size = 0
    try:
        with opener.open(req, timeout=timeout) as resp:
            check_url(resp.geturl())
            length = resp.headers.get("Content-Length") if getattr(resp, "headers", None) else None
            if length and length.isdigit() and int(length) > max_bytes:
                raise AsnError("réponse trop grande (%s octets, %d au plus)" % (length, max_bytes))
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise AsnError("réponse trop grande (plus de %d octets)" % max_bytes)
                chunks.append(chunk)
    except AsnError:
        raise
    except (OSError, ValueError) as exc:
        raise AsnError("téléchargement impossible : %s" % exc) from exc
    return b"".join(chunks)


def fetch(asn, opener, timeout=TIMEOUT):
    """Préfixes annoncés par l'ASN (RIPEstat), valides et dédoublonnés."""
    body = http_get(API_URL % int(asn), MAX_BODY, opener, timeout)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AsnError("réponse RIPEstat illisible") from exc
    inner = data.get("data") if isinstance(data, dict) else None
    items = inner.get("prefixes") if isinstance(inner, dict) else None
    if not isinstance(items, list):
        raise AsnError("réponse RIPEstat sans liste de préfixes")
    out = []
    seen = set()
    for item in items:
        prefix = item.get("prefix") if isinstance(item, dict) else None
        if not isinstance(prefix, str):
            continue
        try:
            net = ipaddress.ip_network(prefix.strip(), strict=False)
        except ValueError:
            continue
        text = str(net)
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out
