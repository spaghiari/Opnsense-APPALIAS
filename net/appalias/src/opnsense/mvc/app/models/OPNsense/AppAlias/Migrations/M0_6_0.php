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

namespace OPNsense\AppAlias\Migrations;

use OPNsense\AppAlias\AppAlias;
use OPNsense\Base\BaseModelMigration;

/**
 * Modèle 0.6.0 (plugin 0.6) : résolution proactive.
 *
 * Seules les valeurs par défaut des nouveaux champs sont posées par la
 * migration de base (proactive_enabled = 1, intervalles 300 s et 3600 s,
 * préfixe « www », proactive = 1 par application). Rien n'est lancé.
 *
 * La fréquence de mise à jour du catalogue passe de « daily » (ancien défaut)
 * à « hourly » (nouveau défaut) : migrée une seule fois, seulement si la
 * valeur stockée est « daily » ; « manual » est conservé.
 */
class M0_6_0 extends BaseModelMigration
{
    public function run($model)
    {
        parent::run($model);
        if (!($model instanceof AppAlias)) {
            return;
        }
        if ((string)$model->general->catalog_update_schedule === 'daily') {
            $model->general->catalog_update_schedule->setValue('hourly');
        }
    }
}
