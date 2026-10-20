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

"""Mise à jour du cache des préfixes BGP des ASN des applications.

  asn_update.py update [--if-needed] [--config PATH] [--cache PATH] [--no-reload]

Sans --if-needed, tous les ASN des applications sont rafraîchis ; avec,
seulement ceux absents du cache ou périmés (lib/asn.is_stale). Si les
préfixes ont changé, le démon est prévenu (configctl appalias reload),
sauf avec --no-reload. Plugin désactivé ou sources ASN désactivées : aucune
connexion, sortie {"status": "disabled"}. Sortie : un JSON sur stdout ;
code 0 même avec des erreurs par ASN (dans « errors »), 1 seulement si la
configuration est illisible. Un échec garde les anciens préfixes et l'heure
du dernier succès (« fetched ») et note la tentative (« attempted »).
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import asn  # noqa: E402
from lib import config as cfgmod  # noqa: E402

LOCK_NAME = "asn_update.lock"
LOCK_PATH = "/var/db/appalias/" + LOCK_NAME
CONFIGCTL = ["/usr/local/sbin/configctl", "appalias", "reload"]
PAUSE = 0.5
RELOAD_TIMEOUT = 30


def log_error(message):
    try:
        import syslog
        syslog.openlog("appalias", syslog.LOG_PID, syslog.LOG_DAEMON)
        syslog.syslog(syslog.LOG_ERR, "asn update: %s" % message)
    except ImportError:
        print("asn update: %s" % message, file=sys.stderr)


class Busy(Exception):
    """Une autre exécution est en cours."""


class Lock:
    """Verrou exclusif non bloquant (flock, sinon fichier créé en O_EXCL)."""

    def __init__(self, path=LOCK_PATH):
        self.path = path
        self.fd = None
        self.excl = False

    def __enter__(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        try:
            import fcntl
        except ImportError:
            fcntl = None
        if fcntl is not None:
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(self.fd)
                self.fd = None
                raise Busy() from exc
        else:
            try:
                self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                self.excl = True
            except FileExistsError as exc:
                raise Busy() from exc
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.excl:
            try:
                os.unlink(self.path)
            except OSError:
                pass
        return False


def _prefix_map(cache):
    return {k: v["prefixes"] for k, v in cache["asns"].items()}


def _summary(status, cache, fetched, errors):
    return {"status": status, "asns": len(cache["asns"]),
            "prefixes": sum(len(v["prefixes"]) for v in cache["asns"].values()),
            "fetched": fetched, "errors": errors}


def reload_daemon(command=None, run=subprocess.run):
    """Prévient le démon ; best effort, jamais fatal."""
    command = CONFIGCTL if command is None else command
    try:
        run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=RELOAD_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        log_error("rechargement du démon impossible : %s" % exc)


def run(config_path, cache_path, if_needed=False, reload=True, opener=None, sleep=time.sleep,
        now=None, pause=None, lock_path=None, configctl=None):
    """Mise à jour complète ; renvoie le dict de sortie (ConfigError si config illisible)."""
    cfg = cfgmod.load(config_path)
    if not cfg.enabled or not getattr(cfg, "asn_enabled", True):
        return {"status": "disabled"}   # aucune connexion à RIPEstat
    lock_path = lock_path or os.path.join(os.path.dirname(os.path.abspath(cache_path)), LOCK_NAME)
    pause = PAUSE if pause is None else pause
    try:
        with Lock(lock_path):
            now = int(time.time() if now is None else now)
            old = asn.load_cache(cache_path)
            needed = sorted(asn.needed_asns(cfg))
            todo = [n for n in needed
                    if not if_needed or str(n) not in old["asns"] or asn.is_stale(old["asns"][str(n)], now)]
            new = {"version": asn.CACHE_VERSION, "asns": {}}
            for n in needed:
                if str(n) in old["asns"]:
                    new["asns"][str(n)] = dict(old["asns"][str(n)])
            errors = {}
            fetched = 0
            if todo and opener is None:
                opener = asn.make_opener()
            for index, n in enumerate(todo):
                if index:
                    sleep(pause)
                key = str(n)
                try:
                    prefixes = asn.fetch(n, opener)
                    new["asns"][key] = {"prefixes": prefixes, "fetched": now, "error": None}
                    fetched += 1
                except asn.AsnError as exc:
                    previous = new["asns"].get(key, {})
                    new["asns"][key] = {"prefixes": previous.get("prefixes", []),
                                        "fetched": previous.get("fetched"),
                                        "attempted": now, "error": str(exc)}
                    errors[key] = str(exc)
                    log_error("AS%s : %s" % (key, exc))
            changed = _prefix_map(new) != _prefix_map(old)
            if changed or new != old:
                asn.save_cache(new, cache_path)
            if changed and reload:
                reload_daemon(configctl)
            return _summary("updated" if changed else "unchanged", new, fetched, errors)
    except Busy:
        return {"status": "busy"}


def main(argv=None, opener=None, sleep=time.sleep, **kwargs):
    parser = argparse.ArgumentParser(prog="asn_update.py")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("update")
    up.add_argument("--if-needed", action="store_true")
    up.add_argument("--config", default=cfgmod.CONFIG_PATH)
    up.add_argument("--cache", default=asn.CACHE_PATH)
    up.add_argument("--no-reload", action="store_true")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        out = run(args.config, args.cache, if_needed=args.if_needed, reload=not args.no_reload,
                  opener=opener, sleep=sleep, **kwargs)
    except cfgmod.ConfigError as exc:
        log_error("configuration illisible : %s" % exc)
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=True, separators=(",", ":")))
        return 1
    print(json.dumps(out, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
