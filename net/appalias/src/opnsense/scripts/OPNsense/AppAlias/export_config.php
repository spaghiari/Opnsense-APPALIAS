#!/usr/local/bin/php
<?php

/*
 * Copyright (C) Les contributeurs d'os-appalias
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

use OPNsense\AppAlias\AppAlias;

const APPALIAS_DIR = '/var/db/appalias';
const APPALIAS_CONFIG = '/var/db/appalias/config.json';

$mdl = new AppAlias();
$gen = $mdl->general;

$out = [
    'version' => 1,
    'general' => [
        'enabled' => (string)$gen->enabled == '1',
        'listen_address' => (string)$gen->listen_address,
        'listen_port' => (int)(string)$gen->listen_port,
        'upstream_address' => (string)$gen->upstream_address,
        'upstream_port' => (int)(string)$gen->upstream_port,
        'upstream_fallback' => AppAlias::splitList($gen->upstream_fallback),
        'follow_cname' => (string)$gen->follow_cname == '1',
        'ttl_min' => (int)(string)$gen->ttl_min,
        'ttl_margin' => (int)(string)$gen->ttl_margin,
        'max_entries_per_app' => (int)(string)$gen->max_entries_per_app,
        'exclude_networks' => AppAlias::splitList($gen->exclude_networks),
        'alias_prefix' => (string)$gen->alias_prefix,
        'loglevel' => (string)$gen->loglevel,
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
    ];
}

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
