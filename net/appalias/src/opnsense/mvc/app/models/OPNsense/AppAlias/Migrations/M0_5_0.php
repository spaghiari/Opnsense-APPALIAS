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
 * Modèle 0.5.0 (plugin 0.6) : suivi automatique du catalogue.
 *
 * - catalog_follow : 1 pour les applications existantes qui ont des
 *   catalog_refs (importées ou fusionnées depuis le catalogue), 0 pour les
 *   autres (valeur par défaut, posée par la migration de base) ;
 * - catalog_declined : règles de la résolution actuelle absentes de
 *   catalog_rules et de domains, c'est-à-dire décochées à l'import (ou
 *   arrivées depuis sans être acceptées). Le suivi ne les ajoute pas : sans
 *   cela, la première mise à jour ajouterait d'un coup ce que l'utilisateur
 *   avait refusé. Les nouveautés suivantes du catalogue seront, elles,
 *   ajoutées.
 *
 * Si la résolution échoue (catalogue illisible, liste disparue), le suivi
 * reste à 0 pour l'application et la raison est journalisée : le diff
 * manuel reste disponible.
 */
class M0_5_0 extends BaseModelMigration
{
    const CATALOG_PY = '/usr/local/opnsense/scripts/OPNsense/AppAlias/catalog.py';

    public function run($model)
    {
        // valeurs par défaut des champs obligatoires (catalog_follow = 0)
        parent::run($model);
        if (!($model instanceof AppAlias)) {
            return;
        }
        foreach ($model->apps->app->iterateItems() as $app) {
            $refs = AppAlias::splitList($app->catalog_refs);
            if (count($refs) == 0) {
                $app->catalog_follow->setValue('0');
                continue;
            }
            $rules = self::resolve($refs);
            if ($rules === null) {
                $app->catalog_follow->setValue('0');
                syslog(LOG_WARNING, sprintf(
                    'appalias: migration 0.5.0 : %s ne suit pas le catalogue (résolution impossible de %s)',
                    (string)$app->name,
                    implode(',', $refs)
                ));
                continue;
            }
            // comparaison normalisée (« example.com » = « domain:example.com », casse ignorée)
            $kept = [];
            foreach (array_merge(AppAlias::splitList($app->catalog_rules), AppAlias::splitList($app->domains)) as $rule) {
                $kept[AppAlias::ruleKey($rule)] = true;
            }
            $declined = [];
            foreach ($rules as $rule) {
                if (!isset($kept[AppAlias::ruleKey($rule)]) && strpos($rule, ',') === false) {
                    $declined[] = $rule;
                }
            }
            $app->catalog_declined->setValue(implode(',', $declined));
            $app->catalog_follow->setValue('1');
        }
    }

    /**
     * Règles résolues (catalog.py resolve), ou null en cas d'échec. Les
     * références sont validées avant l'appel.
     */
    private static function resolve($refs)
    {
        if (count($refs) > 32) {
            return null;
        }
        foreach ($refs as $ref) {
            if (strlen($ref) > 128 || preg_match(AppAlias::CATALOG_REF_PATTERN, $ref) !== 1) {
                return null;
            }
        }
        $output = [];
        exec(
            escapeshellarg(self::CATALOG_PY) . ' resolve ' . escapeshellarg(implode(',', $refs)) . ' 2>/dev/null',
            $output,
            $rc
        );
        $data = json_decode(implode("\n", $output), true);
        if (
            $rc != 0 || !is_array($data) || empty($data['importable']) ||
            !isset($data['rules']) || !is_array($data['rules'])
        ) {
            return null;
        }
        return array_map('strval', $data['rules']);
    }
}
