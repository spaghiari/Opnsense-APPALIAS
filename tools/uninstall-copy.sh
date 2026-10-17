#!/bin/sh
#
# Désinstallation d'une installation par copie d'os-appalias.
#
# Usage (en root, depuis la même copie du projet que pour l'installation) :
#   sh tools/uninstall-copy.sh        # désinstalle
#   sh tools/uninstall-copy.sh -n     # affiche seulement ce qui serait fait
#
# Retire exactement les fichiers installés (liste générée depuis
# net/appalias/src/, comme à l'installation), plus les caches Python
# générés dans le dossier lib/ du plugin. Les dossiers devenus vides sont
# supprimés (rmdir ne touche pas un dossier non vide).
#
# Ne touche PAS à config.xml : la section OPNsense/appalias et les alias
# APP_* restent. Les supprimer dans l'interface avant, si besoin.
# /var/db/appalias (config.json, state.json) est conservé.

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

FILES=$(cd "${SRC}" && find . -type f ! -name '*.pyc' | sed 's|^\./||' | sort)

if [ -x "${DEST}/etc/rc.d/appaliasd" ]; then
	run "${DEST}/etc/rc.d/appaliasd" onestop || true
fi

for f in ${FILES}; do
	if [ -e "${DEST}/${f}" ]; then
		run rm -f "${DEST}/${f}"
	fi
done

for d in "${DEST}/opnsense/scripts/OPNsense/AppAlias/__pycache__" \
         "${DEST}/opnsense/scripts/OPNsense/AppAlias/lib/__pycache__"; do
	[ -d "${d}" ] && run rm -rf "${d}"
done

# Dossiers devenus vides, du plus profond au moins profond.
DIRS=$(for f in ${FILES}; do dirname "${f}"; done | sort -u -r)
for d in ${DIRS}; do
	[ -d "${DEST}/${d}" ] && run rmdir "${DEST}/${d}" 2>/dev/null || true
done

run rm -f /etc/rc.conf.d/appaliasd
run rm -f /tmp/opnsense_menu_cache.xml /tmp/opnsense_acl_cache.json
run service configd restart

echo
echo "Désinstallé. Conservés : /var/db/appalias, la section appalias de config.xml et les alias APP_*."
