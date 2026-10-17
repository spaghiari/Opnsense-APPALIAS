#!/bin/sh
#
# Rétablit les bits exécutables des fichiers d'os-appalias lancés
# directement par configd ou par rc. À lancer sur la machine de build,
# avant « make package » (un projet créé ou copié sous Windows les perd).
#
# Usage :
#   sh tools/fix-modes.sh        # applique chmod 0755
#   sh tools/fix-modes.sh -n     # affiche seulement la liste
#
# La liste est dérivée, pas codée en dur :
#   - les scripts rc de src/etc/rc.d/ ;
#   - les fichiers /usr/local/... cités en « command: » dans les actions
#     configd (actions_appalias.conf) ;
#   - les fichiers « ${scriptdir}/... » lancés par le script rc
#     (dont appaliasd.py).

set -eu

DRYRUN=0
[ "${1:-}" = "-n" ] && DRYRUN=1

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SRC="${ROOT}/net/appalias/src"
ACTIONS="${SRC}/opnsense/service/conf/actions.d/actions_appalias.conf"

if [ ! -d "${SRC}/etc/rc.d" ] || [ ! -f "${ACTIONS}" ]; then
	echo "Arborescence du plugin introuvable sous ${SRC}" >&2
	exit 1
fi

list_files()
{
	# 1. scripts rc
	for f in "${SRC}"/etc/rc.d/*; do
		[ -f "${f}" ] && echo "${f#${SRC}/}"
	done
	# 2. commandes des actions configd sous /usr/local/
	sed -n 's|^command:/usr/local/\([^ ]*\).*|\1|p' "${ACTIONS}"
	# 3. fichiers lancés par le script rc via ${scriptdir}
	for rc in "${SRC}"/etc/rc.d/*; do
		[ -f "${rc}" ] || continue
		dir=$(sed -n 's|^scriptdir="/usr/local/\(.*\)"$|\1|p' "${rc}")
		[ -n "${dir}" ] || continue
		grep -o '\${scriptdir}/[A-Za-z0-9_.-]*' "${rc}" | sed "s|\${scriptdir}|${dir}|"
	done
}

FILES=$(list_files | sort -u)

for f in ${FILES}; do
	if [ ! -f "${SRC}/${f}" ]; then
		echo "absent (ignoré) : ${f}" >&2
		continue
	fi
	echo "chmod 0755 ${f}"
	[ "${DRYRUN}" -eq 1 ] || chmod 0755 "${SRC}/${f}"
done
