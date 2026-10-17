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
 * Synchronise les alias Firewall gérés avec les applications d'AppAlias.
 *
 * - crée ou met à jour un alias « external » par application, avec la
 *   description marquée, et mémorise son uuid dans app.alias_uuid ;
 * - un alias géré dont l'application a disparu est supprimé, sauf s'il est
 *   encore référencé ailleurs dans config.xml : il est alors seulement vidé
 *   (pfctl -T flush) et l'événement est journalisé ;
 * - un alias homonyme non géré n'est jamais modifié ;
 * - le renommage d'une application dont l'alias est référencé est refusé
 *   en amont, par la validation du modèle (AppAlias::performValidation) ;
 * - lance ensuite « filter reload » via configd (option --no-reload pour l'éviter).
 */

require_once('script/load_phalcon.php');

use OPNsense\AppAlias\AppAlias;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;
use OPNsense\Firewall\Alias;

const PFCTL = '/sbin/pfctl';

openlog('appalias', LOG_PID, LOG_DAEMON);

function appalias_log($prio, $msg)
{
    syslog($prio, $msg);
    echo $msg . "\n";
}

function appalias_flush_table($name)
{
    if (!preg_match('/^[A-Za-z0-9_]{1,31}$/', $name)) {
        return;
    }
    exec(PFCTL . ' -t ' . escapeshellarg($name) . ' -T flush 2>&1', $output, $rc);
    if ($rc != 0) {
        appalias_log(LOG_WARNING, "appalias: vidage de la table $name impossible : " . implode(' ', $output));
    }
}

$noReload = in_array('--no-reload', $argv ?? [], true);

$mdl = new AppAlias();
$aliasMdl = new Alias();
$prefix = (string)$mdl->general->alias_prefix;

/* Alias existants par nom. */
$existing = [];
foreach ($aliasMdl->aliases->alias->iterateItems() as $uuid => $alias) {
    $existing[(string)$alias->name] = ['uuid' => (string)$uuid, 'node' => $alias];
}

$desired = [];
$aliasChanged = false;
$appChanged = false;

foreach ($mdl->apps->app->iterateItems() as $appUuid => $app) {
    $table = $prefix . (string)$app->name;
    if (!preg_match('/^[A-Za-z0-9_]{1,31}$/', $table)) {
        appalias_log(LOG_WARNING, "appalias: nom d'alias invalide ignoré : $table");
        continue;
    }
    $desired[$table] = true;

    if (isset($existing[$table])) {
        $node = $existing[$table]['node'];
        $aliasUuid = $existing[$table]['uuid'];
        if (strpos((string)$node->description, AppAlias::MARKER) !== 0) {
            appalias_log(LOG_ERR, "appalias: l'alias $table existe et n'est pas géré : il n'est pas modifié");
            continue;
        }
        if ((string)$node->type !== 'external') {
            $node->type = 'external';
            $node->content = '';
            $aliasChanged = true;
        }
        if ((string)$node->description !== AppAlias::ALIAS_DESCRIPTION) {
            $node->description = AppAlias::ALIAS_DESCRIPTION;
            $aliasChanged = true;
        }
        if ((string)$node->enabled !== '1') {
            $node->enabled = '1';
            $aliasChanged = true;
        }
    } else {
        $node = $aliasMdl->aliases->alias->Add();
        $node->enabled = '1';
        $node->name = $table;
        $node->type = 'external';
        $node->description = AppAlias::ALIAS_DESCRIPTION;
        $node->content = '';
        $aliasUuid = $node->getAttribute('uuid');
        $existing[$table] = ['uuid' => $aliasUuid, 'node' => $node];
        $aliasChanged = true;
        appalias_log(LOG_NOTICE, "appalias: alias $table créé");
    }

    if ((string)$app->alias_uuid !== (string)$aliasUuid) {
        $app->alias_uuid = (string)$aliasUuid;
        $appChanged = true;
    }
}

/* Alias gérés orphelins. */
$configText = null;
foreach ($existing as $name => $info) {
    if (isset($desired[$name]) || strpos((string)$info['node']->description, AppAlias::MARKER) !== 0) {
        continue;
    }
    if ($configText === null) {
        $configText = AppAlias::configTextWithoutAliases();
    }
    if (AppAlias::isAliasReferenced($name, $configText)) {
        appalias_flush_table($name);
        appalias_log(
            LOG_WARNING,
            "appalias: l'alias $name n'a plus d'application mais reste référencé : vidé, pas supprimé"
        );
        continue;
    }
    $aliasMdl->aliases->alias->del($info['uuid']);
    $aliasChanged = true;
    appalias_log(LOG_NOTICE, "appalias: alias orphelin $name supprimé");
}

if ($aliasChanged || $appChanged) {
    if ($aliasChanged) {
        $messages = $aliasMdl->performValidation();
        if (count($messages) > 0) {
            foreach ($messages as $message) {
                appalias_log(LOG_ERR, 'appalias: alias refusé : ' . $message->getField() . ' : ' . $message->getMessage());
            }
            exit(1);
        }
        $aliasMdl->serializeToConfig();
    }
    if ($appChanged) {
        $mdl->serializeToConfig(false, true);
    }
    Config::getInstance()->save();
}

if (!$noReload) {
    (new Backend())->configdRun('filter reload');
}

echo "OK\n";
