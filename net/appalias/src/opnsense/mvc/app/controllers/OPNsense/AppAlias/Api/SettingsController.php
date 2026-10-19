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

use OPNsense\Base\ApiMutableModelControllerBase;

/**
 * Réglages et applications.
 *
 * get / set : section générale (héritées) ;
 * searchApp / getApp / addApp / setApp / delApp / toggleApp : applications.
 */
class SettingsController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'appalias';
    protected static $internalModelClass = 'OPNsense\AppAlias\AppAlias';

    public function searchAppAction()
    {
        return $this->searchBase(
            'apps.app',
            [
                'enabled', 'name', 'description', 'origin', 'domains', 'networks', 'families', 'catalog_refs',
                'catalog_follow',
                'proactive',
            ],
            'name'
        );
    }

    public function getAppAction($uuid = null)
    {
        return $this->getBase('app', 'apps.app', $uuid);
    }

    public function addAppAction()
    {
        return $this->addBase('app', 'apps.app');
    }

    public function setAppAction($uuid)
    {
        return $this->setBase('app', 'apps.app', $uuid);
    }

    public function delAppAction($uuid)
    {
        return $this->delBase('apps.app', $uuid);
    }

    public function toggleAppAction($uuid, $enabled = null)
    {
        return $this->toggleBase('apps.app', $uuid, $enabled);
    }
}
