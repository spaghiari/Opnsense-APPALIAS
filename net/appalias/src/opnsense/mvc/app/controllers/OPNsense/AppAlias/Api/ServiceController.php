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
 * reconfigure : template, export, sync, puis rechargement (SIGHUP) ou démarrage.
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
            return ['status' => 'unknown', 'apps' => [], 'error' => gettext('Réponse de status.py illisible.')];
        }
        $mdl = new \OPNsense\AppAlias\AppAlias();
        if ((string)$mdl->general->enabled != '1' && ($data['status'] ?? '') != 'running') {
            $data['status'] = 'disabled';
        }
        if (!isset($data['apps']) || !is_array($data['apps'])) {
            $data['apps'] = [];
        }
        return $data;
    }

    /**
     * Ordre : template rc.conf.d, export de config.json, synchronisation des
     * alias (avec filter reload), puis SIGHUP si le démon tourne, sinon démarrage.
     */
    public function reconfigureAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $this->sessionClose();
        $backend = new Backend();
        $mdl = new \OPNsense\AppAlias\AppAlias();
        $enabled = (string)$mdl->general->enabled == '1';

        $backend->configdpRun('template reload', [static::$internalServiceTemplate]);
        $backend->configdRun('appalias export');
        $backend->configdRun('appalias sync');

        if (!$enabled) {
            $backend->configdRun('appalias stop');
            return ['status' => 'ok'];
        }
        $status = $this->statusAction();
        if (($status['status'] ?? '') == 'running') {
            $backend->configdRun('appalias reload');
        } else {
            $backend->configdRun('appalias start');
        }
        return ['status' => 'ok'];
    }
}
