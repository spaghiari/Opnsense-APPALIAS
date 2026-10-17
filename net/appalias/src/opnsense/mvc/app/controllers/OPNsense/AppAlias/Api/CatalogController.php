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

use OPNsense\AppAlias\AppAlias;
use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;

/**
 * Catalogue v2fly embarqué (catalog/v2fly.lst, interrogé par catalog.py).
 *
 * GET  info, popular, search?q=, preview?ref=, diff[?uuid=] ;
 * POST import {ref, mode: new|merge, app_uuid?, name?, rules[]},
 *      applyDiff {app_uuid, add[], remove[]}.
 *
 * Les écritures vérifient côté serveur que les règles viennent bien de la
 * résolution de la référence, puis enregistrent l'application dans
 * config.xml. Rien n'est appliqué : il faut toujours cliquer sur « Appliquer ».
 */
class CatalogController extends ApiControllerBase
{
    /** Nombre maximal de règles par import (ou par mise à jour). */
    const MAX_IMPORT = 1000;

    /** Au-delà, l'import réussit avec un avertissement. */
    const WARN_IMPORT = 300;

    const QUERY_PATTERN = '/^[a-z0-9.-]{1,64}$/';
    const UUID_PATTERN = '/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/';
    const NAME_PATTERN = '/^[A-Za-z0-9_]{1,31}$/';
    const MAX_RULE_LENGTH = 1100;

    /**
     * Appel configd « appalias <action> [param] » ; JSON décodé ou ['error' => …].
     * Le paramètre est toujours validé avant l'appel (configd le place entre
     * guillemets dans une commande shell).
     */
    private function catalogRun($action, $param = null)
    {
        $backend = new Backend();
        if ($param === null) {
            $raw = $backend->configdRun('appalias ' . $action);
        } else {
            $raw = $backend->configdpRun('appalias ' . $action, [$param]);
        }
        $data = json_decode(trim((string)$raw), true);
        if (!is_array($data)) {
            return ['error' => gettext('Unreadable answer from catalog.py.')];
        }
        return $data;
    }

    private static function failed($message, $extra = [])
    {
        return array_merge(['result' => 'failed', 'message' => $message], $extra);
    }

    private static function tooMany($count)
    {
        return self::failed(sprintf(
            gettext('Too many rules: %d selected, %d at most per import.'),
            $count,
            self::MAX_IMPORT
        ));
    }

    private static function validRef($ref)
    {
        return is_string($ref) && strlen($ref) <= 128 && preg_match(AppAlias::CATALOG_REF_PATTERN, $ref) === 1;
    }

    /**
     * Liste de règles reçue (tableau de chaînes), nettoyée et dédoublonnée ;
     * null si invalide.
     */
    private static function ruleList($value)
    {
        if ($value === null || $value === '') {
            return [];
        }
        if (!is_array($value)) {
            return null;
        }
        $out = [];
        foreach ($value as $rule) {
            if (!is_string($rule)) {
                return null;
            }
            $rule = trim($rule);
            if ($rule === '') {
                continue;
            }
            if (
                strlen($rule) > self::MAX_RULE_LENGTH ||
                strpos($rule, ',') !== false ||
                preg_match('/[^\x21-\x7e]/', $rule) === 1
            ) {
                return null;
            }
            $out[$rule] = true;
        }
        return array_keys($out);
    }

    /** Union ordonnée (ordre de $a, puis nouveautés de $b). */
    private static function union($a, $b)
    {
        return array_keys(array_fill_keys($a, true) + array_fill_keys($b, true));
    }

    /** Éléments de $a absents de $b (ordre de $a). */
    private static function minus($a, $b)
    {
        return array_keys(array_diff_key(array_fill_keys($a, true), array_fill_keys($b, true)));
    }

    /**
     * Règles que la validation du modèle refuserait (domain: sur une plateforme
     * partagée, sur un parent de l'une d'elles, ou TLD seul).
     */
    private static function refusedRules($rules, $platforms)
    {
        $out = [];
        foreach ($rules as $rule) {
            if (AppAlias::sharedRefusal($rule, $platforms) !== null) {
                $out[] = $rule;
            }
        }
        return $out;
    }

    /** Vrai si le nom est une plateforme partagée, son parent ou son sous-domaine. */
    private static function touchesShared($value, $platforms)
    {
        foreach ($platforms as $platform) {
            if (
                $platform === $value ||
                str_ends_with($platform, '.' . $value) ||
                str_ends_with($value, '.' . $platform)
            ) {
                return true;
            }
        }
        return false;
    }

    /** Résolution (catalog.py resolve) d'une liste de références. */
    private function resolveRefs($refs)
    {
        foreach ($refs as $ref) {
            if (!self::validRef($ref)) {
                return ['error' => gettext('Invalid catalog reference.')];
            }
        }
        if (count($refs) == 0) {
            return ['error' => gettext('No catalog reference.')];
        }
        $data = $this->catalogRun('catalog_resolve', implode(',', $refs));
        if (isset($data['error'])) {
            return $data;
        }
        if (empty($data['importable'])) {
            return ['error' => sprintf(
                gettext('This catalog list is broken and cannot be imported (%s).'),
                (string)($data['broken'] ?? '')
            )];
        }
        if (!isset($data['rules']) || !is_array($data['rules'])) {
            return ['error' => gettext('Unreadable answer from catalog.py.')];
        }
        return $data;
    }

    /**
     * Enregistre le modèle sous verrou de config.xml ; renvoie les messages
     * de validation (vide si enregistré).
     */
    private static function validateAndSave($mdl)
    {
        $validations = [];
        foreach ($mdl->performValidation() as $msg) {
            $validations[$msg->getField()] = $msg->getMessage();
        }
        if (count($validations) == 0) {
            $mdl->serializeToConfig();
            Config::getInstance()->save();
        }
        return $validations;
    }

    public function infoAction()
    {
        return $this->catalogRun('catalog_info');
    }

    /** Galerie « Popular apps » (catalog/popular.json, noms présents dans l'instantané). */
    public function popularAction()
    {
        return $this->catalogRun('catalog_popular');
    }

    public function searchAction()
    {
        $query = strtolower(trim((string)$this->request->get('q')));
        if (preg_match(self::QUERY_PATTERN, $query) !== 1) {
            return ['error' => gettext('Search: 1 to 64 characters among a-z, 0-9, "." and "-".'), 'results' => []];
        }
        return $this->catalogRun('catalog_search', $query);
    }

    public function previewAction()
    {
        $ref = trim((string)$this->request->get('ref'));
        if (!self::validRef($ref)) {
            return ['error' => gettext('Invalid catalog reference.')];
        }
        $data = $this->catalogRun('catalog_preview', $ref);
        if (isset($data['rules']) && is_array($data['rules'])) {
            // Drapeaux recalculés avec la liste complète (fichier + réglage extra).
            $platforms = (new AppAlias())->sharedPlatforms();
            foreach ($data['rules'] as $i => $rule) {
                $kind = (string)($rule['kind'] ?? '');
                $value = (string)($rule['value'] ?? '');
                $text = (string)($rule['rule'] ?? '');
                $refused = null;
                if ($kind === 'domain') {
                    $refused = AppAlias::sharedRefusal($text, $platforms);
                }
                if ($refused === null && strpos($text, ',') !== false) {
                    $refused = 'comma';
                }
                $data['rules'][$i]['refused'] = $refused;
                $data['rules'][$i]['shared'] = in_array($kind, ['domain', 'full'], true) &&
                    self::touchesShared($value, $platforms);
            }
        }
        return $data;
    }

    public function importAction()
    {
        if (!$this->request->isPost()) {
            return self::failed(gettext('POST required.'));
        }
        $ref = (string)$this->request->getPost('ref');
        $mode = (string)$this->request->getPost('mode');
        if (!self::validRef($ref)) {
            return self::failed(gettext('Invalid catalog reference.'));
        }
        if (!in_array($mode, ['new', 'merge'], true)) {
            return self::failed(gettext('Invalid import mode.'));
        }
        $rules = self::ruleList($this->request->getPost('rules'));
        if ($rules === null) {
            return self::failed(gettext('Invalid rule list.'));
        }
        if (count($rules) == 0) {
            return self::failed(gettext('Select at least one rule.'));
        }
        if (count($rules) > self::MAX_IMPORT) {
            return self::tooMany(count($rules));
        }

        $resolved = $this->resolveRefs([$ref]);
        if (isset($resolved['error'])) {
            return self::failed((string)$resolved['error']);
        }
        $unknown = self::minus($rules, $resolved['rules']);
        if (count($unknown) > 0) {
            return self::failed(sprintf(
                gettext('%d rule(s) are not part of the catalog list %s, for example %s.'),
                count($unknown),
                $ref,
                $unknown[0]
            ));
        }
        $revision = (string)($resolved['revision'] ?? '');

        $uuid = null;
        $name = null;
        if ($mode === 'merge') {
            $uuid = (string)$this->request->getPost('app_uuid');
            if (preg_match(self::UUID_PATTERN, $uuid) !== 1) {
                return self::failed(gettext('Choose the application to merge into.'));
            }
        } else {
            $name = strtoupper(trim((string)$this->request->getPost('name')));
            if (preg_match(self::NAME_PATTERN, $name) !== 1) {
                return self::failed(gettext('Name: letters, digits or "_" only (31 characters at most).'));
            }
        }

        Config::getInstance()->lock();
        try {
            $mdl = new AppAlias();
            if ($mode === 'merge') {
                $node = $mdl->getNodeByReference('apps.app.' . $uuid);
                if ($node === null) {
                    return self::failed(gettext('Application not found.'));
                }
                $node->setNodes([
                    'origin' => 'v2fly',
                    'domains' => implode(',', self::union(AppAlias::splitList($node->domains), $rules)),
                    'catalog_refs' => implode(',', self::union(AppAlias::splitList($node->catalog_refs), [$ref])),
                    'catalog_rules' => implode(',', self::union(AppAlias::splitList($node->catalog_rules), $rules)),
                    'catalog_revision' => $revision,
                ]);
            } else {
                $node = $mdl->apps->app->Add();
                $node->setNodes([
                    'enabled' => '1',
                    'name' => $name,
                    'description' => 'v2fly: ' . $ref,
                    'origin' => 'v2fly',
                    'domains' => implode(',', $rules),
                    'families' => 'both',
                    'catalog_refs' => $ref,
                    'catalog_rules' => implode(',', $rules),
                    'catalog_revision' => $revision,
                ]);
                $uuid = (string)$node->getAttribute('uuid');
            }
            $validations = self::validateAndSave($mdl);
        } finally {
            Config::getInstance()->unlock();
        }

        if (count($validations) > 0) {
            return self::failed(gettext('The application was not saved.'), ['validations' => $validations]);
        }
        $result = ['result' => 'saved', 'uuid' => $uuid, 'count' => count($rules)];
        if (count($rules) > self::WARN_IMPORT) {
            $result['warning'] = sprintf(
                gettext('%d rules imported: a large list can fill the alias with many addresses.'),
                count($rules)
            );
        }
        return $result;
    }

    /**
     * Écarts entre le catalogue et chaque application importée :
     * added = catalogue - catalog_rules, removed = catalog_rules - catalogue,
     * custom = domains - catalog_rules.
     */
    public function diffAction()
    {
        $only = trim((string)$this->request->get('uuid'));
        if ($only !== '' && preg_match(self::UUID_PATTERN, $only) !== 1) {
            return ['error' => gettext('Invalid application.')];
        }
        $out = ['apps' => []];
        $mdl = new AppAlias();
        $platforms = $mdl->sharedPlatforms();
        foreach ($mdl->apps->app->iterateItems() as $uuid => $app) {
            $uuid = (string)$uuid;
            if ($only !== '' && $uuid !== $only) {
                continue;
            }
            $refs = AppAlias::splitList($app->catalog_refs);
            if (count($refs) == 0) {
                continue;
            }
            $item = [
                'name' => (string)$app->name,
                'refs' => $refs,
                'revision' => (string)$app->catalog_revision,
            ];
            $resolved = $this->resolveRefs($refs);
            if (isset($resolved['error'])) {
                $item['error'] = (string)$resolved['error'];
            } else {
                $catalog = $resolved['rules'];
                $imported = AppAlias::splitList($app->catalog_rules);
                // Règles refusées par le garde-fou : jamais proposées (applyDiff échouerait).
                $added = self::minus($catalog, $imported);
                $item['refused'] = self::refusedRules($added, $platforms);
                $item['added'] = self::minus($added, $item['refused']);
                $item['removed'] = self::minus($imported, $catalog);
                $item['custom'] = self::minus(AppAlias::splitList($app->domains), $imported);
                $item['catalog_revision'] = (string)($resolved['revision'] ?? '');
            }
            $out['apps'][$uuid] = $item;
        }
        return $out;
    }

    public function applyDiffAction()
    {
        if (!$this->request->isPost()) {
            return self::failed(gettext('POST required.'));
        }
        $uuid = (string)$this->request->getPost('app_uuid');
        if (preg_match(self::UUID_PATTERN, $uuid) !== 1) {
            return self::failed(gettext('Invalid application.'));
        }
        $add = self::ruleList($this->request->getPost('add'));
        $remove = self::ruleList($this->request->getPost('remove'));
        if ($add === null || $remove === null) {
            return self::failed(gettext('Invalid rule list.'));
        }
        if (count($add) + count($remove) == 0) {
            return self::failed(gettext('Select at least one change.'));
        }
        if (count($add) > self::MAX_IMPORT) {
            return self::tooMany(count($add));
        }

        Config::getInstance()->lock();
        try {
            $mdl = new AppAlias();
            $node = $mdl->getNodeByReference('apps.app.' . $uuid);
            if ($node === null) {
                return self::failed(gettext('Application not found.'));
            }
            $refs = AppAlias::splitList($node->catalog_refs);
            $resolved = $this->resolveRefs($refs);
            if (isset($resolved['error'])) {
                return self::failed((string)$resolved['error']);
            }
            $imported = AppAlias::splitList($node->catalog_rules);
            // Règles refusées par le garde-fou : ignorées et signalées.
            $ignored = self::refusedRules($add, $mdl->sharedPlatforms());
            $add = self::minus($add, $ignored);
            if (count($add) + count($remove) == 0) {
                return self::failed(
                    sprintf(
                        gettext('Nothing to save: %d rule(s) refused (shared platform or top-level domain).'),
                        count($ignored)
                    ),
                    ['ignored' => $ignored]
                );
            }
            if (count(self::minus($add, $resolved['rules'])) > 0) {
                return self::failed(
                    gettext('Some rules to add are not part of the catalog lists of this application.')
                );
            }
            if (count(self::minus($remove, $imported)) > 0) {
                return self::failed(gettext('Only rules imported from the catalog can be removed here.'));
            }
            $domains = self::minus(self::union(AppAlias::splitList($node->domains), $add), $remove);
            $imported = self::minus(self::union($imported, $add), $remove);
            $node->setNodes([
                'domains' => implode(',', $domains),
                'catalog_rules' => implode(',', $imported),
                'catalog_revision' => (string)($resolved['revision'] ?? ''),
            ]);
            $validations = self::validateAndSave($mdl);
        } finally {
            Config::getInstance()->unlock();
        }

        if (count($validations) > 0) {
            return self::failed(gettext('The application was not saved.'), ['validations' => $validations]);
        }
        $result = ['result' => 'saved', 'added' => count($add), 'removed' => count($remove), 'ignored' => $ignored];
        if (count($ignored) > 0) {
            $result['warning'] = sprintf(
                gettext('%d rule(s) ignored (shared platform or top-level domain): %s'),
                count($ignored),
                implode(', ', array_slice($ignored, 0, 5))
            );
        }
        return $result;
    }
}
