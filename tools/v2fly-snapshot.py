#!/usr/bin/env python3
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

"""Instantané du catalogue v2fly/domain-list-community (outil de maintenance).

Non livré dans le paquet : lancé à la main par le mainteneur pour produire
les fichiers embarqués sous scripts/OPNsense/AppAlias/catalog/ :

- v2fly.lst           : une section « [liste] » par liste, lignes triées,
                        commentaires retirés, « include: » conservés ;
- v2fly.manifest.json : commit, date, sha256 de l'archive et du .lst,
                        règles d'exclusion, compteurs ;
- LICENSE.v2fly       : notice de copyright et licence MIT de l'archive.

Usage (stdlib uniquement, lancer Python avec -I) :

  # premier téléchargement : l'empreinte est calculée et affichée
  python3 -I tools/v2fly-snapshot.py --commit <sha1> --fetch --tofu \\
      --archive /chemin/vide/v2fly.tar.gz --out <dossier catalog>

  # rafraîchissement ou reconstruction : empreinte connue, vérifiée
  python3 -I tools/v2fly-snapshot.py --commit <sha1> --sha256 <hex> \\
      [--fetch] --archive /chemin/v2fly.tar.gz --out <dossier catalog>

L'archive est une donnée non fiable : elle n'est jamais exécutée, seuls les
fichiers réguliers data/* et LICENSE sont extraits (tarfile, filter="data"),
dans un dossier temporaire, avec des plafonds de taille. La génération est
dans lib/v2fly_snapshot.py, partagée avec la mise à jour en ligne du pare-feu
(catalog_update.py).
"""

import argparse
import os
import sys
import tarfile
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "net", "appalias", "src", "opnsense", "scripts", "OPNsense", "AppAlias"))

from lib.v2fly_snapshot import (  # noqa: E402,F401
    COMMIT_RE, DEFAULT_MAX_LST, EXCLUDED_CHARS, EXCLUDED_NAMES, EXCLUDED_PREFIXES, LIST_NAME,
    MAX_ARCHIVE, MAX_FILE, MAX_MEMBERS, MAX_TOTAL, SHA256_RE, SOURCE, URL, SnapshotError, build,
    clean_line, excluded_reason, license_notice, read_archive, sha256_file, snapshot, write_text)


def fetch(commit, dest):
    """Télécharge l'archive du commit vers dest (qui ne doit pas exister)."""
    if os.path.exists(dest):
        raise SnapshotError("%s existe déjà : choisir un chemin neuf" % dest)
    url = URL % commit
    req = urllib.request.Request(url, headers={"User-Agent": "os-appalias-snapshot"})
    size = 0
    with urllib.request.urlopen(req, timeout=60) as resp:
        if not resp.geturl().startswith("https://"):
            raise SnapshotError("redirection hors HTTPS : %s" % resp.geturl())
        with open(dest, "xb") as out:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise SnapshotError("archive trop grosse (> %d octets)" % MAX_ARCHIVE)
                out.write(chunk)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Instantané v2fly/domain-list-community")
    parser.add_argument("--commit", required=True, help="commit épinglé (40 hex)")
    parser.add_argument("--archive", required=True, help="chemin de l'archive .tar.gz")
    parser.add_argument("--fetch", action="store_true", help="télécharger l'archive vers --archive")
    parser.add_argument("--sha256", help="empreinte attendue de l'archive (64 hex)")
    parser.add_argument("--tofu", action="store_true",
                        help="premier téléchargement : accepter et afficher l'empreinte")
    parser.add_argument("--out", required=True, help="dossier de sortie (catalog/)")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_LST)
    args = parser.parse_args(argv)

    if not COMMIT_RE.match(args.commit):
        parser.error("--commit : 40 caractères hexadécimaux en minuscules")
    if args.sha256 is not None and not SHA256_RE.match(args.sha256):
        parser.error("--sha256 : 64 caractères hexadécimaux en minuscules")
    if args.sha256 is None and not args.tofu:
        parser.error("--sha256 obligatoire (ou --tofu pour un premier téléchargement)")
    try:
        if args.fetch:
            fetch(args.commit, args.archive)
        manifest = snapshot(args.archive, args.commit, args.out, args.sha256, args.max_bytes)
    except (SnapshotError, OSError, tarfile.TarError, UnicodeDecodeError) as exc:
        print("erreur : %s" % exc, file=sys.stderr)
        return 1
    print("archive_sha256 %s" % manifest["archive_sha256"])
    print("lst_sha256     %s" % manifest["lst_sha256"])
    print("lst_bytes      %d" % manifest["lst_bytes"])
    print("listes         %d conservées, %d exclues" % (
        manifest["counts"]["lists_kept"], manifest["counts"]["lists_excluded"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
