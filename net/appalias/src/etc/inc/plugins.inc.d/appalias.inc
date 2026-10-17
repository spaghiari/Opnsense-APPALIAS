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
            'description' => gettext('AppAlias (DNS relay)'),
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
 * Mise à jour quotidienne du catalogue v2fly (activée par défaut) : une tâche
 * cron seulement si le téléchargement en ligne est activé et la fréquence
 * réglée sur « daily ». Minute et heure (entre 1 h et 4 h 59) dérivées du nom
 * d'hôte, pour que les pare-feu n'appellent pas GitHub tous à la même heure.
 * Le script prend un verrou et ne remplace jamais le catalogue sans contrôle
 * de cohérence. Rien n'est appliqué aux applications : les nouveautés passent
 * par le diff.
 */
function appalias_cron()
{
    $jobs = [];
    $mdl = new \OPNsense\AppAlias\AppAlias();
    if (
        (string)$mdl->general->catalog_online_update == '1' &&
        (string)$mdl->general->catalog_update_schedule == 'daily'
    ) {
        $hash = crc32((string)gethostname());
        $jobs[]['autocron'] = [
            '/usr/local/sbin/configctl -d appalias catalog_update',
            (string)($hash % 60),
            (string)(1 + intdiv($hash, 60) % 4),
        ];
    }
    return $jobs;
}

/**
 * Événements : démarrage du système, événement « appalias », et démarrage
 * ou reconfiguration d'Unbound (« dns », « unbound_start » du core). Les
 * plugins sont parcourus par ordre alphabétique (plugins_scan) : appalias
 * régénère son fichier avant que unbound_configure_do() lance start.sh, qui
 * le recopie. Jamais de redémarrage d'Unbound ici.
 */
function appalias_configure()
{
    return [
        'bootup' => ['appalias_configure_boot'],
        'appalias' => ['appalias_configure_do'],
        'dns' => ['appalias_configure_unbound'],
        'unbound_start' => ['appalias_configure_unbound'],
    ];
}

/**
 * Export de config.json puis écriture des zones de transfert d'Unbound
 * (sans redémarrage : Unbound est en train de démarrer).
 */
function appalias_configure_unbound($verbose = false)
{
    mwexecf(APPALIAS_SCRIPTS . '/export_config.php');
    mwexecf(APPALIAS_SCRIPTS . '/unbound_forward.py');
}

/**
 * Application « appalias » proposée dans la liste des applications des
 * destinations syslog distantes (Système > Journaux > Destinations distantes).
 * Le fichier local /var/log/appalias/appalias_<date>.log ne vient pas d'ici :
 * il vient du template OPNsense/Syslog/local/appalias.conf (filtre
 * program("appalias")), affiché dans /ui/diagnostics/log/core/appalias.
 * Même forme que dnsmasq_syslog() et ntpd_syslog() du core 26.7 :
 * clé = nom du journal, 'facility' = liste d'identifiants syslog (ident).
 */
function appalias_syslog()
{
    $logfacilities = [];
    $logfacilities['appalias'] = [
        'facility' => ['appalias'],
    ];

    return $logfacilities;
}

/**
 * Au démarrage : rc.d démarre déjà le démon (appaliasd_enable=YES) et exporte
 * config.json avant de le lancer. On réexporte seulement, et on envoie un
 * rechargement (SIGHUP) si, et seulement si, le démon tourne déjà.
 */
function appalias_configure_boot($verbose = false)
{
    mwexecf(APPALIAS_SCRIPTS . '/export_config.php');
    mwexecf(APPALIAS_SCRIPTS . '/unbound_forward.py');
    if (appalias_enabled() && mwexecf('/usr/local/etc/rc.d/appaliasd onestatus', [], true) == 0) {
        mwexecf('/usr/local/etc/rc.d/appaliasd onereload');
    }
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
    mwexecf(APPALIAS_SCRIPTS . '/unbound_forward.py');

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
    $created = appalias_add_missing($mdl, $aliasModel, $prefix);

    if (count($created) == 0) {
        return;
    }

    // Détection rapide ci-dessus (hors verrou) : on reconstruit tout sous verrou.
    $cfgObj = \OPNsense\Core\Config::getInstance();
    $cfgObj->lock();
    try {
        $mdl = new \OPNsense\AppAlias\AppAlias();
        $prefix = (string)$mdl->general->alias_prefix;
        $aliasModel = new \OPNsense\Firewall\Alias();
        $created = appalias_add_missing($mdl, $aliasModel, $prefix);
        if (count($created) > 0) {
            $aliasModel->serializeToConfig();
            $cfgObj->save();
        }
    } finally {
        $cfgObj->unlock();
    }
    if (count($created) > 0) {
        // Pas d'openlog : l'ident syslog du processus du core reste inchangé.
        syslog(LOG_NOTICE, sprintf('appalias: alias recréés : %s', implode(' ', $created)));
    }
}

/**
 * Ajoute au modèle les alias gérés manquants ; renvoie les noms ajoutés.
 */
function appalias_add_missing($mdl, $aliasModel, $prefix)
{
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

    return $created;
}
