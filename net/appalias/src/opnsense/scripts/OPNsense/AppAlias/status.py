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

"""État d'os-appalias au format JSON (action configd « appalias status »).

Sans argument : état du démon et, par application, nombre d'IP v4/v6 dans
la table pf, dernière correspondance (last_hit) et dernière erreur ;
« latency » : mesures du démon (metrics.json, à côté de l'état), temps de
l'amont par amont et de pf, nombre, moyenne et p95 sur les 256 dernières
mesures. Le démon les écrit toutes les 15 s au plus : léger décalage possible.

Version : « version » est celle du code en cours d'exécution (écrite par le
démon dans metrics.json au démarrage, retenue seulement si le PID correspond
au processus en cours), « installed_version » celle des fichiers installés ;
« restart_needed » est vrai quand elles diffèrent, null si inconnu.

« --show <table> » : contenu d'une table gérée (action « appalias table_show »).
Le nom est validé et doit appartenir à une application configurée.
"""

import argparse
import ipaddress
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import config as cfgmod  # noqa: E402
from lib import metrics  # noqa: E402
from lib.pftable import PfError, PfTable, valid_table  # noqa: E402
from lib.state import State  # noqa: E402
from lib.version import VERSION  # noqa: E402


def daemon_running(pidfile=cfgmod.PIDFILE):
    try:
        with open(pidfile, "r", encoding="ascii") as fh:
            pid = int(fh.read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def child_pid(pidfile=cfgmod.CHILD_PIDFILE):
    try:
        with open(pidfile, "r", encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def running_version(latency, pid):
    """Version écrite par le démon en cours d'exécution (PID vérifié), ou None."""
    info = latency.get("daemon") if isinstance(latency, dict) else None
    if not isinstance(info, dict) or pid is None or info.get("pid") != pid:
        return None
    version = info.get("version")
    return str(version) if isinstance(version, str) and len(version) <= 32 else None


def count_families(entries):
    v4 = v6 = 0
    for entry in entries:
        try:
            net = ipaddress.ip_network(entry.split()[0], strict=False)
        except (ValueError, IndexError):
            continue
        if net.version == 4:
            v4 += 1
        else:
            v6 += 1
    return v4, v6


def status(config_path, state_path, pf, child_pidfile=cfgmod.CHILD_PIDFILE):
    out = {"status": "stopped", "pid": None, "apps": {}, "error": None, "latency": {},
           "version": None, "installed_version": VERSION, "restart_needed": None}
    try:
        cfg = cfgmod.load(config_path)
    except cfgmod.ConfigError as exc:
        out["error"] = str(exc)
        return out
    pid = daemon_running()
    if pid:
        out["status"] = "running"
        out["pid"] = pid
    elif not cfg.enabled:
        out["status"] = "disabled"
    state = State.load(state_path)
    out["latency"] = metrics.read(metrics.path_for(state_path))
    if pid:
        out["version"] = running_version(out["latency"], child_pid(child_pidfile))
        if out["version"] is not None:
            out["restart_needed"] = out["version"] != VERSION
    for app in cfg.apps:
        item = {
            "uuid": app.uuid,
            "table": app.table,
            "enabled": app.enabled,
            "v4": 0,
            "v6": 0,
            "last_hit": state.last_hit.get(app.table),
            "last_error": state.last_error.get(app.table),
        }
        try:
            item["v4"], item["v6"] = count_families(pf.show(app.table))
        except PfError as exc:
            item["pf_error"] = str(exc)
        out["apps"][app.uuid or app.table] = item
    return out


def show(table, config_path, pf):
    if not valid_table(table):
        return {"error": "nom de table invalide"}
    try:
        cfg = cfgmod.load(config_path)
    except cfgmod.ConfigError as exc:
        return {"error": str(exc)}
    if table not in {a.table for a in cfg.apps}:
        return {"error": "table non gérée par os-appalias"}
    try:
        return {"table": table, "entries": pf.show(table)}
    except PfError as exc:
        return {"table": table, "error": str(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="État d'os-appalias")
    parser.add_argument("--show", metavar="TABLE")
    parser.add_argument("--config", default=cfgmod.CONFIG_PATH)
    parser.add_argument("--state", default=cfgmod.STATE_PATH)
    args = parser.parse_args(argv)
    pf = PfTable()
    if args.show is not None:
        result = show(args.show.strip(), args.config, pf)
    else:
        result = status(args.config, args.state, pf)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
