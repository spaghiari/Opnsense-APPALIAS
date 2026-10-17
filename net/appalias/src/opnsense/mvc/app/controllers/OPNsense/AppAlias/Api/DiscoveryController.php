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
 * Découverte : propositions calculées par discovery.py (lecture seule) et
 * décisions de l'utilisateur.
 *
 * GET  status, proposals?app=&min=, unassigned ;
 * POST accept {name, rule, app_uuid}, ignore {rule}, unignore {rule},
 *      learn {seconds, app_uuid?}, stopLearn, purge, refresh.
 *
 * accept enregistre la règle dans l'application (validation du modèle et
 * garde-fou des plateformes partagées) ; rien n'est appliqué avant « Apply ».
 * Les règles ignorées (general.discovery_ignored) sont filtrées ici tout de
 * suite, et par le démon à la source après le prochain « Apply ».
 */
class DiscoveryController extends ApiControllerBase
{
    const UUID_PATTERN = '/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/';
    const HOST_PATTERN = '/^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?(\.[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)*$/';
    const MAX_LEARN = 600;

    /** Réponse de discovery.py list, lue une fois par requête. */
    private $listCache = null;

    private static function failed($message, $extra = [])
    {
        return array_merge(['result' => 'failed', 'message' => $message], $extra);
    }

    private function listData()
    {
        if ($this->listCache === null) {
            $raw = (new Backend())->configdRun('appalias discovery_list');
            $data = json_decode(trim((string)$raw), true);
            if (!is_array($data)) {
                $data = ['error' => gettext('Unreadable answer from discovery.py.')];
            }
            $this->listCache = $data;
        }
        return $this->listCache;
    }

    /** Règles ignorées du modèle -> [type, nom]. */
    private static function ignoredRules($mdl)
    {
        $out = [];
        foreach (AppAlias::splitList($mdl->general->discovery_ignored) as $rule) {
            $parsed = AppAlias::discoveryRule($rule);
            if ($parsed !== null) {
                $out[] = $parsed;
            }
        }
        return $out;
    }

    private static function isIgnored($name, $ignored)
    {
        foreach ($ignored as $rule) {
            if ($rule[1] === $name || ($rule[0] === 'domain' && str_ends_with($name, '.' . $rule[1]))) {
                return true;
            }
        }
        return false;
    }

    /** [propositions, groupes non attribués] sans les noms ignorés depuis le dernier export. */
    private function proposalsData()
    {
        $data = $this->listData();
        $ignored = self::ignoredRules(new AppAlias());
        $proposals = [];
        foreach ($data['proposals'] ?? [] as $item) {
            if (is_array($item) && !self::isIgnored((string)($item['name'] ?? ''), $ignored)) {
                $proposals[] = $item;
            }
        }
        $groups = [];
        foreach ($data['unassigned'] ?? [] as $group) {
            if (!is_array($group)) {
                continue;
            }
            $names = [];
            foreach ($group['names'] ?? [] as $item) {
                if (is_array($item) && !self::isIgnored((string)($item['name'] ?? ''), $ignored)) {
                    $names[] = $item;
                }
            }
            if (count($names) > 0) {
                $group['names'] = $names;
                $groups[] = $group;
            }
        }
        return [$proposals, $groups];
    }

    /** Commande au relais (discovery.py ctl, paramètre validé par l'appelant). */
    private function ctl($arg)
    {
        return trim((string)(new Backend())->configdpRun('appalias discovery_ctl', [$arg])) === 'OK';
    }

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

    public function statusAction()
    {
        $data = $this->listData();
        list($proposals, $groups) = $this->proposalsData();
        $counts = [];
        foreach ($proposals as $item) {
            $uuid = (string)($item['app_uuid'] ?? '');
            $counts[$uuid] = ($counts[$uuid] ?? 0) + 1;
        }
        $unassigned = 0;
        foreach ($groups as $group) {
            $unassigned += count($group['names']);
        }
        $mdl = new AppAlias();
        return [
            'enabled' => (string)$mdl->general->discovery_enabled == '1',
            'exported' => !empty($data['enabled']),
            'data' => !empty($data['data']),
            'saved' => $data['saved'] ?? null,
            'observed' => (int)($data['observed'] ?? 0),
            'uncovered' => (int)($data['uncovered'] ?? 0),
            'proposals' => count($proposals),
            'unassigned' => $unassigned,
            'dropped' => (int)($data['dropped'] ?? 0),
            'retention_days' => (int)($data['retention_days'] ?? 0),
            'window' => (int)($data['window'] ?? 0),
            'learn' => $data['learn'] ?? null,
            'counts' => $counts,
            'ignored' => AppAlias::splitList($mdl->general->discovery_ignored),
            'regex_timeout' => !empty($data['regex_timeout']),
            'error' => $data['error'] ?? null,
        ];
    }

    public function proposalsAction()
    {
        $app = trim((string)$this->request->get('app'));
        if ($app !== '' && preg_match(self::UUID_PATTERN, $app) !== 1) {
            return ['error' => gettext('Invalid application.'), 'proposals' => []];
        }
        $min = (int)$this->request->get('min');
        list($proposals, $groups) = $this->proposalsData();
        $out = [];
        foreach ($proposals as $item) {
            if ($app !== '' && ($item['app_uuid'] ?? '') !== $app) {
                continue;
            }
            if ((int)($item['score'] ?? 0) < $min) {
                continue;
            }
            $out[] = $item;
        }
        return ['proposals' => $out];
    }

    public function unassignedAction()
    {
        list($proposals, $groups) = $this->proposalsData();
        return ['groups' => $groups];
    }

    public function acceptAction()
    {
        if (!$this->request->isPost()) {
            return self::failed(gettext('POST required.'));
        }
        $name = AppAlias::normalizeHost($this->request->getPost('name'));
        $uuid = (string)$this->request->getPost('app_uuid');
        $parsed = AppAlias::discoveryRule((string)$this->request->getPost('rule'));
        if (strlen($name) > 253 || preg_match(self::HOST_PATTERN, $name) !== 1) {
            return self::failed(gettext('Invalid name.'));
        }
        if (preg_match(self::UUID_PATTERN, $uuid) !== 1) {
            return self::failed(gettext('Choose an application.'));
        }
        if ($parsed === null) {
            return self::failed(gettext('Invalid rule: use full:name or domain:name.'));
        }
        list($kind, $value) = $parsed;
        // La règle doit porter sur le nom proposé : full:<nom> ou domain:<parent du nom>.
        if (
            ($kind === 'full' && $value !== $name) ||
            ($kind === 'domain' && $value !== $name && !str_ends_with($name, '.' . $value))
        ) {
            return self::failed(gettext('The rule does not match the proposed name.'));
        }
        $rule = $kind . ':' . $value;

        Config::getInstance()->lock();
        try {
            $mdl = new AppAlias();
            // Garde-fou CDN : jamais de domain: qui touche une plateforme partagée.
            $platforms = $mdl->sharedPlatforms();
            if (
                $kind === 'domain' &&
                (AppAlias::sharedRefusal($rule, $platforms) !== null || AppAlias::touchesShared($value, $platforms))
            ) {
                return self::failed(gettext('Shared platform (CDN, cloud, hosting): only full: is accepted here.'));
            }
            $node = $mdl->getNodeByReference('apps.app.' . $uuid);
            if ($node === null) {
                return self::failed(gettext('Application not found.'));
            }
            $domains = AppAlias::splitList($node->domains);
            if (in_array($rule, $domains, true)) {
                return self::failed(gettext('This rule is already in the application.'));
            }
            $domains[] = $rule;
            $node->setNodes(['domains' => implode(',', $domains)]);
            $validations = self::validateAndSave($mdl);
        } finally {
            Config::getInstance()->unlock();
        }
        if (count($validations) > 0) {
            return self::failed(gettext('The application was not saved.'), ['validations' => $validations]);
        }
        return ['result' => 'saved', 'rule' => $rule, 'uuid' => $uuid];
    }

    private function changeIgnored($add)
    {
        if (!$this->request->isPost()) {
            return self::failed(gettext('POST required.'));
        }
        $parsed = AppAlias::discoveryRule((string)$this->request->getPost('rule'));
        if ($parsed === null) {
            return self::failed(gettext('Invalid rule: use full:name or domain:name.'));
        }
        $rule = $parsed[0] . ':' . $parsed[1];
        Config::getInstance()->lock();
        try {
            $mdl = new AppAlias();
            $list = AppAlias::splitList($mdl->general->discovery_ignored);
            if ($add) {
                if (in_array($rule, $list, true)) {
                    return ['result' => 'saved', 'rule' => $rule];
                }
                if (count($list) >= AppAlias::MAX_DISCOVERY_IGNORED) {
                    return self::failed(sprintf(
                        gettext('At most %d ignored discovery rules.'),
                        AppAlias::MAX_DISCOVERY_IGNORED
                    ));
                }
                $list[] = $rule;
            } else {
                $list = array_values(array_diff($list, [$rule]));
            }
            $mdl->general->discovery_ignored->setValue(implode(',', $list));
            $validations = self::validateAndSave($mdl);
        } finally {
            Config::getInstance()->unlock();
        }
        if (count($validations) > 0) {
            return self::failed(gettext('Not saved.'), ['validations' => $validations]);
        }
        return ['result' => 'saved', 'rule' => $rule];
    }

    public function ignoreAction()
    {
        return $this->changeIgnored(true);
    }

    public function unignoreAction()
    {
        return $this->changeIgnored(false);
    }

    public function learnAction()
    {
        if (!$this->request->isPost()) {
            return self::failed(gettext('POST required.'));
        }
        $seconds = (int)$this->request->getPost('seconds');
        $uuid = trim((string)$this->request->getPost('app_uuid'));
        if ($seconds < 1 || $seconds > self::MAX_LEARN) {
            return self::failed(sprintf(gettext('Learning lasts 1 to %d seconds.'), self::MAX_LEARN));
        }
        if ($uuid !== '' && preg_match(self::UUID_PATTERN, $uuid) !== 1) {
            return self::failed(gettext('Invalid application.'));
        }
        $arg = 'learn:' . $seconds . ($uuid !== '' ? ':' . $uuid : '');
        if ($this->ctl($arg)) {
            return ['result' => 'ok'];
        }
        return self::failed(gettext('The relay did not take the command: is it running, with discovery applied?'));
    }

    public function stopLearnAction()
    {
        if (!$this->request->isPost()) {
            return self::failed(gettext('POST required.'));
        }
        return $this->ctl('stop_learn') ? ['result' => 'ok'] :
            self::failed(gettext('The relay did not take the command.'));
    }

    /** Efface les données de découverte (après confirmation dans l'interface). */
    public function purgeAction()
    {
        if (!$this->request->isPost()) {
            return self::failed(gettext('POST required.'));
        }
        return $this->ctl('purge') ? ['result' => 'ok'] : self::failed(gettext('Purge failed, see the log.'));
    }

    /** Demande au relais d'écrire ses données tout de suite (dump). */
    public function refreshAction()
    {
        if (!$this->request->isPost()) {
            return self::failed(gettext('POST required.'));
        }
        return $this->ctl('dump') ? ['result' => 'ok'] :
            self::failed(gettext('The relay did not take the command.'));
    }
}
