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

"""Suivi automatique du catalogue : calculs et journal (lib/follow.py).

  catalog_follow.py plan        stdin {apps, shared_extra, cap} :
                                écarts de chaque application suivie
  catalog_follow.py undo-plan   stdin {entry_id, apps: {uuid: état}} :
                                inverse d'une entrée du journal
  catalog_follow.py record      stdin {entries} : ajout au journal (ids)
  catalog_follow.py log [n]     dernières entrées du journal (20 par défaut)

Appelé par catalog_follow.php (plan, undo-plan, record), qui garde le verrou
de config.xml et enregistre le modèle, et par configd (log). Toujours un
JSON sur stdout, jamais de trace ; une erreur = {"error": ...}.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import catalog as catmod  # noqa: E402
from lib import follow  # noqa: E402
from lib import shared  # noqa: E402
from lib import v2fly  # noqa: E402

MAX_INPUT = 32 * 1024 * 1024
MAX_APPS = 1000


def _apps(value):
    if isinstance(value, list) and not value:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_APPS:
        raise follow.FollowError("apps expected")
    return value


def cmd_plan(data, store):
    """Écarts de chaque application ; une erreur de résolution ne touche que son application."""
    apps = data.get("apps")
    if not isinstance(apps, list) or len(apps) > MAX_APPS:
        raise follow.FollowError("apps expected")
    cap = data.get("cap", follow.MAX_APP_RULES)
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        raise follow.FollowError("invalid cap")
    platforms = shared.load(extra=follow.rule_list(data.get("shared_extra")))
    try:
        cat = store.catalog()
        revision = store.revision()
    except catmod.CatalogError as exc:
        return {"error": str(exc)}
    out = {"revision": revision, "origin": store.origin, "apps": {}}
    for app in apps:
        if not isinstance(app, dict) or not follow.UUID_RE.match(str(app.get("uuid") or "")):
            raise follow.FollowError("invalid application")
        uuid = app["uuid"]
        try:
            refs = catmod.parse_refs(",".join(follow.rule_list(app.get("refs"))))
        except catmod.CatalogError as exc:
            out["apps"][uuid] = {"error": str(exc)}
            continue
        try:
            res = cat.resolve_refs(refs)
        except KeyError as exc:
            out["apps"][uuid] = {"error": "unknown list: %s" % exc.args[0]}
            continue
        except v2fly.RefError:
            out["apps"][uuid] = {"error": "invalid reference"}
            continue
        if not res.importable:
            out["apps"][uuid] = {"error": "broken list: %s" % res.broken}
            continue
        if res.missing:
            # include: vers une liste absente : un retrait serait faux, rien n'est appliqué
            out["apps"][uuid] = {"error": "catalog list incomplete: %s" % ", ".join(sorted(res.missing)[:5])}
            continue
        out["apps"][uuid] = follow.plan_app(
            res.items(),
            follow.rule_list(app.get("catalog_rules")),
            follow.rule_list(app.get("domains")),
            follow.rule_list(app.get("declined")),
            platforms, cap=cap)
    return out


def cmd_undo_plan(data, journal_path):
    entry = follow.find_entry(data.get("entry_id"), journal_path)
    apps = _apps(data.get("apps"))
    state = apps.get(entry["app_uuid"])
    if not isinstance(state, dict):
        raise follow.FollowError("the application %s was deleted: nothing to undo" % (entry["app"] or "?"))
    cap = data.get("cap", follow.MAX_APP_RULES)
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        raise follow.FollowError("invalid cap")
    plan = follow.plan_undo(entry,
                            follow.rule_list(state.get("domains")),
                            follow.rule_list(state.get("catalog_rules")),
                            follow.rule_list(state.get("declined")), cap,
                            follow.load_journal(journal_path))
    plan["entry"] = entry
    return plan


def run(argv, stdin_text="", store=None, journal_path=follow.JOURNAL_PATH):
    """Résultat JSON de la commande (jamais d'exception)."""
    try:
        if argv[:1] == ["log"] and len(argv) <= 2:
            limit = follow.LOG_DEFAULT
            if len(argv) == 2:
                if not argv[1].isdigit() or not 1 <= int(argv[1]) <= follow.JOURNAL_MAX:
                    raise follow.FollowError("log: 1 to %d entries" % follow.JOURNAL_MAX)
                limit = int(argv[1])
            return follow.recent(journal_path, limit)
        if len(argv) != 1 or argv[0] not in ("plan", "undo-plan", "record"):
            return {"error": "usage: catalog_follow.py plan | undo-plan | record | log [n]"}
        if len(stdin_text) > MAX_INPUT:
            raise follow.FollowError("input too large")
        data = json.loads(stdin_text or "{}")
        if not isinstance(data, dict):
            raise follow.FollowError("JSON object expected")
        if argv[0] == "plan":
            return cmd_plan(data, store if store is not None else catmod.open_store())
        if argv[0] == "undo-plan":
            return cmd_undo_plan(data, journal_path)
        return {"ids": follow.record(data.get("entries"), journal_path)}
    except (follow.FollowError, ValueError, OSError, TypeError, AttributeError) as exc:
        return {"error": str(exc) or type(exc).__name__}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    text = "" if argv[:1] == ["log"] else sys.stdin.read(MAX_INPUT + 1)
    print(json.dumps(run(argv, text), ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
