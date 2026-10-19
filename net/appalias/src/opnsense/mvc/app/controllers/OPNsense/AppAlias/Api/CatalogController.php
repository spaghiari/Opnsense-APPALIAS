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
 * GET  info, popular, search?q=, preview?ref=, diff[?uuid=], followLog ;
 * POST import {ref, mode: new|merge, app_uuid?, name?, rules[]} ; sans rules
 *      (mode new) le serveur choisit les règles et renvoie la référence
 *      effective (« ref », avec @-ads si la liste porte des règles @ads),
 *      applyDiff {app_uuid, add[], remove[]},
 *      update (téléchargement en ligne, si l'option est activée), reset
 *      (retour à l'instantané embarqué), undoFollow {entry_id}.
 *
 * Les écritures vérifient côté serveur que les règles viennent bien de la
 * résolution de la référence, puis enregistrent l'application dans
 * config.xml. Import, fusion et diff manuel n'appliquent rien eux-mêmes : le
 * front enchaîne l'application (service/reconfigure), ou l'utilisateur clique
 * sur « Appliquer ». Seul le suivi automatique (catalog_follow=1,
 * catalog_follow.php, après une mise à jour du catalogue) et son annulation
 * (undoFollow) appliquent eux-mêmes leurs changements.
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
    const ENTRY_PATTERN = '/^[0-9a-f]{16}$/';

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
     * Éléments de $a absents de $b, comparés sous leur forme canonique
     * (AppAlias::ruleKey : « example.com » équivaut à « domain:example.com »).
     */
    private static function minusRules($a, $b)
    {
        $known = [];
        foreach ($b as $rule) {
            $known[AppAlias::ruleKey($rule)] = true;
        }
        $out = [];
        foreach ($a as $rule) {
            if (!isset($known[AppAlias::ruleKey($rule)])) {
                $out[] = $rule;
            }
        }
        return $out;
    }

    /** Règles sans virgule : une virgule casserait la liste catalog_declined. */
    private static function withoutComma($rules)
    {
        return array_values(array_filter($rules, function ($rule) {
            return strpos($rule, ',') === false;
        }));
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

    /**
     * Règles que le suivi (lib/follow.py plan_app) ajouterait à l'application :
     * mêmes écarts (déjà importée, déclinée, déjà présente sous sa forme
     * canonique, doublon) et mêmes exclusions (virgule, non appliquées keyword:/regexp:, refus,
     * plateforme partagée ou TLD).
     */
    private static function followAdds($catalog, $imported, $declined, $domains, $platforms)
    {
        $importedSet = array_fill_keys($imported, true);
        $declinedSet = array_fill_keys($declined, true);
        $present = [];
        foreach ($domains as $domain) {
            $present[AppAlias::ruleKey($domain)] = true;
        }
        $add = [];
        foreach ($catalog as $text) {
            $text = (string)$text;
            if (
                isset($importedSet[$text]) || isset($declinedSet[$text]) ||
                isset($present[AppAlias::ruleKey($text)]) || isset($add[$text])
            ) {
                continue;
            }
            if (preg_match('/^([a-z]+):(.*)$/s', $text, $m) === 1) {
                $kind = $m[1];
                $value = $m[2];
            } else {
                $kind = 'domain';
                $value = $text;
            }
            if (strpos($text, ',') !== false || $kind === 'keyword' || $kind === 'regexp') {
                continue;
            }
            if ($kind === 'domain' && AppAlias::sharedRefusal($text, $platforms) !== null) {
                continue;
            }
            if ($kind === 'domain' || $kind === 'full') {
                if (AppAlias::catalogExclusion($kind, $value, $platforms) !== null) {
                    continue;
                }
            }
            $add[$text] = true;
        }
        return array_keys($add);
    }

    /**
     * Aperçu catalog_preview dont les drapeaux « refused » et « shared » sont
     * recalculés avec la liste complète des plateformes (fichier + réglage extra).
     */
    private function previewData($ref, $platforms)
    {
        $data = $this->catalogRun('catalog_preview', $ref);
        if (isset($data['rules']) && is_array($data['rules'])) {
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
                $data['rules'][$i]['shared'] = AppAlias::catalogExclusion($kind, $value, $platforms) === 'shared';
            }
        }
        return $data;
    }

    /**
     * Sélection automatique (import sans « rules ») : règles ni non appliquées (keyword:/regexp:),
     * ni sur une plateforme partagée, ni refusées, ni avec virgule. Si l'aperçu
     * contient des règles @ads, la référence reçoit « @-ads » (comme le fait
     * l'interface) et l'aperçu est relancé.
     * @return array ['ref' => référence effective, 'rules' => règles retenues] ou ['error' => …]
     */
    private function autoSelect($ref, $platforms)
    {
        $data = $this->previewData($ref, $platforms);
        if (isset($data['error']) || !isset($data['rules']) || !is_array($data['rules'])) {
            return ['error' => (string)($data['error'] ?? gettext('Unreadable answer from catalog.py.'))];
        }
        $hasAds = false;
        foreach ($data['rules'] as $rule) {
            if (in_array('ads', (array)($rule['attrs'] ?? []), true)) {
                $hasAds = true;
                break;
            }
        }
        // pas de « @-ads » si la référence demande déjà « ads » (x@ads) ou l'écarte déjà
        // attributs seulement : l'index 0 est le nom de la liste
        $tokens = array_slice(explode('@', $ref), 1);
        if ($hasAds && !in_array('-ads', $tokens, true) && !in_array('ads', $tokens, true)) {
            $ref = $ref . '@-ads';
            if (!self::validRef($ref)) {
                return ['error' => gettext('Invalid catalog reference.')];
            }
            $data = $this->previewData($ref, $platforms);
            if (isset($data['error']) || !isset($data['rules']) || !is_array($data['rules'])) {
                return ['error' => (string)($data['error'] ?? gettext('Unreadable answer from catalog.py.'))];
            }
        }
        $selected = [];
        foreach ($data['rules'] as $rule) {
            $text = (string)($rule['rule'] ?? '');
            if (
                $text !== '' &&
                empty($rule['unapplied']) &&
                empty($rule['shared']) &&
                // « tld » comme « shared » : jamais sélectionnés automatiquement
                AppAlias::catalogExclusion(
                    (string)($rule['kind'] ?? ''),
                    (string)($rule['value'] ?? ''),
                    $platforms
                ) === null &&
                ($rule['refused'] ?? null) === null &&
                strpos($text, ',') === false
            ) {
                $selected[$text] = true;
            }
        }
        return ['ref' => $ref, 'rules' => array_keys($selected)];
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
        $data = $this->catalogRun('catalog_info');
        $mdl = new AppAlias();
        $data['online_update'] = (string)$mdl->general->catalog_online_update == '1';
        $data['schedule'] = (string)$mdl->general->catalog_update_schedule;
        return $data;
    }

    /**
     * Téléchargement du dernier catalogue v2fly (catalog_update.py), seulement
     * si l'option est activée. Le catalogue est remplacé après contrôle ; les
     * applications suivies (catalog_follow=1) sont ensuite mises à jour et
     * appliquées (clé « follow » du résultat), les autres passent par le diff.
     */
    public function updateAction()
    {
        if (!$this->request->isPost()) {
            return self::failed(gettext('POST required.'));
        }
        $mdl = new AppAlias();
        if ((string)$mdl->general->catalog_online_update != '1') {
            return self::failed(gettext('Catalog download is disabled in Settings.'));
        }
        $data = $this->catalogRun('catalog_update');
        if (!isset($data['result'])) {
            return self::failed((string)($data['error'] ?? gettext('Unreadable answer from catalog_update.py.')));
        }
        return $data;
    }

    /** Suppression du catalogue téléchargé : retour à l'instantané embarqué. */
    public function resetAction()
    {
        if (!$this->request->isPost()) {
            return self::failed(gettext('POST required.'));
        }
        $data = $this->catalogRun('catalog_reset');
        return isset($data['result']) ? $data : self::failed((string)($data['error'] ?? ''));
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
        return $this->previewData($ref, (new AppAlias())->sharedPlatforms());
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
        $rawRules = $this->request->getPost('rules');
        // Clé « rules » absente seulement : une chaîne vide reste une sélection vide.
        $isAuto = $rawRules === null && $mode === 'new';
        if ($isAuto) {
            // Sans « rules » : sélection automatique côté serveur (parcours « un clic »).
            $auto = $this->autoSelect($ref, (new AppAlias())->sharedPlatforms());
            if (isset($auto['error'])) {
                return self::failed((string)$auto['error']);
            }
            $ref = $auto['ref'];
            $rules = $auto['rules'];
        } else {
            $rules = self::ruleList($rawRules);
        }
        if ($rules === null) {
            return self::failed(gettext('Invalid rule list.'));
        }
        if (count($rules) == 0) {
            return self::failed(gettext('Select at least one rule.'));
        }
        if (count($rules) > self::MAX_IMPORT) {
            if ($isAuto) {
                return self::failed(sprintf(
                    gettext('This application has %d rules to follow, above the limit of %d. Import it from the catalog dialog and choose the rules to keep.'),
                    count($rules),
                    self::MAX_IMPORT
                ));
            }
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
                $current = AppAlias::splitList($node->domains);
                $domains = self::union($current, $rules);
                // une règle déjà présente à la main dans domains n'entre pas dans catalog_rules
                $tracked = self::minusRules($rules, $current);
                // catalog_follow : seulement si l'application n'était pas déjà liée au catalogue
                // (jamais d'écrasement d'une désactivation explicite)
                $hadRefs = count(AppAlias::splitList($node->catalog_refs)) > 0;
                $values = [
                    'origin' => 'v2fly',
                    'domains' => implode(',', $domains),
                    'catalog_refs' => implode(',', self::union(AppAlias::splitList($node->catalog_refs), [$ref])),
                    'catalog_rules' => implode(',', self::union(AppAlias::splitList($node->catalog_rules), $tracked)),
                    'catalog_revision' => $revision,
                    // règles de la liste non retenues : jamais ajoutées par le suivi automatique
                    'catalog_declined' => implode(',', self::withoutComma(self::minusRules(
                        self::union(AppAlias::splitList($node->catalog_declined), $resolved['rules']),
                        $domains
                    ))),
                ];
                if (!$hadRefs) {
                    $values['catalog_follow'] = '1';
                }
                $node->setNodes($values);
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
                    'catalog_follow' => '1',
                    'catalog_declined' => implode(',', self::withoutComma(
                        self::minusRules($resolved['rules'], $rules)
                    )),
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
        $result = ['result' => 'saved', 'uuid' => $uuid, 'count' => count($rules), 'ref' => $ref];
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
                'follow' => (string)$app->catalog_follow === '1',
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
                // Nombre de règles après application du diff, au regard du plafond.
                // Application suivie : mêmes filtres que le suivi (plan_app) ; sinon
                // règles proposées, comparées sous leur forme canonique.
                $domains = AppAlias::splitList($app->domains);
                if ((string)$app->catalog_follow === '1') {
                    $adds = self::followAdds(
                        $catalog,
                        $imported,
                        AppAlias::splitList($app->catalog_declined),
                        $domains,
                        $platforms
                    );
                } else {
                    $adds = self::minusRules($item['added'], $domains);
                }
                $projected = count(self::minus($domains, $item['removed'])) + count($adds);
                $item['projected'] = $projected;
                $item['capped'] = (count($adds) + count($item['removed']) > 0) && $projected > self::MAX_IMPORT;
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
            $current = AppAlias::splitList($node->domains);
            $domains = self::minus(self::union($current, $add), $remove);
            // une règle déjà présente à la main dans domains n'entre pas dans catalog_rules
            $tracked = self::minusRules($add, $current);
            $imported = self::minus(self::union($imported, $tracked), $remove);
            $node->setNodes([
                'domains' => implode(',', $domains),
                'catalog_rules' => implode(',', $imported),
                'catalog_revision' => (string)($resolved['revision'] ?? ''),
                'catalog_declined' => implode(',', self::minus(AppAlias::splitList($node->catalog_declined), $add)),
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

    /**
     * Journal du suivi automatique (20 dernières entrées, la plus récente en
     * premier) ; « app_exists » indique si l'application existe encore.
     */
    public function followLogAction()
    {
        $data = $this->catalogRun('catalog_follow_log');
        if (!isset($data['entries']) || !is_array($data['entries'])) {
            return [
                'entries' => [],
                'error' => (string)($data['error'] ?? gettext('Unreadable answer from catalog_follow.py.')),
            ];
        }
        $apps = [];
        foreach ((new AppAlias())->apps->app->iterateItems() as $uuid => $app) {
            $apps[(string)$uuid] = (string)$app->name;
        }
        foreach ($data['entries'] as $i => $entry) {
            $uuid = is_array($entry) ? (string)($entry['app_uuid'] ?? '') : '';
            $data['entries'][$i]['app_exists'] = isset($apps[$uuid]);
            if (isset($apps[$uuid])) {
                $data['entries'][$i]['app'] = $apps[$uuid];
            }
        }
        return $data;
    }

    /**
     * Annulation d'une entrée du journal (catalog_follow.php undo) : retire
     * ce qui a été ajouté, remet ce qui a été retiré, puis applique. Refusée
     * si l'application a été supprimée ou si l'entrée est déjà annulée.
     */
    public function undoFollowAction()
    {
        if (!$this->request->isPost()) {
            return self::failed(gettext('POST required.'));
        }
        $entry = (string)$this->request->getPost('entry_id');
        if (preg_match(self::ENTRY_PATTERN, $entry) !== 1) {
            return self::failed(gettext('Invalid journal entry.'));
        }
        $data = $this->catalogRun('catalog_follow_undo', $entry);
        if (($data['result'] ?? '') !== 'undone') {
            return self::failed((string)($data['error'] ?? gettext('Unreadable answer from catalog_follow.php.')));
        }
        return $data;
    }
}
