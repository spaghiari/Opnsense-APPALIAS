#!/bin/sh
#
# Phase 0 d'os-appalias : vérifications manuelles sur une box OPNsense.
#
# Toutes les étapes sont en lecture seule ou réversibles. Rien n'est créé
# dans la configuration par ce script : les alias de test se créent à la
# main dans l'interface, quand le script le demande. Chaque action sur pf
# est affichée et confirmée avant d'être lancée. Le nettoyage est fait à
# la fin (entrées de test retirées des tables).
#
# Usage (en root) :
#   sh tools/phase0.sh
#
# Variables facultatives :
#   T1=APPALIAS_T1       alias External de test (créé à la main)
#   G1=APPALIAS_G1       alias « Network group » de test contenant T1 (créé à la main)
#   TEST_DOMAIN=example.com
#   RELAY_PORT=53535
#
# Adresses de test : plages de documentation RFC 5737 (192.0.2.0/24,
# 198.51.100.0/24), jamais routées.

set -u

T1="${T1:-APPALIAS_T1}"
G1="${G1:-APPALIAS_G1}"
TEST_DOMAIN="${TEST_DOMAIN:-example.com}"
RELAY_PORT="${RELAY_PORT:-53535}"
PFCTL=/sbin/pfctl
RESULTS=""

say()  { printf '\n=== %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
result() { RESULTS="${RESULTS}
  $1 : $2"; printf '    -> %s\n' "$2"; }

confirm()
{
	printf '    Lancer : %s ? [o/N] ' "$*"
	read -r _ans
	case "${_ans}" in
	o|O|y|Y) return 0 ;;
	*) info "étape ignorée"; return 1 ;;
	esac
}

pause()
{
	printf '    %s [Entrée pour continuer] ' "$*"
	read -r _ans
}

table_has()
{
	"${PFCTL}" -t "$1" -T show 2>/dev/null | awk '{print $1}' | grep -qx "$2"
}

if [ "$(id -u)" -ne 0 ]; then
	echo "À lancer en root." >&2
	exit 1
fi

say "0. Contexte (lecture seule)"
info "version : $(opnsense-version 2>/dev/null || echo inconnue)"
info "pfctl   : $(ls -l ${PFCTL} 2>/dev/null || echo absent)"
info "python3 : $(/usr/local/bin/python3 -V 2>&1)"

# ---------------------------------------------------------------------------
say "1. Un alias External garde son contenu après « filter reload »"
info "À faire dans l'interface : Pare-feu > Alias, créer l'alias ${T1}"
info "de type « External (advanced) », sans contenu. Appliquer."
pause "Alias ${T1} créé ?"
if ! "${PFCTL}" -t "${T1}" -T show >/dev/null 2>&1; then
	result "T1 survit au reload" "ÉCHEC : table ${T1} absente (alias non créé ?)"
else
	if confirm "${PFCTL} -t ${T1} -T add 192.0.2.1"; then
		"${PFCTL}" -t "${T1}" -T add 192.0.2.1
		info "contenu avant : $("${PFCTL}" -t "${T1}" -T show | tr -s ' \n' ' ')"
		if confirm "configctl filter reload"; then
			configctl filter reload
			sleep 3
			info "contenu après : $("${PFCTL}" -t "${T1}" -T show | tr -s ' \n' ' ')"
			if table_has "${T1}" 192.0.2.1; then
				result "T1 survit au reload" "OK"
			else
				result "T1 survit au reload" "ÉCHEC : 192.0.2.1 perdu après reload"
			fi
		fi
	fi
fi

# ---------------------------------------------------------------------------
say "2. Latence de 100 appels « pfctl -T add »"
info "Ajoute 198.51.100.1 à 198.51.100.100 dans ${T1}, un appel par IP, puis les retire."
if "${PFCTL}" -t "${T1}" -T show >/dev/null 2>&1 && confirm "100 x ${PFCTL} -t ${T1} -T add 198.51.100.N"; then
	/usr/local/bin/python3 - "${PFCTL}" "${T1}" <<'EOF'
import subprocess, sys, time
pfctl, table = sys.argv[1], sys.argv[2]
times = []
for i in range(1, 101):
    t0 = time.perf_counter()
    subprocess.run([pfctl, "-t", table, "-T", "add", "198.51.100.%d" % i],
                   capture_output=True, check=False)
    times.append((time.perf_counter() - t0) * 1000)
times.sort()
print("    moyenne %.1f ms, médiane %.1f ms, p95 %.1f ms, max %.1f ms" % (
    sum(times) / len(times), times[49], times[94], times[-1]))
EOF
	result "latence pfctl add" "mesurée (voir ci-dessus)"
fi

# ---------------------------------------------------------------------------
say "3. Un External imbriqué dans un « Network group »"
info "À faire dans l'interface : créer l'alias ${G1} de type « Network group »"
info "contenant ${T1}. Appliquer."
pause "Alias ${G1} créé ?"
if "${PFCTL}" -t "${G1}" -T show >/dev/null 2>&1; then
	if confirm "${PFCTL} -t ${T1} -T add 192.0.2.77 puis configctl filter refresh_aliases"; then
		"${PFCTL}" -t "${T1}" -T add 192.0.2.77
		configctl filter refresh_aliases >/dev/null 2>&1
		sleep 3
		info "contenu de ${G1} : $("${PFCTL}" -t "${G1}" -T show | tr -s ' \n' ' ')"
		if table_has "${G1}" 192.0.2.77; then
			result "External dans networkgroup" "OK : les ajouts dynamiques se propagent"
		else
			result "External dans networkgroup" "NON : le groupe ne suit pas les ajouts dynamiques (utiliser APP_* directement dans les règles)"
		fi
	fi
else
	result "External dans networkgroup" "non testé (table ${G1} absente)"
fi

# ---------------------------------------------------------------------------
say "4. Transmission d'Unbound vers le relais (lecture seule)"
if grep -rqs "127.0.0.1@${RELAY_PORT}" /var/unbound/ 2>/dev/null; then
	info "forward vers 127.0.0.1@${RELAY_PORT} présent dans la config Unbound"
	grep -rhs "do-not-query-localhost" /var/unbound/ 2>/dev/null | sed 's/^/    unbound : /'
	result "forward Unbound (config)" "OK"
else
	result "forward Unbound (config)" "absent : Services > Unbound DNS > Query Forwarding, serveur 127.0.0.1 port ${RELAY_PORT}"
fi
if command -v drill >/dev/null 2>&1; then
	info "relais direct : $(drill -p "${RELAY_PORT}" "${TEST_DOMAIN}" @127.0.0.1 2>&1 | grep -E 'rcode|Query time' | tr -s ' \n' ' ')"
	info "via Unbound   : $(drill "${TEST_DOMAIN}" @127.0.0.1 2>&1 | grep -E 'rcode|Query time' | tr -s ' \n' ' ')"
	result "requêtes drill" "voir ci-dessus (le relais doit tourner pour la première)"
fi

# ---------------------------------------------------------------------------
say "5. Nettoyage"
if "${PFCTL}" -t "${T1}" -T show >/dev/null 2>&1 && confirm "retirer les entrées de test de ${T1}"; then
	_list="192.0.2.1 192.0.2.77"
	_i=1
	while [ ${_i} -le 100 ]; do
		_list="${_list} 198.51.100.${_i}"
		_i=$((_i + 1))
	done
	# shellcheck disable=SC2086
	"${PFCTL}" -t "${T1}" -T delete ${_list} 2>&1 | sed 's/^/    /'
fi
info "À faire dans l'interface : supprimer les alias ${G1} puis ${T1}, puis Appliquer."

say "Résumé"
printf '%s\n' "${RESULTS}"
