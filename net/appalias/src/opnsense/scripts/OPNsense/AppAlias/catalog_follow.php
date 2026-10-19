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
 * Suivi automatique du catalogue v2fly (applications avec catalog_follow=1).
 *
 *   catalog_follow.php run          applique les écarts du catalogue en usage
 *   catalog_follow.php undo <id>    annule une entrée du journal
 *
 * Lancé après chaque mise à jour réussie du catalogue (catalog_update.py :
 * cron horaire par défaut, POST_INSTALL, bouton « Update catalog now »), après une
 * mise à jour du paquet (+POST_INSTALL : catalogue embarqué plus récent) et
 * par l'action configd « appalias catalog_follow_undo ».
 *
 * Le calcul (ajouts, retraits, plafond, inverse d'une entrée) et le journal
 * sont dans lib/follow.py (catalog_follow.py, JSON sur stdin et stdout) ; ce
 * script garde le verrou de config.xml et passe par le modèle et ses
 * validations, application par application : une application refusée
 * (validation, plafond, liste du catalogue disparue) n'est pas enregistrée
 * et l'erreur est journalisée ; les autres le sont. Puis, si le plugin est
 * activé seulement, un enchaînement plus étroit que « Apply » : export, zones
 * d'Unbound (redémarré seulement si la liste des zones change) et
 * rechargement du relais s'il tourne. Pas de synchronisation des alias (le
 * suivi ne crée ni ne supprime d'alias : pas de « filter reload »). Plugin
 * désactivé : config.xml est enregistré et journalisé (annulable) mais rien
 * n'est appliqué, l'application est différée. Sortie : un JSON.
 */

require_once('script/load_phalcon.php');

use OPNsense\AppAlias\AppAlias;
use OPNsense\Core\Config;

const APPALIAS_SCRIPTS = '/usr/local/opnsense/scripts/OPNsense/AppAlias';
const APPALIAS_PYTHON = '/usr/local/bin/python3';
const APPALIAS_RC = '/usr/local/etc/rc.d/appaliasd';
const APPALIAS_RUN_LOCK = '/var/db/appalias/catalog_follow.run.lock';
const APPALIAS_MAX_APP_RULES = 1000;
const APPALIAS_ENTRY_PATTERN = '/^[0-9a-f]{16}$/';

// Ident « appalias » pour que les messages aboutissent dans le journal dédié.
openlog('appalias', LOG_PID, LOG_DAEMON);

function appalias_out($data)
{
    echo json_encode($data, JSON_UNESCAPED_SLASHES) . "\n";
}

/**
 * catalog_follow.py <commande>, entrée JSON sur stdin ; JSON décodé ou
 * ['error' => …]. Le script Python lit toute son entrée avant d'écrire :
 * pas d'interblocage des tubes.
 */
function appalias_follow_py($command, $input)
{
    $spec = [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['file', '/dev/null', 'w']];
    $proc = proc_open([APPALIAS_PYTHON, APPALIAS_SCRIPTS . '/catalog_follow.py', $command], $spec, $pipes);
    if (!is_resource($proc)) {
        return ['error' => 'catalog_follow.py could not be started'];
    }
    fwrite($pipes[0], json_encode($input, JSON_UNESCAPED_SLASHES));
    fclose($pipes[0]);
    $raw = stream_get_contents($pipes[1]);
    fclose($pipes[1]);
    proc_close($proc);
    $data = json_decode(trim((string)$raw), true);
    return is_array($data) ? $data : ['error' => 'unreadable answer from catalog_follow.py'];
}

/** Liste de chaînes reçue de catalog_follow.py, ou null si elle est invalide. */
function appalias_strings($value)
{
    if (!is_array($value)) {
        return null;
    }
    foreach ($value as $item) {
        if (!is_string($item) || strpos($item, ',') !== false) {
            return null;
        }
    }
    return array_values($value);
}

/** Messages de validation du modèle, en une ligne (vide si aucun). */
function appalias_validation_text($mdl)
{
    $parts = [];
    foreach ($mdl->performValidation() as $msg) {
        $parts[] = $msg->getField() . ': ' . $msg->getMessage();
    }
    return implode('; ', $parts);
}

/** Le plugin est-il activé (general.enabled) ? Lu dans la configuration courante. */
function appalias_enabled()
{
    return (string)(new AppAlias())->general->enabled === '1';
}

/**
 * Application du suivi, plus étroite que « Apply »
 * (ServiceController::reconfigureAction) : export, zones d'Unbound puis
 * rechargement du relais. Pas de synchronisation des alias (sync_aliases.php,
 * qui entraîne un « filter reload ») : le suivi ne crée ni ne supprime jamais
 * d'alias et ne touche pas aux champs que cette étape lit (nom, alias_uuid).
 * Pas de template ni de cron : aucun réglage n'a changé. Renvoie null, ou le
 * nom de l'étape en échec. Le relais est rechargé s'il tourne ; arrêté, il
 * relira config.json à son prochain démarrage (pas de démarrage automatique).
 * Ne doit être appelée que si le plugin est activé (cf. appalias_run et
 * appalias_undo).
 */
function appalias_apply()
{
    $steps = [
        'export' => [APPALIAS_SCRIPTS . '/export_config.php'],
        'unbound' => [APPALIAS_SCRIPTS . '/unbound_forward.py', '--apply'],
    ];
    foreach ($steps as $step => $cmd) {
        $output = [];
        exec(implode(' ', array_map('escapeshellarg', $cmd)) . ' 2>&1', $output, $rc);
        if ($rc != 0) {
            syslog(LOG_ERR, sprintf(
                'appalias: suivi du catalogue : étape %s en échec : %s',
                $step,
                implode(' ', $output)
            ));
            return $step;
        }
    }
    if (!appalias_enabled()) {
        return null;
    }
    $output = [];
    exec(escapeshellarg(APPALIAS_RC) . ' onestatus > /dev/null 2>&1', $output, $rc);
    if ($rc == 0) {
        exec(escapeshellarg(APPALIAS_RC) . ' onereload > /dev/null 2>&1', $output, $rc);
        if ($rc != 0) {
            // fenêtre de relance de daemon(8) : superviseur présent, fils absent
            exec(escapeshellarg(APPALIAS_RC) . ' onerestart > /dev/null 2>&1', $output, $rc);
            if ($rc != 0) {
                syslog(LOG_ERR, 'appalias: suivi du catalogue : rechargement du relais en échec');
                return 'reload';
            }
        }
    }
    return null;
}

/** Listes d'une application, pour catalog_follow.py. */
function appalias_app_state($uuid, $app)
{
    return [
        'uuid' => (string)$uuid,
        'name' => (string)$app->name,
        'refs' => AppAlias::splitList($app->catalog_refs),
        'catalog_rules' => AppAlias::splitList($app->catalog_rules),
        'domains' => AppAlias::splitList($app->domains),
        'declined' => AppAlias::splitList($app->catalog_declined),
    ];
}

/**
 * Nouvelles valeurs d'une application : modèle relu depuis Config
 * (modifications des applications précédentes comprises), validé, puis
 * sérialisé. Renvoie null si c'est fait, sinon le message d'erreur ; en cas
 * d'erreur rien n'est sérialisé pour l'application.
 */
function appalias_set_app($uuid, $values)
{
    $mdl = new AppAlias();
    $node = $mdl->getNodeByReference('apps.app.' . $uuid);
    if ($node === null) {
        return 'application not found';
    }
    $node->setNodes($values);
    $errors = appalias_validation_text($mdl);
    if ($errors !== '') {
        return $errors;
    }
    $mdl->serializeToConfig();
    return null;
}

/** Journal (catalog_follow.py record) ; une erreur est seulement journalisée. */
function appalias_record($entries)
{
    if (count($entries) == 0) {
        return;
    }
    $journal = appalias_follow_py('record', ['entries' => $entries]);
    if (isset($journal['error'])) {
        syslog(LOG_ERR, 'appalias: journal du suivi du catalogue : ' . (string)$journal['error']);
    }
}

function appalias_run()
{
    $out = ['result' => 'unchanged', 'revision' => null, 'apps' => [], 'changed' => 0, 'errors' => 0];
    $entries = [];
    $cfg = Config::getInstance();
    $cfg->lock();
    try {
        $mdl = new AppAlias();
        $apps = [];
        foreach ($mdl->apps->app->iterateItems() as $uuid => $app) {
            if ((string)$app->catalog_follow === '1' && count(AppAlias::splitList($app->catalog_refs)) > 0) {
                $apps[(string)$uuid] = appalias_app_state($uuid, $app);
            }
        }
        if (count($apps) == 0) {
            $out['result'] = 'nothing';
            return $out;
        }
        $plan = appalias_follow_py('plan', [
            'apps' => array_values($apps),
            'ignored' => AppAlias::splitList($mdl->general->discovery_ignored),
            'shared_extra' => AppAlias::splitList($mdl->general->shared_platforms_extra),
            'cap' => APPALIAS_MAX_APP_RULES,
        ]);
        if (isset($plan['error']) || !isset($plan['apps']) || !is_array($plan['apps'])) {
            $message = (string)($plan['error'] ?? 'unreadable plan');
            syslog(LOG_ERR, 'appalias: suivi du catalogue impossible : ' . $message);
            return ['result' => 'failed', 'error' => $message];
        }
        $revision = (string)($plan['revision'] ?? '');
        $out['revision'] = $revision;
        $saved = [];
        foreach ($apps as $uuid => $state) {
            $p = $plan['apps'][$uuid] ?? null;
            $name = $state['name'];
            if (!is_array($p)) {
                continue;
            }
            if (isset($p['error'])) {
                syslog(LOG_ERR, sprintf(
                    'appalias: suivi du catalogue : %s non mis à jour : %s',
                    $name,
                    (string)$p['error']
                ));
                $out['apps'][$uuid] = ['name' => $name, 'error' => (string)$p['error']];
                $out['errors']++;
                continue;
            }
            if (!empty($p['capped'])) {
                syslog(LOG_WARNING, sprintf(
                    'appalias: suivi du catalogue : %s non mis à jour, %d règles au-delà du plafond de %d',
                    $name,
                    (int)($p['count'] ?? 0),
                    APPALIAS_MAX_APP_RULES
                ));
                $out['apps'][$uuid] = ['name' => $name, 'capped' => true, 'count' => (int)($p['count'] ?? 0)];
                $out['errors']++;
                continue;
            }
            if (empty($p['changed'])) {
                continue;
            }
            $domains = appalias_strings($p['domains'] ?? null);
            $rules = appalias_strings($p['catalog_rules'] ?? null);
            $add = appalias_strings($p['add'] ?? null);
            $removed = appalias_strings($p['removed'] ?? null);
            if ($domains === null || $rules === null || $add === null || $removed === null) {
                syslog(LOG_ERR, sprintf('appalias: suivi du catalogue : %s : plan illisible', $name));
                $out['apps'][$uuid] = ['name' => $name, 'error' => 'unreadable plan'];
                $out['errors']++;
                continue;
            }
            $error = appalias_set_app($uuid, [
                'domains' => implode(',', $domains),
                'catalog_rules' => implode(',', $rules),
                'catalog_revision' => $revision,
            ]);
            if ($error !== null) {
                syslog(LOG_ERR, sprintf('appalias: suivi du catalogue : %s non enregistré : %s', $name, $error));
                $out['apps'][$uuid] = ['name' => $name, 'error' => $error];
                $out['errors']++;
                continue;
            }
            $saved[$uuid] = ['name' => $name, 'added' => $add, 'removed' => $removed];
        }
        if (count($saved) == 0) {
            return $out;
        }
        try {
            $cfg->save();
        } catch (\Exception $e) {
            syslog(LOG_ERR, 'appalias: suivi du catalogue : enregistrement impossible : ' . $e->getMessage());
            return ['result' => 'failed', 'error' => 'configuration not saved'];
        }
        foreach ($saved as $uuid => $item) {
            $entries[] = [
                'app_uuid' => $uuid,
                'app' => $item['name'],
                'added' => $item['added'],
                'removed' => $item['removed'],
                'revision' => $revision,
                'kind' => 'follow',
            ];
            syslog(LOG_INFO, sprintf(
                'appalias: suivi du catalogue : %s : +%d -%d règles (révision %s)',
                $item['name'],
                count($item['added']),
                count($item['removed']),
                substr($revision, 0, 12)
            ));
            $out['apps'][$uuid] = ['name' => $item['name'], 'added' => $item['added'], 'removed' => $item['removed']];
        }
        $out['changed'] = count($saved);
        $out['result'] = 'updated';
    } finally {
        $cfg->unlock();
    }
    appalias_record($entries);
    if (!appalias_enabled()) {
        syslog(LOG_INFO, 'appalias: suivi du catalogue : application différée, le plugin est désactivé');
        return $out;
    }
    $failed = appalias_apply();
    if ($failed !== null) {
        $out['apply_error'] = $failed;
    }
    return $out;
}

function appalias_undo($entryId)
{
    if (!is_string($entryId) || preg_match(APPALIAS_ENTRY_PATTERN, $entryId) !== 1) {
        return ['result' => 'failed', 'error' => 'invalid entry'];
    }
    $cfg = Config::getInstance();
    $cfg->lock();
    try {
        $mdl = new AppAlias();
        $apps = [];
        foreach ($mdl->apps->app->iterateItems() as $uuid => $app) {
            $apps[(string)$uuid] = appalias_app_state($uuid, $app);
        }
        $plan = appalias_follow_py('undo-plan', [
            'entry_id' => $entryId,
            'apps' => (object)$apps,
            'cap' => APPALIAS_MAX_APP_RULES,
        ]);
        if (isset($plan['error'])) {
            return ['result' => 'failed', 'error' => (string)$plan['error']];
        }
        $entry = $plan['entry'] ?? null;
        $domains = appalias_strings($plan['domains'] ?? null);
        $rules = appalias_strings($plan['catalog_rules'] ?? null);
        $declined = appalias_strings($plan['catalog_declined'] ?? null);
        $added = appalias_strings($plan['added'] ?? null);
        $removed = appalias_strings($plan['removed'] ?? null);
        if (
            !is_array($entry) || !isset($apps[(string)($entry['app_uuid'] ?? '')]) ||
            $domains === null || $rules === null || $declined === null || $added === null || $removed === null
        ) {
            return ['result' => 'failed', 'error' => 'unreadable undo plan'];
        }
        $uuid = (string)$entry['app_uuid'];
        $name = $apps[$uuid]['name'];
        $error = appalias_set_app($uuid, [
            'domains' => implode(',', $domains),
            'catalog_rules' => implode(',', $rules),
            'catalog_declined' => implode(',', $declined),
        ]);
        if ($error !== null) {
            syslog(LOG_ERR, sprintf(
                'appalias: annulation du suivi du catalogue : %s non enregistré : %s',
                $name,
                $error
            ));
            return ['result' => 'failed', 'error' => $error];
        }
        try {
            $cfg->save();
        } catch (\Exception $e) {
            syslog(LOG_ERR, 'appalias: annulation du suivi du catalogue : enregistrement impossible : ' . $e->getMessage());
            return ['result' => 'failed', 'error' => 'configuration not saved'];
        }
        syslog(LOG_INFO, sprintf(
            'appalias: suivi du catalogue annulé : %s : +%d -%d règles',
            $name,
            count($added),
            count($removed)
        ));
    } finally {
        $cfg->unlock();
    }
    appalias_record([[
        'app_uuid' => $uuid,
        'app' => $name,
        'added' => $added,
        'removed' => $removed,
        'revision' => (string)($entry['revision'] ?? ''),
        'kind' => 'undo',
        'undo_of' => $entryId,
    ]]);
    $out = ['result' => 'undone', 'app' => $name, 'added' => $added, 'removed' => $removed];
    if (!appalias_enabled()) {
        syslog(LOG_INFO, 'appalias: annulation du suivi du catalogue : application différée, le plugin est désactivé');
        return $out;
    }
    $failed = appalias_apply();
    if ($failed !== null) {
        $out['apply_error'] = $failed;
    }
    return $out;
}

$args = array_slice($argv ?? [], 1);

/* Un seul suivi ou une seule annulation à la fois : verrou tenu jusqu'à la fin. */
if (!is_dir(dirname(APPALIAS_RUN_LOCK))) {
    mkdir(dirname(APPALIAS_RUN_LOCK), 0750, true);
}
$lock = fopen(APPALIAS_RUN_LOCK, 'c');
if ($lock === false || !flock($lock, LOCK_EX)) {
    appalias_out(['result' => 'failed', 'error' => 'lock unavailable']);
    exit(0);
}

if ($args === ['run']) {
    appalias_out(appalias_run());
} elseif (count($args) == 2 && $args[0] === 'undo') {
    appalias_out(appalias_undo($args[1]));
} else {
    appalias_out(['result' => 'failed', 'error' => 'usage: catalog_follow.php run | undo <entry-id>']);
}

flock($lock, LOCK_UN);
fclose($lock);
