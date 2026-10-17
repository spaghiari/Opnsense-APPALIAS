#!/bin/sh
#
# Installation d'os-appalias par copie (sans paquet), sur la box OPNsense.
#
# Usage (en root, depuis une copie du projet sur la box) :
#   sh tools/install-copy.sh        # installe
#   sh tools/install-copy.sh -n     # affiche seulement ce qui serait fait
#
# Copie net/appalias/src/ vers /usr/local/, purge les caches de menu et
# d'ACL, puis redémarre configd. La liste des fichiers est générée depuis
# src/ ; tools/uninstall-copy.sh utilise la même liste.

set -eu

DRYRUN=0
[ "${1:-}" = "-n" ] && DRYRUN=1

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SRC="${ROOT}/net/appalias/src"
DEST="/usr/local"

if [ ! -d "${SRC}" ]; then
	echo "Dossier source introuvable : ${SRC}" >&2
	exit 1
fi
if [ "${DRYRUN}" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
	echo "À lancer en root." >&2
	exit 1
fi

run()
{
	echo "+ $*"
	[ "${DRYRUN}" -eq 1 ] || "$@"
}

file_mode()
{
	case "$1" in
	etc/rc.d/*) echo 0755 ;;
	opnsense/scripts/OPNsense/AppAlias/*.py) echo 0755 ;;
	opnsense/scripts/OPNsense/AppAlias/*.php) echo 0755 ;;
	*) echo 0644 ;;
	esac
}

FILES=$(cd "${SRC}" && find . -type f ! -name '*.pyc' | sed 's|^\./||' | sort)

echo "Fichiers à installer dans ${DEST} :"
echo "${FILES}" | sed 's|^|  |'
echo

for f in ${FILES}; do
	run install -d -m 0755 "${DEST}/$(dirname "${f}")"
	run install -m "$(file_mode "${f}")" "${SRC}/${f}" "${DEST}/${f}"
done

run install -d -m 0750 /var/db/appalias
run rm -f /tmp/opnsense_menu_cache.xml /tmp/opnsense_acl_cache.json
run service configd restart

echo
echo "Installé. Étapes suivantes :"
echo "  1. Services > AppAlias > Réglages : indiquer le résolveur amont, activer, enregistrer."
echo "  2. Ajouter une application, puis « Appliquer »."
echo "  3. Rediriger Unbound (Query Forwarding) ou Dnsmasq vers 127.0.0.1 port 53535."
