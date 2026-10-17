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

"""Garde-fou des plateformes partagées (CDN, cloud, hébergement).

La liste vit dans data/shared_platforms.txt, source unique lue aussi par le
modèle PHP (AppAlias::sharedPlatforms). Une règle « domain:x » est refusée
quand x est un domaine de premier niveau seul (« com ») ou quand x est égal à
une plateforme partagée, ou parent de l'une d'elles (« cloudfront.net »,
« net ») : elle ferait entrer dans l'alias les adresses d'innombrables sites.
« domain:d123.cloudfront.net » (sous-domaine dédié) et « full:x » restent
acceptés.
"""

import logging
import os
import re

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "shared_platforms.txt")

_NAME = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?(\.[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)*$")

log = logging.getLogger("appalias")


def normalize(name):
    name = str(name).strip().lower().rstrip(".")
    if name.startswith("*."):
        name = name[2:]
    return name


def parse(text):
    """Noms valides d'un texte (un par ligne, « # » = commentaire)."""
    out = set()
    for raw in str(text).splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name = normalize(line)
        if _NAME.match(name):
            out.add(name)
    return out


def load(path=DEFAULT_PATH, extra=()):
    """Plateformes du fichier, plus les noms supplémentaires (réglage
    general.shared_platforms_extra). Fichier illisible : journalisé, la liste
    supplémentaire reste appliquée."""
    names = set()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            names = parse(fh.read())
    except OSError as exc:
        log.warning("plateformes partagées : lecture impossible de %s : %s", path, exc)
    for item in extra or ():
        name = normalize(item)
        if _NAME.match(name):
            names.add(name)
    return frozenset(names)


def is_tld(value):
    return "." not in value


def covers_platform(value, platforms):
    """Vrai si value est égal à une plateforme ou parent de l'une d'elles."""
    suffix = "." + value
    return any(p == value or p.endswith(suffix) for p in platforms)


def under_platform(value, platforms):
    """Vrai si value est un sous-domaine strict d'une plateforme."""
    return any(value.endswith("." + p) for p in platforms)


def refused(kind, value, platforms):
    """Raison du refus d'une règle (« tld » ou « shared »), ou None."""
    if kind != "domain":
        return None
    value = normalize(value)
    if not value:
        return None
    if is_tld(value):
        return "tld"
    if covers_platform(value, platforms):
        return "shared"
    return None


def touches(value, platforms):
    """Vrai si le nom concerne une plateforme partagée (égal, parent ou
    sous-domaine) : signalé dans l'interface, décoché par défaut."""
    value = normalize(value)
    return bool(value) and (covers_platform(value, platforms) or under_platform(value, platforms))
