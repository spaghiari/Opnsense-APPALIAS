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

"""Appels à pfctl pour les tables des alias gérés.

Sécurité :
- aucun shell : subprocess.run([...], shell=False) avec un timeout (3 s) ;
- noms de tables validés (^[A-Za-z0-9_]{1,31}$) avant chaque appel ;
- entrées validées par ipaddress avant d'être passées en argument.

Le chemin de pfctl vient de la variable d'environnement APPALIAS_PFCTL
(utile pour les tests), sinon /sbin/pfctl. Un « runner » injectable remplace
subprocess.run dans les tests.
"""

import ipaddress
import os
import re
import shlex
import subprocess
import tempfile

DEFAULT_PFCTL = "/sbin/pfctl"
TABLE_RE = re.compile(r"^[A-Za-z0-9_]{1,31}$")


class PfError(Exception):
    """Échec d'un appel à pfctl."""


def valid_table(name):
    return isinstance(name, str) and bool(TABLE_RE.match(name))


def check_table(name):
    if not valid_table(name):
        raise PfError("nom de table invalide : %r" % (name,))
    return name


def normalize_entry(entry):
    """Adresse ou réseau sous forme canonique (192.0.2.1, 192.0.2.0/24)."""
    net = ipaddress.ip_network(str(entry).strip(), strict=False)
    if net.prefixlen == net.max_prefixlen:
        return str(net.network_address)
    return str(net)


def pfctl_argv():
    """Commande pfctl de base, éventuellement surchargée par APPALIAS_PFCTL."""
    env = os.environ.get("APPALIAS_PFCTL", "").strip()
    if not env:
        return [DEFAULT_PFCTL]
    if os.path.isfile(env):
        return [env]
    return shlex.split(env)


class PfTable:
    name = "pfctl"

    def __init__(self, pfctl=None, runner=None, timeout=3.0, tmpdir=None):
        self.argv = list(pfctl) if pfctl else pfctl_argv()
        self.runner = runner
        self.timeout = timeout
        self.tmpdir = tmpdir

    def _run(self, args):
        argv = self.argv + list(args)
        if self.runner is not None:
            res = self.runner(argv, self.timeout)
        else:
            try:
                res = subprocess.run(argv, shell=False, capture_output=True,
                                     text=True, timeout=self.timeout, check=False)
            except subprocess.TimeoutExpired as exc:
                raise PfError("pfctl : délai dépassé (%ss)" % self.timeout) from exc
            except OSError as exc:
                raise PfError("pfctl introuvable ou non exécutable : %s" % exc) from exc
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()
            raise PfError("pfctl %s : code %s : %s" % (" ".join(args[:4]), res.returncode, err))
        return res.stdout or ""

    def add(self, table, entries):
        """Un seul appel « -T add » pour toutes les entrées."""
        check_table(table)
        items = [normalize_entry(e) for e in entries]
        if not items:
            return
        self._run(["-t", table, "-T", "add"] + items)

    def delete(self, table, entries):
        check_table(table)
        items = [normalize_entry(e) for e in entries]
        if not items:
            return
        self._run(["-t", table, "-T", "delete"] + items)

    def flush(self, table):
        check_table(table)
        self._run(["-t", table, "-T", "flush"])

    def replace(self, table, entries):
        """Remplace le contenu de la table via un fichier (-T replace -f)."""
        check_table(table)
        items = [normalize_entry(e) for e in entries]
        fd, path = tempfile.mkstemp(prefix=".appalias-%s-" % table, suffix=".txt",
                                    dir=self.tmpdir)
        try:
            with os.fdopen(fd, "w", encoding="ascii", newline="\n") as fh:
                for item in items:
                    fh.write(item + "\n")
            self._run(["-t", table, "-T", "replace", "-f", path])
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def show(self, table):
        check_table(table)
        out = self._run(["-t", table, "-T", "show"])
        return [line.strip() for line in out.splitlines() if line.strip()]


def _tag(backend, reason=None):
    """Pose backend.fallback_reason (None, ou cause du repli sur pfctl) si possible."""
    try:
        backend.fallback_reason = reason
    except AttributeError:
        pass  # objet sans attribut modifiable (fabrique injectée par un test)
    return backend


def make_backend(mode="auto", tmpdir=None, log=None, ioctl_factory=None):
    """Choisit le backend : « pfctl », « ioctl » ou « auto » (ioctl, repli pfctl).

    En « ioctl » et « auto », le backend ioctl doit passer probe() puis
    selftest() ; en « auto », un échec replie sur pfctl.

    L'objet rendu porte fallback_reason : None, ou le texte de l'exception
    quand « auto » a dû replier sur pfctl.
    """
    if mode not in ("auto", "pfctl", "ioctl"):
        raise ValueError("mode de backend pf inconnu : %r" % (mode,))
    if mode == "pfctl":
        return _tag(PfTable(tmpdir=tmpdir))

    def build():
        if ioctl_factory is not None:
            backend = ioctl_factory()
        else:
            from .pfioctl import PfIoctl  # import paresseux (ctypes, /dev/pf)
            backend = PfIoctl()
        try:
            # /dev/pf joignable, puis aller-retour réel sur une table de test.
            for check in ("probe", "selftest"):
                func = getattr(backend, check, None)
                if func is not None:
                    func()
        except Exception:
            close = getattr(backend, "close", None)
            if close is not None:
                close()
            raise
        return backend

    if mode == "ioctl":
        return _tag(build())
    try:
        return _tag(build())
    except (PfError, OSError, ImportError) as exc:
        if log is None:
            import logging
            log = logging.getLogger("appalias")
        log.warning("backend pf ioctl indisponible (%s), repli sur pfctl", exc)
        return _tag(PfTable(tmpdir=tmpdir), str(exc) or exc.__class__.__name__)
