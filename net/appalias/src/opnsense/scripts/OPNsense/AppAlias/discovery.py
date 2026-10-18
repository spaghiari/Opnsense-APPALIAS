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

"""Découverte : propositions et commandes (actions configd, hors démon).

  discovery.py list          état, propositions, noms non attribués (JSON)
  discovery.py ctl <cmd>     dump | purge | stop_learn | learn:<s>[:<uuid>]

« list » lit discovery.json (écrit par le démon), config.json et l'index du
catalogue v2fly ; rien n'est modifié. « ctl » écrit discovery.ctl.json
(écriture atomique) puis demande au démon de le lire (rc.d appaliasd ctl,
SIGUSR1) ; « purge » efface aussi discovery.json directement, que le démon
tourne ou non. Les arguments viennent de configd et sont validés ici.
"""

import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import config as cfgmod  # noqa: E402
from lib import discovery as discmod  # noqa: E402
from lib import score  # noqa: E402
from lib import shared  # noqa: E402

RC_SCRIPT = "/usr/local/etc/rc.d/appaliasd"
CTL_RE = re.compile(
    r"^(dump|purge|stop_learn|learn:([0-9]{1,3})(:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}))?)$")


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def load_data(path):
    """discovery.json nettoyé (relu par le moteur), ou None."""
    raw = read_json(path)
    if raw is None:
        return None
    engine = discmod.Engine(lambda name: set(), path=path)
    if not engine.load_dict(raw):
        return None
    data = engine.to_dict(now=raw.get("saved") if isinstance(raw.get("saved"), (int, float)) else None)
    # valeurs du démon au moment de l'écriture (to_dict recalcule les compteurs locaux)
    for key in ("dropped", "queued", "saved"):
        if isinstance(raw.get(key), (int, float)) and not isinstance(raw.get(key), bool):
            data[key] = raw[key]
    raw_learn = raw.get("learn")
    if data.get("learn") is not None and isinstance(raw_learn, dict):
        data["learn"]["active"] = raw_learn.get("active") is True
    return data


def catalog_rules(app, store):
    """{ref: [règles]} des listes importées dans l'application."""
    out = {}
    if store is None:
        return out
    for ref in app.catalog_refs:
        try:
            res = store.catalog().resolve_refs([ref])
        except Exception:  # liste disparue, référence invalide, catalogue illisible
            continue
        if res.importable:
            out[ref] = res.texts()
    return out


def catalog_store():
    try:
        import catalog as catmod
        store = catmod.open_store()
        return store, store.index().get("domains", {})
    except Exception:
        return None, {}


def cmd_list(config_path, data_path, now=None):
    now = time.time() if now is None else now
    out = {"enabled": False, "data": False, "saved": None, "observed": 0, "uncovered": 0,
           "dropped": 0, "queued": 0, "retention_days": cfgmod.DISCOVERY_RETENTION[0],
           "window": cfgmod.DISCOVERY_WINDOW[0], "proposals": [], "unassigned": [],
           "learn": None, "counts": {}, "covered": 0, "regex_timeout": False, "error": None}
    try:
        cfg = cfgmod.load(config_path)
    except cfgmod.ConfigError as exc:
        out["error"] = str(exc)
        return out
    out["enabled"] = cfg.discovery_enabled
    out["retention_days"] = cfg.discovery_retention_days
    out["window"] = cfg.discovery_window
    if not cfg.discovery_enabled:
        return out
    data = load_data(data_path)
    if data is None:
        return out
    out["data"] = True
    out["saved"] = data.get("saved")
    out["dropped"] = data.get("dropped", 0)
    out["queued"] = data.get("queued", 0)
    out["uncovered"] = len(data["names"])
    out["observed"] = len(data["names"]) + len(data["covered"])
    platforms = shared.load(extra=cfg.shared_platforms_extra)
    suffixes = score.Suffixes.load()
    store, domains_index = catalog_store()
    apps = [score.AppRules(app, platforms, suffixes, catalog_rules(app, store))
            for app in cfg.apps if app.uuid]
    result = score.build(data, apps, platforms, suffixes, domains_index,
                         ignored=discmod.Ignored(cfg.discovery_ignored), now=now,
                         follow_cname=cfg.follow_cname)
    out.update(result)
    # « already covered » (adresses déjà ajoutées par la chaîne CNAME) : hors compteurs
    out["counts"] = score.count_useful(out["proposals"])
    out["covered"] = sum(1 for item in out["proposals"] if item.get("covered"))
    learn = data.get("learn")
    if learn is not None:
        out["learn"] = {
            "active": bool(learn.get("active")) and learn["until"] > now,
            "start": learn["start"],
            "until": learn["until"],
            "remaining": max(0, int(learn["until"] - now)) if learn.get("active") else 0,
            "app_uuid": learn.get("app_uuid"),
            "events": learn.get("events", 0),
            "stopped": learn.get("stopped"),
            "names": sorted(learn["names"]),
            "new": sorted(n for n, v in learn["names"].items() if v.get("new")),
        }
    return out


def parse_ctl_arg(arg, now):
    """Argument configd -> commande pour discovery.ctl.json, ou ValueError."""
    match = CTL_RE.match(str(arg or "").strip())
    if not match:
        raise ValueError("usage: ctl dump | purge | stop_learn | learn:<seconds>[:<uuid>]")
    if match.group(2) is None:
        return {"cmd": match.group(1)}
    seconds = int(match.group(2))
    if not 1 <= seconds <= discmod.LEARN_MAX_SECONDS:
        raise ValueError("learn: 1 to %d seconds" % discmod.LEARN_MAX_SECONDS)
    cmd = {"cmd": "learn", "until": now + seconds}
    if match.group(4):
        cmd["app_uuid"] = match.group(4)
    return cmd


def cmd_ctl(arg, data_path, run=subprocess.run, now=None):
    now = time.time() if now is None else now
    cmd = parse_ctl_arg(arg, now)
    discmod.parse_ctl(json.dumps(cmd), now)      # même validation que le démon
    if cmd["cmd"] == "purge":
        discmod.remove_file(data_path)
    discmod.write_json(discmod.ctl_path_for(data_path), cmd)
    res = run([RC_SCRIPT, "onectl"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              timeout=10, check=False)
    if res.returncode != 0:
        # démon arrêté : la commande n'a pas d'objet, sauf purge (déjà faite)
        discmod.remove_file(discmod.ctl_path_for(data_path))
        return cmd["cmd"] == "purge"
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["list"] and len(argv) == 1:
        print(json.dumps(cmd_list(cfgmod.CONFIG_PATH, discmod.DISCOVERY_PATH),
                         ensure_ascii=True, separators=(",", ":")))
        return 0
    if argv[:1] == ["ctl"] and len(argv) == 2:
        try:
            ok = cmd_ctl(argv[1], discmod.DISCOVERY_PATH)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        return 0 if ok else 1
    print("usage: discovery.py list | ctl <dump|purge|stop_learn|learn:<s>[:<uuid>]>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
