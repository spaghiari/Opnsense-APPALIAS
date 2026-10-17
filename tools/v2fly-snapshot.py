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
dans un dossier temporaire, avec des plafonds de taille.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.request

SOURCE = "https://github.com/v2fly/domain-list-community"
URL = SOURCE + "/archive/%s.tar.gz"

EXCLUDED_PREFIXES = ("category-", "geolocation-", "tld-")
EXCLUDED_NAMES = ("cn", "private")
EXCLUDED_CHARS = ("!",)
LIST_NAME = re.compile(r"^[a-z0-9-]+$")

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_MEMBERS = 50000
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
DEFAULT_MAX_LST = 1572864  # 1,5 Mo

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SnapshotError(Exception):
    """Archive refusée ou sortie impossible."""


def excluded_reason(name):
    """Raison d'exclusion d'une liste, ou None si elle est conservée."""
    for char in EXCLUDED_CHARS:
        if char in name:
            return "contains"
    if name in EXCLUDED_NAMES:
        return "name"
    if name.startswith(EXCLUDED_PREFIXES):
        return "prefix"
    if not LIST_NAME.match(name):
        return "invalid_name"
    return None


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def clean_line(raw):
    """Ligne utile (commentaire retiré, espaces réduits) ou None."""
    pos = raw.find("#")
    if pos >= 0:
        raw = raw[:pos]
    line = " ".join(raw.split())
    return line or None


def read_archive(path, commit):
    """Renvoie (listes {nom: [lignes brutes]}, texte LICENSE, date ISO)."""
    with tarfile.open(path, "r:*") as tar:
        comment = (tar.pax_headers or {}).get("comment")
        if comment is not None and comment != commit:
            raise SnapshotError("l'archive correspond au commit %s, pas %s" % (comment, commit))
        members = []
        count = 0
        total = 0
        top = None
        for member in tar:
            count += 1
            if count > MAX_MEMBERS:
                raise SnapshotError("trop d'entrées dans l'archive")
            parts = member.name.split("/")
            if top is None:
                top = parts[0]
            if parts[0] != top or ".." in parts:
                raise SnapshotError("chemin inattendu dans l'archive : %r" % member.name)
            rel = parts[1:]
            wanted = (rel == ["LICENSE"]) or (len(rel) == 2 and rel[0] == "data" and rel[1])
            if not wanted:
                continue
            if not member.isreg():
                raise SnapshotError("entrée non régulière : %r" % member.name)
            if member.size > MAX_FILE:
                raise SnapshotError("fichier trop gros : %r" % member.name)
            total += member.size
            if total > MAX_TOTAL:
                raise SnapshotError("contenu total trop gros")
            members.append(member)
        if top is None:
            raise SnapshotError("archive vide")

        lists = {}
        license_text = None
        mtime = 0
        with tempfile.TemporaryDirectory(prefix="v2fly-") as tmp:
            tar.extractall(tmp, members=members, filter="data")
            for member in members:
                mtime = max(mtime, int(member.mtime))
                rel = member.name.split("/")[1:]
                with open(os.path.join(tmp, *member.name.split("/")), "rb") as fh:
                    data = fh.read()
                if rel == ["LICENSE"]:
                    license_text = data.decode("utf-8")
                else:
                    lists[rel[1]] = data
    if license_text is None:
        raise SnapshotError("LICENSE absent de l'archive")
    if "Permission is hereby granted, free of charge" not in license_text:
        raise SnapshotError("LICENSE de l'archive : texte MIT non reconnu")
    if not lists:
        raise SnapshotError("aucune liste sous data/")
    date = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return lists, license_text, date


def build(lists):
    """Texte du .lst et compteurs."""
    counts = {
        "lists_total": len(lists),
        "lists_kept": 0,
        "lists_excluded": 0,
        "excluded_by_reason": {"contains": 0, "invalid_name": 0, "name": 0, "prefix": 0},
        "lines": 0,
        "includes": 0,
        "includes_to_excluded": 0,
        "non_ascii_dropped": 0,
    }
    kept = {}
    for name in sorted(lists):
        reason = excluded_reason(name)
        if reason is not None:
            counts["lists_excluded"] += 1
            counts["excluded_by_reason"][reason] += 1
            continue
        lines = set()
        for raw in lists[name].decode("utf-8", "replace").splitlines():
            line = clean_line(raw)
            if line is None:
                continue
            if not line.isascii() or "�" in line:
                counts["non_ascii_dropped"] += 1
                continue
            lines.add(line)
        kept[name] = sorted(lines)
    out = []
    for name in sorted(kept):
        out.append("[%s]\n" % name)
        for line in kept[name]:
            out.append(line + "\n")
            counts["lines"] += 1
            if line.lower().startswith("include:"):
                counts["includes"] += 1
                target = line[len("include:"):].split(" ")[0].strip().lower()
                if excluded_reason(target) is not None:
                    counts["includes_to_excluded"] += 1
        out.append("\n")
    counts["lists_kept"] = len(kept)
    return "".join(out), counts


def license_notice(commit, text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
    head = (
        "The domain lists in v2fly.lst are a snapshot of\n"
        "v2fly/domain-list-community (%s), commit %s.\n"
        "They are distributed under the MIT License reproduced below.\n"
        "\n"
    ) % (SOURCE, commit)
    return head + text


def write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def snapshot(archive, commit, out_dir, expected_sha256=None, max_bytes=DEFAULT_MAX_LST):
    """Produit les trois fichiers dans out_dir ; renvoie le manifeste."""
    actual = sha256_file(archive)
    if expected_sha256 is not None and actual != expected_sha256:
        raise SnapshotError("sha256 de l'archive : %s, attendu %s" % (actual, expected_sha256))
    lists, license_text, date = read_archive(archive, commit)
    lst, counts = build(lists)
    raw = lst.encode("ascii")
    if len(raw) > max_bytes:
        raise SnapshotError("v2fly.lst fait %d octets, plafond %d" % (len(raw), max_bytes))
    manifest = {
        "source": SOURCE,
        "commit": commit,
        "date": date,
        "archive_sha256": actual,
        "lst_sha256": hashlib.sha256(raw).hexdigest(),
        "lst_bytes": len(raw),
        "license": "MIT",
        "exclusions": {
            "prefixes": list(EXCLUDED_PREFIXES),
            "names": list(EXCLUDED_NAMES),
            "contains": list(EXCLUDED_CHARS),
            "name_pattern": LIST_NAME.pattern,
        },
        "counts": counts,
    }
    os.makedirs(out_dir, exist_ok=True)
    write_text(os.path.join(out_dir, "v2fly.lst"), lst)
    write_text(os.path.join(out_dir, "v2fly.manifest.json"),
               json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    write_text(os.path.join(out_dir, "LICENSE.v2fly"), license_notice(commit, license_text))
    return manifest


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
