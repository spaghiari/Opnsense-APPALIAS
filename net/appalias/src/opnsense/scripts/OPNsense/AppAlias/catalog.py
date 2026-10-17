#!/usr/local/bin/python3
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

"""Catalogue v2fly embarqué : interrogation en JSON (actions configd).

  catalog.py info                 état du catalogue (commit, date, intégrité)
  catalog.py search <q>           listes par nom, et inverse par domaine (20 max.)
  catalog.py preview <ref>        règles résolues d'une référence, avec drapeaux
  catalog.py resolve <ref,ref>    union des règles de plusieurs références
  catalog.py popular              galerie « Popular apps » (catalog/popular.json)

<ref> : nom de liste suivi de filtres d'attribut (« netflix@-ads »), validé
par ^[a-z0-9-]+(@-?[a-z0-9-]+)*$ ; <q> : [a-z0-9.-], 64 caractères au plus.
Aucune option en ligne de commande : les arguments viennent de configd et
sont pris tels quels comme paramètres, jamais comme options.

Index de recherche mis en cache dans /var/db/appalias/catalog.index.json,
reconstruit quand le sha256 du .lst change. Aucun accès réseau.
"""

import hashlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import shared  # noqa: E402
from lib import v2fly  # noqa: E402

CATALOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog")
CACHE_PATH = "/var/db/appalias/catalog.index.json"
LST_NAME = "v2fly.lst"
MANIFEST_NAME = "v2fly.manifest.json"
LICENSE_NAME = "LICENSE.v2fly"
POPULAR_NAME = "popular.json"

INDEX_VERSION = 1
MAX_RESULTS = 20
MAX_REFS = 32
MAX_IMPORT = 1000
WARN_IMPORT = 300
QUERY_RE = re.compile(r"^[a-z0-9.-]{1,64}$")
APP_NAME_RE = re.compile(r"[^A-Z0-9_]")


class CatalogError(Exception):
    """Erreur rendue en JSON ({"error": ...})."""


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


class Store:
    """Accès au catalogue : fichiers embarqués, vérification, cache d'index."""

    def __init__(self, catalog_dir=CATALOG_DIR, cache_path=CACHE_PATH):
        self.catalog_dir = catalog_dir
        self.cache_path = cache_path
        self._raw = None
        self._manifest = None
        self._catalog = None

    def raw(self):
        if self._raw is None:
            try:
                with open(os.path.join(self.catalog_dir, LST_NAME), "rb") as fh:
                    self._raw = fh.read()
            except OSError as exc:
                raise CatalogError("catalog unavailable: %s" % exc.strerror) from exc
        return self._raw

    def manifest(self):
        if self._manifest is None:
            try:
                with open(os.path.join(self.catalog_dir, MANIFEST_NAME), "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError) as exc:
                raise CatalogError("catalog manifest unreadable") from exc
            if not isinstance(data, dict):
                raise CatalogError("catalog manifest unreadable")
            self._manifest = data
        return self._manifest

    def lst_sha256(self):
        return sha256_bytes(self.raw())

    def check(self):
        """Le .lst doit correspondre au manifeste."""
        if self.manifest().get("lst_sha256") != self.lst_sha256():
            raise CatalogError("catalog integrity check failed (v2fly.lst differs from the manifest)")

    def catalog(self):
        if self._catalog is None:
            self.check()
            self._catalog = v2fly.Catalog.parse(self.raw().decode("utf-8", "replace"))
        return self._catalog

    def revision(self):
        return str(self.manifest().get("commit") or self.lst_sha256())

    # -- index de recherche --
    def build_index(self):
        cat = self.catalog()
        lists = {}
        domains = {}
        for name in cat.names():
            ldef = cat.lists[name]
            res = cat.resolve(name)
            lists[name] = {
                "rules": len(res.rules),
                "attrs": sorted(res.attrs()),
                "includes": sorted({inc for inc, _f in ldef.includes}),
                "broken": res.broken,
                "missing": sorted(res.missing),
            }
            for kind, value in ldef.rules:
                if kind in (v2fly.KIND_DOMAIN, v2fly.KIND_FULL):
                    domains.setdefault(v2fly.rule_text(kind, value), []).append(name)
        return {
            "version": INDEX_VERSION,
            "lst_sha256": self.lst_sha256(),
            "lists": lists,
            "domains": domains,
        }

    def index(self):
        digest = self.lst_sha256()
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if (isinstance(data, dict) and data.get("version") == INDEX_VERSION
                    and data.get("lst_sha256") == digest
                    and isinstance(data.get("lists"), dict) and isinstance(data.get("domains"), dict)):
                self.check()
                return data
        except (OSError, ValueError):
            pass
        data = self.build_index()
        self._write_cache(data)
        return data

    def _write_cache(self, data):
        """Écriture atomique ; un échec n'empêche pas de répondre."""
        directory = os.path.dirname(self.cache_path) or "."
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".catalog.", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, separators=(",", ":"), sort_keys=True)
            os.chmod(tmp, 0o640)
            os.replace(tmp, self.cache_path)
            tmp = None
        except OSError:
            pass
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def suggested_name(list_name):
    """« netflix » -> « NETFLIX » (nom d'application proposé)."""
    name = APP_NAME_RE.sub("_", list_name.upper().replace("-", "_")).strip("_")
    return (name or "APP")[:31]


def summary(name, entry, match, via=None):
    out = {
        "name": name,
        "rules": entry.get("rules", 0),
        "attrs": entry.get("attrs", []),
        "includes": entry.get("includes", []),
        "broken": entry.get("broken"),
        "missing": entry.get("missing", []),
        "match": match,
    }
    if via is not None:
        out["via"] = via
    return out


def cmd_info(store):
    manifest = store.manifest()
    digest = store.lst_sha256()
    return {
        "source": manifest.get("source"),
        "commit": manifest.get("commit"),
        "date": manifest.get("date"),
        "license": manifest.get("license"),
        "license_file": os.path.isfile(os.path.join(store.catalog_dir, LICENSE_NAME)),
        "lists": (manifest.get("counts") or {}).get("lists_kept"),
        "lst_bytes": len(store.raw()),
        "lst_sha256": digest,
        "integrity": manifest.get("lst_sha256") == digest,
        "max_import": MAX_IMPORT,
        "warn_import": WARN_IMPORT,
    }


def cmd_popular(store):
    """Catégories de popular.json ; seuls les noms présents dans le catalogue
    sont rendus (les autres sont listés dans « missing »)."""
    try:
        with open(os.path.join(store.catalog_dir, POPULAR_NAME), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CatalogError("popular list unreadable") from exc
    raw = data.get("categories") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise CatalogError("popular list unreadable")
    lists = store.index()["lists"]
    categories = []
    missing = []
    for cat in raw:
        if not isinstance(cat, dict) or not isinstance(cat.get("lists"), list):
            continue
        title = str(cat.get("name") or "")[:64]
        items = []
        for name in cat["lists"]:
            if not isinstance(name, str) or not v2fly.REF_RE.match(name) or "@" in name:
                continue
            if name in lists:
                items.append(summary(name, lists[name], "popular"))
            else:
                missing.append(name)
        if items:
            categories.append({"name": title, "lists": items})
    return {"categories": categories, "missing": missing}


def cmd_search(store, query):
    if not isinstance(query, str) or not QUERY_RE.match(query):
        raise CatalogError("invalid search: use 1 to 64 characters among a-z, 0-9, '.' and '-'")
    index = store.index()
    lists = index["lists"]
    found = []
    seen = set()

    def add(name, match, via=None):
        if name in seen or name not in lists or len(found) >= MAX_RESULTS:
            return
        seen.add(name)
        found.append(summary(name, lists[name], match, via))

    # 1. par nom de liste : exact, préfixe, puis sous-chaîne
    ranked = []
    for name in lists:
        if name == query:
            ranked.append((0, name))
        elif name.startswith(query):
            ranked.append((1, name))
        elif query in name:
            ranked.append((2, name))
    for _rank, name in sorted(ranked):
        add(name, "name")

    # 2. inverse par domaine : règle full: exacte, ou domain: égale ou parente
    domains = index["domains"]
    name = query.strip(".")
    labels = name.split(".") if name else []
    keys = ["full:" + name] if name else []
    keys += ["domain:" + ".".join(labels[i:]) for i in range(len(labels))]
    for key in keys:
        for list_name in sorted(domains.get(key, ())):
            add(list_name, "domain", key)
    # 3. sous-chaîne d'une règle de domaine (3 caractères au moins)
    if len(name) >= 3 and len(found) < MAX_RESULTS:
        for key in sorted(domains):
            if name in key.split(":", 1)[1]:
                for list_name in sorted(domains[key]):
                    add(list_name, "domain", key)
            if len(found) >= MAX_RESULTS:
                break
    return {"query": query, "results": found, "max": MAX_RESULTS}


def parse_refs(text):
    if not isinstance(text, str) or not text or len(text) > 2048:
        raise CatalogError("invalid reference")
    refs = []
    for ref in text.split(","):
        if not v2fly.REF_RE.match(ref) or len(ref) > 128:
            raise CatalogError("invalid reference: use a list name followed by @attr or @-attr filters")
        if ref not in refs:
            refs.append(ref)
    if len(refs) > MAX_REFS:
        raise CatalogError("too many references (max %d)" % MAX_REFS)
    return refs


def resolve(store, refs):
    cat = store.catalog()
    try:
        return cat.resolve_refs(refs)
    except KeyError as exc:
        raise CatalogError("unknown list: %s" % exc.args[0]) from exc
    except v2fly.RefError as exc:
        raise CatalogError("invalid reference") from exc


def cmd_preview(store, ref):
    refs = parse_refs(ref)
    if len(refs) != 1:
        raise CatalogError("preview takes a single reference")
    res = resolve(store, refs)
    platforms = shared.load()
    rules = []
    for kind, value, attrs in res.items():
        text = v2fly.rule_text(kind, value)
        refusal = shared.refused(kind, value, platforms)
        if refusal is None and "," in text:
            refusal = "comma"
        rules.append({
            "rule": text,
            "kind": kind,
            "value": value,
            "attrs": sorted(attrs),
            "discovery": kind in v2fly.DISCOVERY_KINDS,
            "shared": kind in (v2fly.KIND_DOMAIN, v2fly.KIND_FULL) and shared.touches(value, platforms),
            "refused": refusal,
        })
    name = refs[0].split("@", 1)[0]
    return {
        "ref": refs[0],
        "name": name,
        "suggested_name": suggested_name(name),
        "importable": res.importable,
        "broken": res.broken,
        "missing": sorted(res.missing),
        "unknown": res.unknown,
        "count": len(rules),
        "rules": rules,
        "revision": store.revision(),
        "max_import": MAX_IMPORT,
        "warn_import": WARN_IMPORT,
    }


def cmd_resolve(store, text):
    refs = parse_refs(text)
    res = resolve(store, refs)
    return {
        "refs": refs,
        "importable": res.importable,
        "broken": res.broken,
        "missing": sorted(res.missing),
        "count": len(res.rules),
        "rules": res.texts(),
        "revision": store.revision(),
    }


COMMANDS = {
    "info": (cmd_info, 0),
    "search": (cmd_search, 1),
    "preview": (cmd_preview, 1),
    "resolve": (cmd_resolve, 1),
    "popular": (cmd_popular, 0),
}


def run(argv, catalog_dir=CATALOG_DIR, cache_path=CACHE_PATH):
    """Renvoie le dictionnaire JSON de la commande (jamais d'exception)."""
    if not argv or argv[0] not in COMMANDS:
        return {"error": "usage: catalog.py info | search <q> | preview <ref> | resolve <ref,ref> | popular"}
    func, nargs = COMMANDS[argv[0]]
    args = list(argv[1:])
    if len(args) != nargs:
        return {"error": "%s: %d argument(s) expected" % (argv[0], nargs)}
    store = Store(catalog_dir, cache_path)
    try:
        return func(store, *args)
    except CatalogError as exc:
        return {"error": str(exc)}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    print(json.dumps(run(argv), ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
