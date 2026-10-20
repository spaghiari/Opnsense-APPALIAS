#!/usr/local/bin/php
<?php

/*
 * Copyright (C) 2026 spaghiari
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

/*
 * Exporte le modèle OPNsense/AppAlias vers /var/db/appalias/config.json,
 * lu par le démon appaliasd (lib/config.py). Écriture atomique.
 */

require_once('script/load_phalcon.php');
require_once('interfaces.lib.inc');

use OPNsense\AppAlias\AppAlias;
use OPNsense\Core\Config;

const APPALIAS_DIR = '/var/db/appalias';
const APPALIAS_CONFIG = '/var/db/appalias/config.json';

// Ident « appalias » pour que les messages aboutissent dans le journal dédié.
openlog('appalias', LOG_PID, LOG_DAEMON);

$mdl = new AppAlias();
$gen = $mdl->general;

// Champ vide -> null : lib/config.py applique alors sa valeur par défaut.
$intOrNull = function ($field) {
    $value = trim((string)$field);
    return $value === '' ? null : (int)$value;
};

$out = [
    'version' => 1,
    'general' => [
        'enabled' => (string)$gen->enabled == '1',
        'listen_address' => (string)$gen->listen_address,
        'listen_port' => (int)(string)$gen->listen_port,
        'upstream_address' => (string)$gen->upstream_address,
        'upstream_port' => (int)(string)$gen->upstream_port,
        // valeur vide (config 0.3) = DNS clair
        'upstream_protocol' => (string)$gen->upstream_protocol !== '' ? (string)$gen->upstream_protocol : 'dns',
        'upstream_tls_name' => trim((string)$gen->upstream_tls_name),
        'upstream_fallback' => AppAlias::splitList($gen->upstream_fallback),
        'follow_cname' => (string)$gen->follow_cname == '1',
        'ttl_min' => (int)(string)$gen->ttl_min,
        'ttl_margin' => (int)(string)$gen->ttl_margin,
        'max_entries_per_app' => (int)(string)$gen->max_entries_per_app,
        'exclude_networks' => AppAlias::splitList($gen->exclude_networks),
        'alias_prefix' => (string)$gen->alias_prefix,
        'loglevel' => (string)$gen->loglevel,
        'shared_platforms_extra' => AppAlias::splitList($gen->shared_platforms_extra),
        // opt-in : valeur vide (config 0.2) = désactivé
        'unbound_autoforward' => (string)$gen->unbound_autoforward == '1',
        // résolution proactive : activée par défaut (valeur vide = défaut côté démon)
        'proactive_enabled' => (string)$gen->proactive_enabled !== '0',
        'proactive_interval_min' => $intOrNull($gen->proactive_interval_min),
        'proactive_interval_max' => $intOrNull($gen->proactive_interval_max),
        'proactive_prefixes' => AppAlias::splitList($gen->proactive_prefixes),
        // mise à jour en ligne du catalogue : activée par défaut
        'catalog_online_update' => (string)$gen->catalog_online_update !== '0',
        // relais ouvert au LAN (0.9) : cache, réseaux clients autorisés, limite par client
        'cache_enabled' => (string)$gen->cache_enabled !== '0',
        'client_networks' => AppAlias::splitList($gen->client_networks),
        'client_rate_limit' => $intOrNull($gen->client_rate_limit),
        // backend pf : valeur vide = automatique (ioctl, repli pfctl)
        'pf_backend' => (string)$gen->pf_backend !== '' ? (string)$gen->pf_backend : 'auto',
        // sources ASN (RIPEstat) : activées par défaut
        'asn_enabled' => (string)$gen->asn_enabled !== '0',
    ],
    'apps' => [],
];

foreach ($mdl->apps->app->iterateItems() as $uuid => $app) {
    $out['apps'][] = [
        'uuid' => (string)$uuid,
        'enabled' => (string)$app->enabled == '1',
        'name' => (string)$app->name,
        'description' => (string)$app->description,
        'origin' => (string)$app->origin,
        'domains' => AppAlias::splitList($app->domains),
        'asns' => AppAlias::splitList($app->asns),
        'networks' => AppAlias::splitList($app->networks),
        'families' => (string)$app->families,
        'alias_uuid' => (string)$app->alias_uuid,
        'catalog_refs' => AppAlias::splitList($app->catalog_refs),
        'proactive' => (string)$app->proactive !== '0',
    ];
}

/*
 * Configuration Unbound d'OPNsense (OPNsense.unboundplus, modèle
 * OPNsense\Unbound\Unbound) utile au dédoublonnage des zones de transfert
 * (lib/unboundfwd.py) : entrées dots (Query Forwarding et DNS over TLS),
 * noms locaux (domaine du système, host overrides, alias, anciens overrides),
 * adresses du pare-feu (détection de boucle sur l'amont du relais) et
 * « Use System Nameservers » (forwarding.enabled, transfert « . »).
 */
$unbound = [
    'enabled' => false,
    'port' => 53,
    'forwarding' => false,
    'dots' => [],
    'local_domains' => [],
    'local_addresses' => [],
];

/* Adresses IPv4 et IPv6 de toutes les interfaces, VIP comprises (méthode du core). */
foreach (legacy_interfaces_details() as $ifname => $ifdata) {
    foreach (['ipv4', 'ipv6'] as $proto) {
        foreach ($ifdata[$proto] ?? [] as $address) {
            if (!empty($address['ipaddr'])) {
                $unbound['local_addresses'][] = (string)$address['ipaddr'];
            }
        }
    }
}
$unbound['local_addresses'] = array_values(array_unique($unbound['local_addresses']));
$cfg = Config::getInstance()->object();
if (isset($cfg->system->domain)) {
    $unbound['local_domains'][] = (string)$cfg->system->domain;
}
if (isset($cfg->OPNsense->unboundplus)) {
    $ub = $cfg->OPNsense->unboundplus;
    $unbound['enabled'] = isset($ub->general->enabled) && (string)$ub->general->enabled == '1';
    if (isset($ub->general->port) && (string)$ub->general->port !== '') {
        $unbound['port'] = (int)(string)$ub->general->port;
    }
    $unbound['forwarding'] = isset($ub->forwarding->enabled) && (string)$ub->forwarding->enabled == '1';
    if (isset($ub->dots->dot)) {
        foreach ($ub->dots->dot as $dot) {
            $unbound['dots'][] = [
                'domain' => (string)$dot->domain,
                'type' => (string)$dot->type,
                'enabled' => (string)$dot->enabled == '1',
            ];
        }
    }
    foreach (['hosts' => 'host', 'aliases' => 'alias'] as $section => $item) {
        if (isset($ub->$section->$item)) {
            foreach ($ub->$section->$item as $entry) {
                $host = trim((string)$entry->hostname);
                $domain = trim((string)$entry->domain);
                if ($domain !== '') {
                    $unbound['local_domains'][] = $host !== '' ? $host . '.' . $domain : $domain;
                }
            }
        }
    }
    if (isset($ub->domains->domain)) {
        foreach ($ub->domains->domain as $entry) {
            if (trim((string)$entry->domain) !== '') {
                $unbound['local_domains'][] = (string)$entry->domain;
            }
        }
    }
}
$out['unbound'] = $unbound;

if (!is_dir(APPALIAS_DIR)) {
    mkdir(APPALIAS_DIR, 0750, true);
}

$json = json_encode($out, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
if ($json === false) {
    syslog(LOG_ERR, 'appalias: export impossible : ' . json_last_error_msg());
    exit(1);
}

$tmp = tempnam(APPALIAS_DIR, '.config.');
if ($tmp === false || file_put_contents($tmp, $json . "\n") === false) {
    syslog(LOG_ERR, 'appalias: écriture impossible dans ' . APPALIAS_DIR);
    exit(1);
}
chmod($tmp, 0640);
if (!rename($tmp, APPALIAS_CONFIG)) {
    @unlink($tmp);
    syslog(LOG_ERR, 'appalias: rename impossible vers ' . APPALIAS_CONFIG);
    exit(1);
}

echo "OK\n";
