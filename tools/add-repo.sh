#!/bin/sh
#
# Déclare le dépôt pkg d'os-appalias sur un OPNsense : télécharge la clé
# publique (vérifiée par son empreinte) et écrit la configuration du dépôt. N'installe rien d'autre : le plugin
# s'installe ensuite depuis System > Firmware > Plugins.
#
# À lancer en root, avec « sh » (le shell root d'OPNsense est tcsh) :
#   sh add-repo.sh        # ajoute le dépôt
#   sh add-repo.sh -u     # retire la clé et la configuration du dépôt
#
# La clé téléchargée est refusée si son empreinte SHA256 diffère de
# EXPECTED_SHA256 (à garder identique à celle du README).

set -eu

BASE_URL="https://spaghiari.github.io/Opnsense-APPALIAS"
EXPECTED_SHA256="325cde334cd45a513bbf8598d51627c991b78bc20379d5648fc4a499ac40a4b3"
KEY_FILE="/usr/local/etc/pkg/keys/appalias.pub"
CONF_FILE="/usr/local/etc/pkg/repos/appalias.conf"

if [ "$(id -u)" -ne 0 ]; then
	echo "À lancer en root." >&2
	exit 1
fi

if [ "${1:-}" = "-u" ]; then
	rm -f "${KEY_FILE}" "${CONF_FILE}"
	echo "Dépôt appalias retiré (${KEY_FILE}, ${CONF_FILE})."
	exit 0
fi

if [ "$#" -ne 0 ]; then
	echo "Usage : sh $0 [-u]" >&2
	exit 64
fi

ABI=$(pkg config ABI)
case "${ABI}" in
FreeBSD:14:amd64 | FreeBSD:15:amd64) ;;
*)
	echo "ABI non prise en charge : ${ABI} (attendu FreeBSD:14:amd64 ou FreeBSD:15:amd64)." >&2
	exit 1
	;;
esac

TMP=$(mktemp -d /tmp/appalias-repo.XXXXXX)
trap 'rm -rf "${TMP}"' EXIT

fetch -q -o "${TMP}/appalias.pub" "${BASE_URL}/appalias.pub"

ACTUAL=$(sha256 -q "${TMP}/appalias.pub")
echo "Empreinte SHA256 de la clé publique : ${ACTUAL}"
if [ "${ACTUAL}" != "${EXPECTED_SHA256}" ]; then
	echo "Empreinte différente de celle attendue (${EXPECTED_SHA256}) : clé refusée, rien installé." >&2
	exit 1
fi

mkdir -p "$(dirname "${KEY_FILE}")" "$(dirname "${CONF_FILE}")"
cp "${TMP}/appalias.pub" "${KEY_FILE}"
# Configuration embarquée (identique à repo/appalias.conf, contrôlé par la CI) :
# elle n'est pas téléchargée, pour qu'un fichier modifié sur le site ne puisse
# pas désactiver la vérification de signature.
cat > "${CONF_FILE}" <<'APPALIAS_CONF'
appalias: {
  url: "https://spaghiari.github.io/Opnsense-APPALIAS/${ABI}",
  signature_type: "pubkey",
  pubkey: "/usr/local/etc/pkg/keys/appalias.pub",
  enabled: yes
}
APPALIAS_CONF
chmod 0644 "${KEY_FILE}" "${CONF_FILE}"

if ! pkg update -f -r appalias; then
	rm -f "${KEY_FILE}" "${CONF_FILE}"
	echo "pkg update a échoué : clé et configuration retirées." >&2
	exit 1
fi

echo
echo "Dépôt ajouté. Aller dans System > Firmware > Plugins et cliquer sur"
echo "« Check for updates » : os-appalias apparaît dans la liste."
