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

"""Lecture du cache d'Unbound pour amorcer les alias sans attendre de trafic.

Commande : unbound-control -c /var/unbound/unbound.conf dump_cache.
Source du chemin de conf : opnsense/core, src/opnsense/scripts/unbound/wrapper.py
(ligne 40), qui appelle '/usr/local/sbin/unbound-control', '-c',
'/var/unbound/unbound.conf', <action>.

Format de la sortie : START_RRSET_CACHE, lignes ";rrset <ttl> <n> ...",
enregistrements au format zone (nom TTL IN TYPE donnée), END_RRSET_CACHE,
START_MSG_CACHE, lignes "msg ...", END_MSG_CACHE, EOF. Seule la section RRSET
est exploitée, et seuls les types A et AAAA de classe IN sont retenus.

Exécution bornée : la sortie est lue par blocs et cumulée jusqu'à MAX_OUTPUT
octets (au-delà, le processus est tué et UnboundCacheError levée) ; le délai
TIMEOUT s'applique à l'ensemble de l'exécution (processus tué à l'expiration).
La sortie est décodée en UTF-8, octets invalides remplacés.
"""

import ipaddress
import subprocess
import threading
from types import SimpleNamespace

UNBOUND_CONTROL = "/usr/local/sbin/unbound-control"
UNBOUND_CONF = "/var/unbound/unbound.conf"
TIMEOUT = 20.0
MAX_OUTPUT = 64 * 1024 * 1024
MAX_RECORDS = 200000
MAX_TTL = 7 * 24 * 3600
CHUNK = 65536
MAX_STDERR = 65536


class UnboundCacheError(Exception):
    """Échec de l'exécution ou de la lecture de unbound-control."""


def _kill(proc):
    try:
        proc.kill()
    except OSError:
        pass


def _run(argv, timeout):
    """Exécute argv ; objet (returncode, stdout, stderr) en octets.

    stdout est lu par blocs de CHUNK octets et cumulé jusqu'à MAX_OUTPUT :
    au-delà, le processus est tué et UnboundCacheError levée. Le délai porte
    sur toute l'exécution : à l'expiration, le processus est tué et
    subprocess.TimeoutExpired levée. stderr est lu à part (MAX_STDERR octets
    retenus) pour ne jamais bloquer le processus.
    """
    proc = subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    expired = threading.Event()

    def on_timeout():
        # Le délai n'est déclaré que si le processus vit encore : un minuteur
        # parti juste après une fin normale ne compte pas (et le code de retour
        # d'un processus tué n'est pas portable : négatif sous FreeBSD, 1 sous Windows).
        if proc.poll() is None:
            expired.set()
            _kill(proc)

    timer = threading.Timer(timeout, on_timeout)
    timer.daemon = True
    err = []

    def read_stderr():
        size = 0
        try:
            for chunk in iter(lambda: proc.stderr.read(4096), b""):
                if size < MAX_STDERR:
                    err.append(chunk[:MAX_STDERR - size])
                    size += len(chunk)
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=read_stderr, daemon=True)
    timer.start()
    reader.start()
    chunks = []
    size = 0
    try:
        while True:
            chunk = proc.stdout.read1(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_OUTPUT:
                _kill(proc)
                raise UnboundCacheError("sortie de dump_cache trop volumineuse")
            chunks.append(chunk)
        returncode = proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            _kill(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        reader.join(1)
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass
    if expired.is_set():
        raise subprocess.TimeoutExpired(argv, timeout)
    return SimpleNamespace(returncode=returncode, stdout=b"".join(chunks), stderr=b"".join(err))


def _text(value):
    """Texte d'une sortie (octets décodés en UTF-8, octets invalides remplacés)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def collect(runner=None, conf=UNBOUND_CONF, control=UNBOUND_CONTROL, timeout=TIMEOUT):
    """Exécute dump_cache et renvoie la liste (nom, ip, ttl).

    runner(argv, timeout) -> objet (returncode, stdout, stderr), en octets ou
    en texte ; remplaçable par les tests (défaut : _run, lecture bornée).
    """
    argv = [control, "-c", conf, "dump_cache"]
    try:
        res = (runner or _run)(argv, timeout)
    except subprocess.TimeoutExpired as exc:
        raise UnboundCacheError("délai dépassé pour unbound-control") from exc
    except OSError as exc:
        raise UnboundCacheError("exécution de unbound-control impossible : %s" % exc) from exc
    if res.returncode != 0:
        err = _text(res.stderr).strip()[:200]
        raise UnboundCacheError("unbound-control a échoué (code %s) : %s" % (res.returncode, err))
    out = res.stdout or b""
    if len(out) > MAX_OUTPUT:
        raise UnboundCacheError("sortie de dump_cache trop volumineuse")
    return parse(_text(out))


def parse(text):
    """Extrait les (nom, ip, ttl) A/AAAA de la section RRSET. Ne lève jamais."""
    if not text:
        return []
    found = {}
    in_rrset = False
    try:
        for line in str(text).splitlines():
            line = line.strip()
            if not line:
                continue
            if line == "START_RRSET_CACHE":
                in_rrset = True
                continue
            if line == "END_RRSET_CACHE":
                in_rrset = False
                continue
            if not in_rrset or line.startswith(";"):
                continue
            parts = line.split()
            if len(parts) < 5 or parts[2].upper() != "IN":
                continue
            rtype = parts[3].upper()
            if rtype not in ("A", "AAAA"):
                continue
            try:
                ttl = int(parts[1])
                ip = ipaddress.ip_address(parts[4])
            except ValueError:
                continue
            if ttl < 0 or (rtype == "A") != (ip.version == 4):
                continue
            name = parts[0].lower().rstrip(".")
            if not name:
                continue
            ttl = min(ttl, MAX_TTL)
            key = (name, str(ip))
            if key not in found:
                if len(found) >= MAX_RECORDS:
                    continue
                found[key] = ttl
            elif ttl > found[key]:
                found[key] = ttl
    except Exception:  # noqa: BLE001 - parse ne doit jamais lever
        return []
    return [(n, i, t) for (n, i), t in found.items()]
