#!/usr/local/bin/python3
"""État d'os-appalias au format JSON (action configd « appalias status »).

Sans argument : état du démon et, par application, nombre d'IP v4/v6 dans
la table pf, dernière correspondance (last_hit) et dernière erreur.

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
from lib.pftable import PfError, PfTable, valid_table  # noqa: E402
from lib.state import State  # noqa: E402


def daemon_running(pidfile=cfgmod.PIDFILE):
    try:
        with open(pidfile, "r", encoding="ascii") as fh:
            pid = int(fh.read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


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


def status(config_path, state_path, pf):
    out = {"status": "stopped", "pid": None, "apps": {}, "error": None}
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
