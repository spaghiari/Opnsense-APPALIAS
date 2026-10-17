#!/bin/sh
#
# Construit le paquet os-appalias (NON signé) pour une ABI donnée. Conçu pour
# tourner dans une VM FreeBSD (CI). Ce script ne touche jamais à la clé de
# signature : la signature est faite ailleurs, par scripts/sign-repo.sh.
#
# Usage :
#   sh scripts/build-packages.sh <opnsense_abi> <pkg_abi> <plugins_commit>
#   ex. : sh scripts/build-packages.sh 26.7 FreeBSD:15:amd64 <sha1 de 40 hex>
#
# Résultat : dist/os-appalias-<VERSION>.pkg, et rien d'autre dans dist/.
#   <VERSION> = PLUGIN_VERSION de net/appalias/Makefile, suivi de
#   _<PLUGIN_REVISION> si cette variable est présente (ex. 0.2_1).
#   Le build passe PLUGIN_DEVEL= (vide) pour supprimer le suffixe « -devel »
#   que Mk/defaults.mk ajoute par défaut. Tout autre nom de paquet fait échouer.

set -eu
set +x

if [ "$#" -ne 3 ]; then
	echo "Usage : $0 <opnsense_abi> <pkg_abi> <plugins_commit>" >&2
	exit 64
fi

OPNSENSE_ABI="$1"
PKG_ABI="$2"
PLUGINS_COMMIT="$3"

case "${OPNSENSE_ABI}" in
[0-9][0-9].[0-9]*) ;;
*) echo "OPNsense ABI invalide : ${OPNSENSE_ABI}" >&2; exit 64 ;;
esac
case "${PKG_ABI}" in
FreeBSD:[0-9]*:amd64) ;;
*) echo "ABI pkg invalide : ${PKG_ABI}" >&2; exit 64 ;;
esac
case "${PLUGINS_COMMIT}" in
*[!0-9a-f]*) echo "Commit invalide : ${PLUGINS_COMMIT}" >&2; exit 64 ;;
esac
if [ "${#PLUGINS_COMMIT}" -ne 40 ]; then
	echo "Commit invalide (40 caractères hexadécimaux attendus)" >&2
	exit 64
fi

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PLUGINS_DIR="/tmp/plugins"
PLUGIN_DIR="${PLUGINS_DIR}/net/appalias"
DIST="${ROOT}/dist"

# Version attendue, lue dans le Makefile du plugin
MAKEFILE="${ROOT}/net/appalias/Makefile"
VERSION=$(sed -n 's/^PLUGIN_VERSION=[[:space:]]*\([^[:space:]]*\).*/\1/p' "${MAKEFILE}")
REVISION=$(sed -n 's/^PLUGIN_REVISION=[[:space:]]*\([^[:space:]]*\).*/\1/p' "${MAKEFILE}")
if [ -z "${VERSION}" ]; then
	echo "PLUGIN_VERSION introuvable dans ${MAKEFILE}" >&2
	exit 1
fi
if [ -n "${REVISION}" ]; then
	VERSION="${VERSION}_${REVISION}"
fi
EXPECTED="os-appalias-${VERSION}.pkg"

# 1. Prérequis : pkg d'abord, puis git
if [ ! -x /usr/local/sbin/pkg ]; then
	pkg bootstrap -f -y || pkg install -y pkg
fi
if [ ! -x /usr/local/sbin/pkg ]; then
	echo "/usr/local/sbin/pkg introuvable" >&2
	exit 1
fi
pkg install -y git

# 2. Sources : opnsense/plugins épinglé par commit, puis notre plugin
rm -rf "${PLUGINS_DIR}"
mkdir -p "${PLUGINS_DIR}"
cd "${PLUGINS_DIR}"
git init -q
git remote add origin https://github.com/opnsense/plugins
git fetch --depth 1 origin "${PLUGINS_COMMIT}"
git checkout -q FETCH_HEAD
HEAD_SHA=$(git rev-parse HEAD)
if [ "${HEAD_SHA}" != "${PLUGINS_COMMIT}" ]; then
	echo "Commit récupéré inattendu : ${HEAD_SHA} (attendu ${PLUGINS_COMMIT})" >&2
	exit 1
fi
cd "${ROOT}"
rm -rf "${PLUGIN_DIR}"
cp -R "${ROOT}/net/appalias" "${PLUGIN_DIR}"

# 3. Bits exécutables (perdus sous Windows)
APPALIAS_SRC="${PLUGIN_DIR}/src" sh "${ROOT}/tools/fix-modes.sh"

# 4. Construction du paquet (PLUGIN_DEVEL= vide : pas de suffixe -devel)
make -C "${PLUGIN_DIR}" PLUGIN_ABIS="${OPNSENSE_ABI}" PLUGIN_DEVEL= package

# 5. Le nom du paquet doit être exactement celui attendu, et seul
set -- "${PLUGIN_DIR}"/work/pkg/*.pkg
if [ "$#" -ne 1 ] || [ "$(basename "$1")" != "${EXPECTED}" ]; then
	echo "Paquet attendu : ${EXPECTED} (seul) ; trouvé :" >&2
	ls -l "${PLUGIN_DIR}/work/pkg/" >&2 || true
	exit 1
fi

rm -rf "${DIST}"
mkdir -p "${DIST}"
cp "$1" "${DIST}/${EXPECTED}"

# 6. Contrôle visuel
pkg info -F "${DIST}/${EXPECTED}" | grep -E '^(Name|Version|Architecture)'
ls -l "${DIST}"
