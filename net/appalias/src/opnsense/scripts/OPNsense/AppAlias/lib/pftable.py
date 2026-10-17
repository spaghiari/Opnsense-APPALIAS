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
