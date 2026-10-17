#!/bin/sh
#
# Signe le dépôt pkg d'une ABI : indexe site/<pkg_abi>/All/*.pkg avec
# « pkg repo » et la clé privée fournie par PKG_SIGNING_KEY. Ne fait rien
# d'autre (ni clone, ni make). Lancé dans la VM FreeBSD du job « sign ».
#
# Usage :   PKG_SIGNING_KEY=... sh scripts/sign-repo.sh <pkg_abi>
#
# La clé n'est jamais affichée : pas de « set -x », pas d'écho. Elle est
# écrite hors du workspace, en 0600 (umask 077), et supprimée dès que
# « pkg repo » a fini ou que le script sort (trap).

set -eu
set +x

if [ "$#" -ne 1 ]; then
	echo "Usage : $0 <pkg_abi>" >&2
	exit 64
fi

PKG_ABI="$1"
case "${PKG_ABI}" in
FreeBSD:[0-9]*:amd64) ;;
*) echo "ABI pkg invalide : ${PKG_ABI}" >&2; exit 64 ;;
esac

if [ -z "${PKG_SIGNING_KEY:-}" ]; then
	echo "PKG_SIGNING_KEY absent ou vide" >&2
	exit 1
fi

OUT="site/${PKG_ABI}"
if ! ls "${OUT}"/All/os-appalias-*.pkg > /dev/null 2>&1; then
	echo "Aucun paquet dans ${OUT}/All/" >&2
	exit 1
fi

KEYFILE=""
cleanup()
{
	if [ -n "${KEYFILE}" ]; then
		rm -f "${KEYFILE}"
	fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

# pkg complet requis : jamais le bootstrapper de base avec la clé présente.
if [ ! -x /usr/local/sbin/pkg ]; then
	echo "/usr/local/sbin/pkg introuvable : signature refusée" >&2
	exit 1
fi

umask 077
KEYFILE=$(mktemp /tmp/appalias-key.XXXXXX)
printf '%s\n' "${PKG_SIGNING_KEY}" > "${KEYFILE}"

/usr/local/sbin/pkg repo "${OUT}" "${KEYFILE}"

rm -f "${KEYFILE}"
KEYFILE=""

for f in meta.conf packagesite.pkg data.pkg; do
	if [ ! -s "${OUT}/${f}" ]; then
		echo "Manquant après pkg repo : ${OUT}/${f}" >&2
		exit 1
	fi
done
echo "Dépôt signé : ${PKG_ABI}"
