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

"""Mise à jour en ligne du catalogue v2fly (option, activée par défaut, quotidienne).

  catalog_update.py update   télécharge le dernier commit et remplace le
                             catalogue téléchargé s'il passe les contrôles
  catalog_update.py update --if-enabled
                             idem, seulement si general.catalog_online_update
                             de config.json est actif (absent = actif, valeur
                             par défaut) : appel de +POST_INSTALL
  catalog_update.py reset    supprime le catalogue téléchargé (retour à
                             l'instantané embarqué)

Réseau : HTTPS seulement, vers api.github.com (dernier commit de la branche
master) et codeload.github.com (archive du commit) ; aucun autre hôte, aucune
redirection vers un autre hôte. Contexte TLS de lib/dot.py (CA du système
OPNsense, repli sur les CA par défaut de Python), délai TIMEOUT, archive
plafonnée à MAX_ARCHIVE octets pendant la lecture.

Moindres privilèges : l'archive est une donnée non fiable. Le processus root
ne fait que la télécharger dans un dossier temporaire 0700 appartenant à
« nobody » ; l'extraction, la génération (lib/v2fly_snapshot.py, le même code
que tools/v2fly-snapshot.py) et le contrôle de cohérence se font dans un
sous-processus lancé sous « nobody » (setgid, setuid et groupes
supplémentaires retirés par subprocess avant l'exécution, Python en -I). Le
sous-processus rend tout son résultat sur stdout, en un seul JSON (.lst en
base64, licence, manifeste) plafonné à MAX_CHILD_OUTPUT : le processus root
ne lit AUCUN fichier du dossier de nobody (ni lien symbolique, ni FIFO à
craindre), valide types, tailles, empreintes et licence, puis installe de
façon atomique dans ONLINE_DIR (empreinte de la licence ajoutée au
manifeste).

Contrôle de cohérence avant remplacement : au moins MIN_LISTS listes, tous
les noms de popular.json présents, aucune liste cassée. En cas d'échec, le
catalogue précédent est gardé et l'erreur journalisée. Un verrou empêche deux
exécutions simultanées. Rien n'est appliqué aux applications : les
nouveautés apparaissent dans les « catalog updates » à valider.
"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import catalog as catmod  # noqa: E402
from lib import dot  # noqa: E402
from lib import v2fly  # noqa: E402
from lib import v2fly_snapshot as snap  # noqa: E402

API_URL = "https://api.github.com/repos/v2fly/domain-list-community/commits/master"
ARCHIVE_URL = snap.CODELOAD_URL
ALLOWED_HOSTS = frozenset(("github.com", "api.github.com", "codeload.github.com"))
MAX_ARCHIVE = 20 * 1024 * 1024
MAX_API = 1024 * 1024
TIMEOUT = 60
CHILD_TIMEOUT = 300
MIN_LISTS = 500
UNPRIVILEGED = "nobody"
LOCK_PATH = "/var/db/appalias/catalog_update.lock"
FILES = (catmod.LST_NAME, catmod.MANIFEST_NAME, catmod.LICENSE_NAME)
MAX_LICENSE = 64 * 1024
# .lst en base64 (4/3) + licence, manifeste et enveloppe JSON : 128 Ko.
MAX_CHILD_OUTPUT = (snap.DEFAULT_MAX_LST * 4) // 3 + 4 + 128 * 1024
LICENSE_HEAD = ("The domain lists in v2fly.lst are a snapshot of\n"
                "v2fly/domain-list-community (" + snap.SOURCE + "), commit %s.\n")
MIT_MARKER = "Permission is hereby granted, free of charge"


class UpdateError(Exception):
    """Mise à jour refusée ; le catalogue en place est gardé."""


def log_error(message):
    try:
        import syslog
        syslog.openlog("appalias", syslog.LOG_PID, syslog.LOG_DAEMON)
        syslog.syslog(syslog.LOG_ERR, "catalog update: %s" % message)
    except ImportError:
        print("catalog update: %s" % message, file=sys.stderr)


# --- réseau ------------------------------------------------------------------
def check_url(url):
    """HTTPS vers un hôte autorisé, sans port ni identifiants ; UpdateError sinon."""
    parts = urllib.parse.urlsplit(str(url))
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS \
            or parts.port is not None or parts.username or parts.password:
        raise UpdateError("URL refused (only https to %s): %s"
                          % (", ".join(sorted(ALLOWED_HOSTS)), parts.hostname or url))
    return url


class _SameHostsRedirect(urllib.request.HTTPRedirectHandler):
    """Redirection suivie seulement vers un hôte autorisé, en HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def make_opener(cafile=dot.CA_BUNDLE):
    ctx = dot.make_context(cafile)
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _SameHostsRedirect())


def http_get(url, max_bytes, opener, dest=None, timeout=TIMEOUT):
    """Contenu de url (ou écrit dans dest), plafonné à max_bytes pendant la lecture."""
    check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "os-appalias-catalog-update",
                                               "Accept": "application/vnd.github+json"
                                               if "api.github.com" in url else "*/*"})
    chunks = []
    size = 0
    out = open(dest, "xb") if dest else None
    try:
        with opener.open(req, timeout=timeout) as resp:
            check_url(resp.geturl())
            length = resp.headers.get("Content-Length") if getattr(resp, "headers", None) else None
            if length and length.isdigit() and int(length) > max_bytes:
                raise UpdateError("download too large (%s bytes, %d at most)" % (length, max_bytes))
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UpdateError("download too large (more than %d bytes)" % max_bytes)
                if out:
                    out.write(chunk)
                else:
                    chunks.append(chunk)
    except UpdateError:
        raise
    except (OSError, ValueError) as exc:
        raise UpdateError("download failed: %s" % exc) from exc
    finally:
        if out:
            out.close()
    return b"".join(chunks)


def latest_commit(opener):
    try:
        data = json.loads(http_get(API_URL, MAX_API, opener).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpdateError("unreadable answer from api.github.com") from exc
    sha = data.get("sha") if isinstance(data, dict) else None
    if not isinstance(sha, str) or not snap.COMMIT_RE.match(sha):
        raise UpdateError("no commit in the answer from api.github.com")
    return sha


# --- génération (processus sans privilèges) ------------------------------------
def popular_names(embedded_dir=catmod.CATALOG_DIR):
    try:
        with open(os.path.join(embedded_dir, catmod.POPULAR_NAME), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    names = []
    for cat in data.get("categories", []) if isinstance(data, dict) else []:
        for name in cat.get("lists", []) if isinstance(cat, dict) else []:
            if isinstance(name, str) and name not in names:
                names.append(name)
    return names


def sanity(lst_text, popular, min_lists=MIN_LISTS):
    """Contrôle de cohérence d'un .lst ; UpdateError si refusé."""
    cat = v2fly.Catalog.parse(lst_text)
    names = cat.names()
    if len(names) < min_lists:
        raise UpdateError("only %d lists (%d expected at least)" % (len(names), min_lists))
    missing = [n for n in popular if n not in cat.lists]
    if missing:
        raise UpdateError("popular lists missing: %s" % ", ".join(missing[:10]))
    broken = [n for n in names if cat.resolve(n).broken]
    if broken:
        raise UpdateError("broken lists: %s" % ", ".join(broken[:10]))
    return len(names)


def generate(archive, commit, workdir, popular, min_lists=MIN_LISTS):
    """Extraction, génération et contrôle dans workdir/out ; renvoie le manifeste."""
    out = os.path.join(workdir, "out")
    os.makedirs(out, mode=0o700, exist_ok=True)
    try:
        manifest = snap.snapshot(archive, commit, out, source="online")
        with open(os.path.join(out, catmod.LST_NAME), "r", encoding="ascii") as fh:
            sanity(fh.read(), popular, min_lists)
    except (snap.SnapshotError, OSError, UnicodeDecodeError, ValueError) as exc:
        raise UpdateError(str(exc)) from exc
    except Exception as exc:  # tarfile.TarError et autres : archive refusée
        raise UpdateError("archive refused: %s" % exc) from exc
    return manifest


def child_payload(archive, commit, workdir, popular, min_lists=MIN_LISTS):
    """Travail du processus sans privilèges : sortie complète en un seul dict.

    Le .lst (base64), la licence et le manifeste passent par stdout : le
    processus root ne lit jamais de fichier du dossier de travail.
    """
    try:
        manifest = generate(archive, commit, workdir, popular, min_lists)
        out = os.path.join(workdir, "out")
        with open(os.path.join(out, catmod.LST_NAME), "rb") as fh:
            lst = fh.read(snap.DEFAULT_MAX_LST + 1)
        with open(os.path.join(out, catmod.LICENSE_NAME), "r", encoding="utf-8") as fh:
            license_text = fh.read(MAX_LICENSE + 1)
    except UpdateError as exc:
        return {"ok": False, "error": str(exc)}
    except (OSError, UnicodeDecodeError) as exc:
        return {"ok": False, "error": "generation failed: %s" % exc}
    return {"ok": True, "manifest": manifest, "license": license_text,
            "lst": base64.b64encode(lst).decode("ascii")}


def child_main(argv):
    """Point d'entrée du sous-processus : un seul JSON sur stdout."""
    archive, commit, workdir, min_lists = argv[0], argv[1], argv[2], int(argv[3])
    popular = json.loads(argv[4])
    sys.stdout.write(json.dumps(child_payload(archive, commit, workdir, popular, min_lists)))
    sys.stdout.flush()
    return 0


def parse_child_output(raw, commit, archive_sha256):
    """Sortie du processus sans privilèges -> (lst, licence, manifeste) validés.

    Taille plafonnée (MAX_CHILD_OUTPUT), types contrôlés, empreinte du .lst
    conforme au manifeste, licence avec l'en-tête attendu et le texte MIT.
    """
    if not isinstance(raw, bytes) or len(raw) > MAX_CHILD_OUTPUT:
        raise UpdateError("generation output too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError("unreadable generation output") from exc
    if not isinstance(data, dict):
        raise UpdateError("unreadable generation output")
    if data.get("ok") is not True:
        raise UpdateError(str(data.get("error") or "generation failed")[:500])
    manifest = data.get("manifest")
    license_text = data.get("license")
    lst_b64 = data.get("lst")
    if not isinstance(manifest, dict) or not isinstance(license_text, str) or not isinstance(lst_b64, str):
        raise UpdateError("unexpected generation output")
    try:
        lst = base64.b64decode(lst_b64.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise UpdateError("unexpected generation output") from exc
    if len(lst) > snap.DEFAULT_MAX_LST or len(license_text) > MAX_LICENSE:
        raise UpdateError("generated catalog too large")
    if hashlib.sha256(lst).hexdigest() != manifest.get("lst_sha256") or len(lst) != manifest.get("lst_bytes"):
        raise UpdateError("generated catalog differs from its manifest")
    if (manifest.get("commit") != commit or manifest.get("archive_sha256") != archive_sha256
            or manifest.get("source") != "online"):
        raise UpdateError("unexpected manifest")
    if not license_text.startswith(LICENSE_HEAD % commit) or MIT_MARKER not in license_text:
        raise UpdateError("unexpected license text")
    try:
        lst.decode("ascii")
    except UnicodeDecodeError as exc:
        raise UpdateError("generated catalog is not ASCII") from exc
    manifest = dict(manifest, license_sha256=hashlib.sha256(license_text.encode("utf-8")).hexdigest())
    return lst, license_text, manifest


def unprivileged_ids(user=UNPRIVILEGED):
    """(uid, gid) de l'utilisateur sans privilèges si le processus est root, sinon None."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None
    import pwd
    try:
        entry = pwd.getpwnam(user)
    except KeyError as exc:
        raise UpdateError("user %s not found" % user) from exc
    return entry.pw_uid, entry.pw_gid


def read_capped(stream, limit, timeout, proc):
    """Lit stream jusqu'à limit + 1 octets ; tue proc au-delà du délai ou de la taille."""
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    chunks = []
    size = 0
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                proc.kill()
                raise UpdateError("generation output too large")
            chunks.append(chunk)
    finally:
        timer.cancel()
    return b"".join(chunks)


def run_child(archive, commit, workdir, popular, min_lists, ids):
    """Sortie brute (JSON) de child_payload, sous l'utilisateur sans privilèges
    (ids), ou en direct si ids est None (processus déjà sans privilèges :
    tests, exécution manuelle). Dans les deux cas, parse_child_output la valide."""
    if ids is None:
        return json.dumps(child_payload(archive, commit, workdir, popular, min_lists)).encode("utf-8")
    uid, gid = ids
    argv = [sys.executable, "-I", os.path.abspath(__file__), "--child", archive, commit, workdir,
            str(min_lists), json.dumps(popular)]
    try:
        proc = subprocess.Popen(argv, user=uid, group=gid, extra_groups=[], umask=0o077,
                                cwd=workdir, env={"PATH": "/usr/bin:/bin", "HOME": workdir,
                                                  "TMPDIR": workdir, "LANG": "C.UTF-8"},
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except (OSError, ValueError) as exc:
        raise UpdateError("generation process failed: %s" % exc) from exc
    try:
        raw = read_capped(proc.stdout, MAX_CHILD_OUTPUT, CHILD_TIMEOUT, proc)
    finally:
        proc.stdout.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    if proc.returncode != 0 and not raw:
        raise UpdateError("generation process failed (code %d)" % proc.returncode)
    return raw


# --- installation (root, aucun fichier du dossier de travail n'est lu) ------
def install(online_dir, lst, license_text, manifest, now):
    """Installe atomiquement les données validées par parse_child_output."""
    manifest = dict(manifest, fetched=int(now))
    os.makedirs(online_dir, mode=0o755, exist_ok=True)
    # .lst d'abord : tant que le manifeste n'est pas remplacé, l'empreinte ne
    # correspond pas et catalog.py reprend l'instantané embarqué.
    for name, data in ((catmod.LST_NAME, lst),
                       (catmod.LICENSE_NAME, license_text.encode("utf-8")),
                       (catmod.MANIFEST_NAME,
                        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))):
        fd, tmp = tempfile.mkstemp(prefix=".catalog.", dir=online_dir)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(online_dir, name))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return manifest


def list_names(directory):
    try:
        with open(os.path.join(directory, catmod.LST_NAME), "r", encoding="utf-8", errors="replace") as fh:
            return set(v2fly.Catalog.parse(fh.read()).names())
    except OSError:
        return set()


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
                raise UpdateError("another catalog update is running") from exc
        else:
            try:
                self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                self.excl = True
            except FileExistsError as exc:
                raise UpdateError("another catalog update is running") from exc
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


def update(opener=None, online_dir=catmod.ONLINE_DIR, embedded_dir=catmod.CATALOG_DIR,
           lock_path=LOCK_PATH, min_lists=MIN_LISTS, ids="auto", now=None):
    """Mise à jour complète ; renvoie le résultat JSON (jamais d'exception)."""
    now = time.time() if now is None else now
    current_dir, origin = catmod.select_dir(embedded_dir, online_dir)
    current = catmod.read_manifest(current_dir) or {}
    result = {"result": "failed", "origin": origin, "previous_commit": current.get("commit")}
    workdir = None
    try:
        with Lock(lock_path):
            opener = opener or make_opener()
            commit = latest_commit(opener)
            result["commit"] = commit
            if commit == current.get("commit"):
                result["result"] = "unchanged"
                return result
            if ids == "auto":
                ids = unprivileged_ids()
            workdir = tempfile.mkdtemp(prefix="appalias-v2fly-")
            os.chmod(workdir, 0o700)
            archive = os.path.join(workdir, "v2fly.tar.gz")
            http_get(ARCHIVE_URL % commit, MAX_ARCHIVE, opener, dest=archive)
            digest = snap.sha256_file(archive)
            if ids is not None:
                os.chown(archive, ids[0], ids[1])
                os.chmod(archive, 0o400)
                os.chown(workdir, ids[0], ids[1])
            raw = run_child(archive, commit, workdir, popular_names(embedded_dir), min_lists, ids)
            lst, license_text, manifest = parse_child_output(raw, commit, digest)
            installed = install(online_dir, lst, license_text, manifest, now)
            old = list_names(current_dir)
            new = list_names(online_dir)
            result.update({
                "result": "updated",
                "origin": "online",
                "date": installed.get("date"),
                "lists": len(new),
                "added": sorted(new - old)[:50],
                "removed": sorted(old - new)[:50],
                "added_count": len(new - old),
                "removed_count": len(old - new),
            })
            if catmod.select_dir(embedded_dir, online_dir)[1] != "online":
                result["note"] = "the embedded snapshot is newer and stays in use"
                result["origin"] = "embedded"
            return result
    except (UpdateError, OSError, UnicodeDecodeError, AttributeError, ValueError, KeyError,
            TypeError) as exc:
        # jamais de trace : une ligne d'erreur, le catalogue en place est gardé
        result["error"] = str(exc) or type(exc).__name__
        log_error("%s (previous catalog kept)" % exc)
        return result
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


def reset(online_dir=catmod.ONLINE_DIR):
    removed = 0
    for name in FILES:
        try:
            os.unlink(os.path.join(online_dir, name))
            removed += 1
        except FileNotFoundError:
            pass
        except OSError as exc:
            return {"result": "failed", "error": str(exc)}
    return {"result": "reset", "removed": removed}


def online_enabled(config_path="/var/db/appalias/config.json"):
    """Option catalog_online_update de config.json ; absente ou illisible = activée."""
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            general = json.load(fh).get("general") or {}
    except (OSError, ValueError, AttributeError):
        return True
    value = general.get("catalog_online_update", True) if isinstance(general, dict) else True
    return value is not False and str(value).strip().lower() not in ("0", "false", "no", "off")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv[:1] == ["--child"] and len(argv) == 6:
            return child_main(argv[1:])
        if argv == ["update", "--if-enabled"]:
            out = update() if online_enabled() else {"result": "disabled"}
        elif argv == ["update"]:
            out = update()
        elif argv == ["reset"]:
            out = reset()
        else:
            out = {"result": "failed", "error": "usage: catalog_update.py update | reset"}
    except (UpdateError, OSError, UnicodeDecodeError, AttributeError, ValueError, KeyError,
            TypeError) as exc:
        # sortie JSON d'erreur, jamais de trace
        out = {"result": "failed", "error": str(exc) or type(exc).__name__}
    print(json.dumps(out, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
