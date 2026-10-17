<?php

/*
 * Copyright (C) Les contributeurs d'os-appalias
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

namespace OPNsense\AppAlias;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;
use OPNsense\Core\Config;

/**
 * Modèle os-appalias : réglages du relais et liste des applications.
 */
class AppAlias extends BaseModel
{
    /** Marqueur placé dans la description des alias gérés. */
    const MARKER = '[os-appalias]';

    /** Description complète des alias gérés. */
    const ALIAS_DESCRIPTION = '[os-appalias] géré automatiquement - ne pas éditer';

    /** Longueur maximale d'un nom de table pf. */
    const MAX_TABLE_LENGTH = 31;

    /**
     * Noms des alias Firewall existants, avec un indicateur « géré ».
     * @return array nom => bool
     */
    public static function firewallAliases()
    {
        $result = [];
        $cfg = Config::getInstance()->object();
        if (
            isset($cfg->OPNsense->Firewall->Alias->aliases) &&
            isset($cfg->OPNsense->Firewall->Alias->aliases->alias)
        ) {
            foreach ($cfg->OPNsense->Firewall->Alias->aliases->alias as $alias) {
                $result[(string)$alias->name] = strpos((string)$alias->description, self::MARKER) === 0;
            }
        }
        return $result;
    }

    /**
     * Nom des alias Firewall gérés, indexés par uuid.
     * @return array uuid => nom
     */
    public static function managedAliasesByUuid()
    {
        $result = [];
        $cfg = Config::getInstance()->object();
        if (
            isset($cfg->OPNsense->Firewall->Alias->aliases) &&
            isset($cfg->OPNsense->Firewall->Alias->aliases->alias)
        ) {
            foreach ($cfg->OPNsense->Firewall->Alias->aliases->alias as $alias) {
                if (strpos((string)$alias->description, self::MARKER) === 0) {
                    $result[(string)$alias['uuid']] = (string)$alias->name;
                }
            }
        }
        return $result;
    }

    /**
     * Texte de config.xml sans la section des alias ni celle d'AppAlias.
     */
    public static function configTextWithoutAliases()
    {
        $doc = new \DOMDocument();
        $doc->loadXML(Config::getInstance()->object()->asXML());
        $xpath = new \DOMXPath($doc);
        foreach (['/opnsense/OPNsense/Firewall/Alias', '/opnsense/OPNsense/appalias'] as $query) {
            foreach (iterator_to_array($xpath->query($query)) as $node) {
                $node->parentNode->removeChild($node);
            }
        }
        return $doc->saveXML();
    }

    /**
     * Vrai si l'alias $name est référencé hors de la section des alias
     * (règles, NAT, etc.) ou dans le contenu d'un autre alias (groupe).
     * $configText peut être fourni pour éviter de le recalculer.
     */
    public static function isAliasReferenced($name, $configText = null)
    {
        if ($configText === null) {
            $configText = self::configTextWithoutAliases();
        }
        $re = '/(?<![A-Za-z0-9_])' . preg_quote($name, '/') . '(?![A-Za-z0-9_])/';
        if (preg_match($re, $configText) === 1) {
            return true;
        }
        $cfg = Config::getInstance()->object();
        if (
            isset($cfg->OPNsense->Firewall->Alias->aliases) &&
            isset($cfg->OPNsense->Firewall->Alias->aliases->alias)
        ) {
            foreach ($cfg->OPNsense->Firewall->Alias->aliases->alias as $alias) {
                if ((string)$alias->name !== $name && preg_match($re, (string)$alias->content) === 1) {
                    return true;
                }
            }
        }
        return false;
    }

    /**
     * Découpe une liste CSV (virgules ou retours à la ligne).
     */
    public static function splitList($value)
    {
        $items = preg_split('/[,\n\r]+/', (string)$value);
        return array_values(array_filter(array_map('trim', $items), 'strlen'));
    }

    /**
     * Vrai si la règle de domaine est valide (domain:, full:, keyword:, regexp: ou nom seul).
     */
    private static function validDomainRule($rule)
    {
        $host = '/^(\*\.)?([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)*[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.?$/i';
        if (preg_match('/^(domain|full|keyword|regexp):(.*)$/i', $rule, $m)) {
            $kind = strtolower($m[1]);
            $value = $m[2];
            if ($value === '') {
                return false;
            }
            if ($kind === 'keyword') {
                return preg_match('/^[a-z0-9._-]+$/i', $value) === 1;
            }
            if ($kind === 'regexp') {
                // accepté, ignoré par le démon dans cette version
                return strpos($value, ',') === false;
            }
            return strlen($value) <= 253 && preg_match($host, $value) === 1;
        }
        return strlen($rule) <= 253 && preg_match($host, $rule) === 1;
    }

    /**
     * Vrai si « ip:port » ou « [ipv6]:port ».
     */
    private static function validHostPort($item)
    {
        if (preg_match('/^\[([0-9a-f:.]+)\]:([0-9]{1,5})$/i', $item, $m)) {
            $ok = filter_var($m[1], FILTER_VALIDATE_IP, FILTER_FLAG_IPV6) !== false;
        } elseif (preg_match('/^([0-9.]+):([0-9]{1,5})$/', $item, $m)) {
            $ok = filter_var($m[1], FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) !== false;
        } else {
            return false;
        }
        return $ok && (int)$m[2] >= 1 && (int)$m[2] <= 65535;
    }

    /**
     * {@inheritdoc}
     */
    public function performValidation($validateFullModel = false)
    {
        $messages = parent::performValidation($validateFullModel);

        $general = $this->general;
        if ($validateFullModel || $general->upstream_fallback->isFieldChanged()) {
            foreach (self::splitList($general->upstream_fallback) as $item) {
                if (!self::validHostPort($item)) {
                    $messages->appendMessage(new Message(
                        sprintf(gettext('Amont de secours invalide : %s (attendu ip:port ou [ipv6]:port).'), $item),
                        $general->upstream_fallback->__reference
                    ));
                }
            }
        }

        $prefix = (string)$general->alias_prefix;
        $prefixChanged = $general->alias_prefix->isFieldChanged();
        $aliases = null;
        $managedByUuid = null;
        $configText = null;

        foreach ($this->apps->app->iterateItems() as $app) {
            if (!$validateFullModel && !$prefixChanged && !$app->isFieldChanged()) {
                continue;
            }
            $key = $app->__reference;
            $name = (string)$app->name;
            $table = $prefix . $name;

            if (strlen($table) > self::MAX_TABLE_LENGTH) {
                $messages->appendMessage(new Message(
                    sprintf(
                        gettext('Le nom de l\'alias « %s » dépasse %d caractères (préfixe compris).'),
                        $table,
                        self::MAX_TABLE_LENGTH
                    ),
                    $key . '.name'
                ));
            }

            $domains = self::splitList($app->domains);
            $networks = self::splitList($app->networks);
            $asns = self::splitList($app->asns);
            if (count($domains) + count($networks) + count($asns) == 0) {
                $messages->appendMessage(new Message(
                    gettext('Indiquer au moins une source : domaines, réseaux ou ASN.'),
                    $key . '.domains'
                ));
            }

            foreach ($domains as $rule) {
                if (!self::validDomainRule($rule)) {
                    $messages->appendMessage(new Message(
                        sprintf(gettext('Règle de domaine invalide : %s'), $rule),
                        $key . '.domains'
                    ));
                }
            }

            foreach ($asns as $asn) {
                if (!preg_match('/^(AS)?[0-9]{1,10}$/i', $asn)) {
                    $messages->appendMessage(new Message(
                        sprintf(gettext('ASN invalide : %s'), $asn),
                        $key . '.asns'
                    ));
                }
            }

            if ($name !== '') {
                if ($aliases === null) {
                    $aliases = self::firewallAliases();
                }
                if (isset($aliases[$table]) && !$aliases[$table]) {
                    $messages->appendMessage(new Message(
                        sprintf(
                            gettext('Un alias Firewall « %s » existe déjà et n\'est pas géré par os-appalias.'),
                            $table
                        ),
                        $key . '.name'
                    ));
                }
            }

            /*
             * Renommage (nom ou préfixe) : refusé si l'alias géré actuel est
             * encore utilisé, sinon les règles qui le citent pointeraient
             * vers un alias vidé.
             */
            $aliasUuid = (string)$app->alias_uuid;
            if ($aliasUuid !== '') {
                if ($managedByUuid === null) {
                    $managedByUuid = self::managedAliasesByUuid();
                }
                if (isset($managedByUuid[$aliasUuid]) && $managedByUuid[$aliasUuid] !== $table) {
                    $oldName = $managedByUuid[$aliasUuid];
                    if ($configText === null) {
                        $configText = self::configTextWithoutAliases();
                    }
                    if (self::isAliasReferenced($oldName, $configText)) {
                        $messages->appendMessage(new Message(
                            sprintf(
                                gettext(
                                    'Renommage impossible : l\'alias « %s » est encore utilisé (règles, NAT ou groupe). ' .
                                    'Retirer d\'abord ces références, ou garder le nom et le préfixe actuels.'
                                ),
                                $oldName
                            ),
                            $prefixChanged && $app->name->isFieldChanged() === false
                                ? $general->alias_prefix->__reference
                                : $key . '.name'
                        ));
                    }
                }
            }
        }

        return $messages;
    }
}
