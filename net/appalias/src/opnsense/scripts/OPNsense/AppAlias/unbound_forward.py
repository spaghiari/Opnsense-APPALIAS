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

"""Branchement automatique d'Unbound (actions configd « appalias unbound »).

Sans argument : calcule les zones (lib/unboundfwd.py) à partir de
config.json et écrit /usr/local/etc/unbound.opnsense.d/appalias.conf (copié
aussi dans /var/unbound/etc/). Mode désactivé ou plugin désactivé : fichiers
supprimés. Jamais de redémarrage : usage au démarrage du système et sur les
événements « dns » / « unbound_start » (Unbound démarre de toute façon, et
start.sh recopie le fichier).

« --apply » (action « appalias unbound », bouton « Apply ») : idem, puis
« configctl unbound restart » si le contenu du fichier a changé et
qu'Unbound est activé, ou si un redémarrage précédent a échoué
(« restart_pending »). Code de sortie 1 si l'écriture ou le redémarrage
échoue (l'erreur est journalisée et affichée dans Settings).

« --status » : état enregistré, en JSON (action « appalias unbound_status »).
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import config as cfgmod  # noqa: E402
from lib import unboundfwd as ub  # noqa: E402

try:
    import syslog
except ImportError:  # hors FreeBSD (tests)
    syslog = None


def log(error, message):
    if syslog is not None:
        syslog.openlog("appalias", syslog.LOG_PID, syslog.LOG_DAEMON)
        syslog.syslog(syslog.LOG_ERR if error else syslog.LOG_NOTICE, "unbound: " + message)


def read_status(path=ub.STATUS_PATH):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(path, status):
    try:
        ub.write_atomic(path, json.dumps(status, sort_keys=True) + "\n")
    except OSError:
        pass


def sync(config_path=cfgmod.CONFIG_PATH, conf_path=ub.CONF_PATH, runtime_path=ub.RUNTIME_PATH,
         status_path=ub.STATUS_PATH, apply=False, restarter=None, platforms=None, clock=time.time):
    """Renvoie (code de sortie, état)."""
    status = {"time": int(clock()), "active": False, "zones": [], "count": 0, "conflicts": [],
              "errors": [], "changed": False, "restarted": False, "unbound_enabled": False,
              "restart_pending": bool(read_status(status_path).get("restart_pending")),
              "root_forward": False}
    try:
        cfg = cfgmod.load(config_path)
    except cfgmod.ConfigError as exc:
        status["errors"].append(str(exc))
        log(True, str(exc))
        write_status(status_path, status)
        return 1, status

    zones, conflicts, active = ub.plan(cfg, platforms)
    for item in conflicts:
        item["text"] = ub.REASONS.get(item["reason"], item["reason"])
    text = ub.render(zones, cfg.listen_address, cfg.listen_port)
    old = ub.read_text(conf_path)
    unbound_enabled = bool((cfg.unbound or {}).get("enabled"))
    status.update({"active": active, "zones": ub.parse_zones(text), "count": len(ub.parse_zones(text)),
                   "conflicts": conflicts, "forward_addr": ub.forward_addr(cfg),
                   "changed": text != old, "unbound_enabled": unbound_enabled,
                   "root_forward": ub.root_forward_active(cfg.unbound),
                   # amont du relais, pour l'encadré d'état de Settings
                   "upstream_protocol": cfg.upstream_protocol,
                   "upstream_tls_name": cfg.upstream_tls_name or "",
                   "upstream_port": cfg.upstreams[0].port if cfg.upstreams else None})
    if any(c["reason"] == "loop" for c in conflicts):
        status["errors"].append(ub.REASONS["loop"] + ": no zone forwarded")
        log(True, status["errors"][-1])

    try:
        if text:
            ub.write_atomic(conf_path, text)
            if os.path.isdir(os.path.dirname(runtime_path)):
                ub.write_atomic(runtime_path, text)
        else:
            ub.remove_file(conf_path)
            ub.remove_file(runtime_path)
    except OSError as exc:
        status["errors"].append("cannot write %s: %s" % (conf_path, exc))
        log(True, status["errors"][-1])
        write_status(status_path, status)
        return 1, status

    code = 0
    if apply and (status["changed"] or status["restart_pending"]) and unbound_enabled:
        ok, output = (restarter or ub.restart_unbound)()
        status["restarted"] = ok
        # Échec : le prochain --apply relance le redémarrage, même sans changement.
        status["restart_pending"] = not ok
        if ok:
            log(False, "%d zone(s) forwarded, Unbound restarted" % status["count"])
        else:
            status["errors"].append("configctl unbound restart failed: %s" % output)
            log(True, status["errors"][-1])
            code = 1
    write_status(status_path, status)
    return code, status


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--status"]:
        print(json.dumps(read_status(), ensure_ascii=True))
        return 0
    if argv not in ([], ["--apply"]):
        print("usage: unbound_forward.py [--apply | --status]", file=sys.stderr)
        return 64
    code, _status = sync(apply=argv == ["--apply"])
    if code == 0:
        print("OK")
    return code


if __name__ == "__main__":
    sys.exit(main())
