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

namespace OPNsense\AppAlias\Api;

use OPNsense\Base\ApiMutableServiceControllerBase;
use OPNsense\Core\Backend;

/**
 * Pilotage du démon appaliasd.
 *
 * start / stop / restart : hérités (actions configd « appalias start|stop|restart ») ;
 * status : JSON de status.py (état du démon + compteurs par application) ;
 * reconfigure : template, export, sync, branchement d'Unbound, puis rechargement
 *               (SIGHUP) ou démarrage ;
 * unboundStatus : état du branchement d'Unbound ; unboundRestart : redémarrage
 *               d'Unbound, seulement à la demande de l'utilisateur.
 */
class ServiceController extends ApiMutableServiceControllerBase
{
    protected static $internalServiceClass = '\OPNsense\AppAlias\AppAlias';
    protected static $internalServiceTemplate = 'OPNsense/AppAlias';
    protected static $internalServiceEnabled = 'general.enabled';
    protected static $internalServiceName = 'appalias';

    /**
     * État du service ; la clé « status » suit la convention OPNsense
     * (running, stopped, disabled), la clé « apps » alimente la grille.
     */
    public function statusAction()
    {
        $backend = new Backend();
        $raw = trim((string)$backend->configdRun('appalias status'));
        $data = json_decode($raw, true);
        if (!is_array($data)) {
            return ['status' => 'unknown', 'apps' => [], 'error' => gettext('Unreadable answer from status.py.')];
        }
        $mdl = new \OPNsense\AppAlias\AppAlias();
        if ((string)$mdl->general->enabled != '1' && ($data['status'] ?? '') != 'running') {
            $data['status'] = 'disabled';
        }
        if (!isset($data['apps']) || !is_array($data['apps'])) {
            $data['apps'] = [];
        }
        $data['widget'] = [
            'caption_restart' => gettext('Restart'),
            'caption_start' => gettext('Start'),
            'caption_stop' => gettext('Stop'),
        ];
        return $data;
    }

    /**
     * Ordre : template rc.conf.d, export de config.json, synchronisation des
     * alias (avec filter reload), zones de transfert d'Unbound (redémarrage
     * d'Unbound seulement si la liste a changé), puis SIGHUP si le démon tourne,
     * sinon démarrage.
     */
    public function reconfigureAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $backend = new Backend();
        $mdl = new \OPNsense\AppAlias\AppAlias();
        $enabled = (string)$mdl->general->enabled == '1';

        // Les actions configd de type « script » renvoient « OK » en cas de succès.
        $templateResult = trim($backend->configdpRun('template reload', [static::$internalServiceTemplate]));
        if ($templateResult !== 'OK') {
            throw new \OPNsense\Base\UserException(sprintf(
                gettext('Template generation failed for internal service "%s". See backend log for details.'),
                static::$internalServiceName
            ), gettext('Configuration exception'));
        }
        foreach (['export', 'sync', 'unbound'] as $step) {
            if (trim($backend->configdRun('appalias ' . $step)) !== 'OK') {
                return [
                    'status' => 'failed',
                    'status_msg' => sprintf(gettext('AppAlias: the "%s" step failed, see the log.'), $step),
                ];
            }
        }

        if (!$enabled) {
            $res = $backend->configdRun('appalias stop');
            return ['status' => trim($res) === 'OK' ? 'ok' : 'failed'];
        }
        $status = $this->statusAction();
        if (($status['status'] ?? '') == 'running') {
            $res = $backend->configdRun('appalias reload');
            if (trim($res) !== 'OK') {
                // Fenêtre de relance de daemon(8) : superviseur présent, fils absent.
                $res = $backend->configdRun('appalias restart');
            }
        } else {
            $res = $backend->configdRun('appalias start');
        }
        return ['status' => trim($res) === 'OK' ? 'ok' : 'failed'];
    }

    /**
     * État du branchement d'Unbound (zones transférées, conflits ignorés,
     * erreurs).
     */
    public function unboundStatusAction()
    {
        $backend = new Backend();
        $data = json_decode(trim((string)$backend->configdRun('appalias unbound_status')), true);
        return is_array($data) ? $data : [];
    }

    /**
     * Redémarrage d'Unbound à la demande de l'utilisateur (après confirmation
     * dans l'interface). Le fichier est d'abord régénéré, sans redémarrage.
     */
    public function unboundRestartAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $backend = new Backend();
        if (trim((string)$backend->configdRun('appalias unbound_write')) !== 'OK') {
            return [
                'status' => 'failed',
                'status_msg' => gettext('AppAlias: writing the Unbound zones failed, see the log.'),
            ];
        }
        if (trim((string)$backend->configdRun('unbound restart')) !== 'OK') {
            return ['status' => 'failed', 'status_msg' => gettext('Unbound restart failed, see the log.')];
        }
        return ['status' => 'ok'];
    }
}
