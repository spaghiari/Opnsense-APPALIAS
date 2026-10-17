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
 * Points d'accroche os-appalias pour le système de plugins OPNsense.
 * Le plugin ne crée aucune règle : il garantit seulement l'existence des
 * alias External gérés (APP_*) que l'utilisateur emploie dans ses règles.
 */

define('APPALIAS_SCRIPTS', '/usr/local/opnsense/scripts/OPNsense/AppAlias');

function appalias_enabled()
{
    $mdl = new \OPNsense\AppAlias\AppAlias();
    return (string)$mdl->general->enabled == '1';
}

/**
 * Service affiché dans le tableau de bord et dans la liste des services.
 */
function appalias_services()
{
    $services = [];

    if (appalias_enabled()) {
        $services[] = [
            'description' => gettext('AppAlias (relais DNS)'),
            'configd' => [
                'restart' => ['appalias restart'],
                'start' => ['appalias start'],
                'stop' => ['appalias stop'],
            ],
            'name' => 'appaliasd',
            'pidfile' => '/var/run/appaliasd.pid',
        ];
    }

    return $services;
}

/**
 * Événements : démarrage du système et événement « appalias ».
 */
function appalias_configure()
{
    return [
        'bootup' => ['appalias_configure_do'],
        'appalias' => ['appalias_configure_do'],
    ];
}

/**
 * Export de config.json puis (re)démarrage du démon si le plugin est activé.
 */
function appalias_configure_do($verbose = false)
{
    $enabled = appalias_enabled();

    if (function_exists('service_log')) {
        service_log('Configuration d\'AppAlias...', $verbose);
    }

    mwexecf(APPALIAS_SCRIPTS . '/export_config.php');

    if ($enabled) {
        mwexecf('/usr/local/etc/rc.d/appaliasd onerestart');
    } else {
        mwexecf('/usr/local/etc/rc.d/appaliasd onestop');
    }

    if (function_exists('service_log')) {
        service_log("terminé.\n", $verbose);
    }
}

/**
 * Ajoute l'alias External s'il n'existe pas (idiome os-crowdsec), sans
 * enregistrer : l'appelant fait un seul Config::save() pour tout le lot.
 * Ne modifie jamais un alias existant.
 */
function appalias_add_alias_if_not_exist($model, $name, $description)
{
    if ($model->getByName($name) != null) {
        return false;
    }

    $alias = $model->aliases->alias->Add();
    $alias->enabled = '1';
    $alias->name = $name;
    $alias->type = 'external';
    $alias->description = $description;
    $alias->content = '';

    return true;
}

/**
 * Appelé à chaque génération du jeu de règles : recrée les alias gérés
 * manquants, en un seul enregistrement. Aucune règle n'est ajoutée à $fw.
 */
function appalias_firewall($fw)
{
    if (!appalias_enabled()) {
        return;
    }

    $mdl = new \OPNsense\AppAlias\AppAlias();
    $prefix = (string)$mdl->general->alias_prefix;
    $aliasModel = new \OPNsense\Firewall\Alias();
    $created = [];

    foreach ($mdl->apps->app->iterateItems() as $app) {
        $name = $prefix . (string)$app->name;
        if (!preg_match('/^[A-Za-z0-9_]{1,31}$/', $name)) {
            continue;
        }
        if (appalias_add_alias_if_not_exist($aliasModel, $name, \OPNsense\AppAlias\AppAlias::ALIAS_DESCRIPTION)) {
            $created[] = $name;
        }
    }

    if (count($created) > 0) {
        $aliasModel->serializeToConfig();
        \OPNsense\Core\Config::getInstance()->save();
        syslog(LOG_NOTICE, sprintf('appalias: alias recréés : %s', implode(' ', $created)));
    }
}
