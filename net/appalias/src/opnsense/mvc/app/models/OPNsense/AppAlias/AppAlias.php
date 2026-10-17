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

    /** Plateformes partagées : source unique, lue aussi par le relais Python. */
    const SHARED_PLATFORMS_FILE = '/usr/local/opnsense/scripts/OPNsense/AppAlias/data/shared_platforms.txt';

    /** Référence de liste du catalogue v2fly (« netflix@-ads »). */
    const CATALOG_REF_PATTERN = '/^[a-z0-9-]+(@-?[a-z0-9-]+)*$/';

    /** Contenu de SHARED_PLATFORMS_FILE, lu une fois par requête. */
    private static $sharedPlatformsFile = null;

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
     * Nom d'hôte normalisé : minuscules, point final et « *. » de tête retirés.
     */
    public static function normalizeHost($name)
    {
        $name = rtrim(strtolower(trim((string)$name)), '.');
        if (substr($name, 0, 2) === '*.') {
            $name = substr($name, 2);
        }
        return $name;
    }

    /**
     * Plateformes partagées du fichier data/shared_platforms.txt.
     * @return array liste de noms
     */
    public static function sharedPlatformsFromFile()
    {
        if (self::$sharedPlatformsFile === null) {
            $names = [];
            $text = @file_get_contents(self::SHARED_PLATFORMS_FILE);
            if ($text !== false) {
                foreach (preg_split('/\R/', $text) as $line) {
                    $line = self::normalizeHost(explode('#', $line, 2)[0]);
                    if ($line !== '') {
                        $names[$line] = true;
                    }
                }
            }
            self::$sharedPlatformsFile = array_keys($names);
        }
        return self::$sharedPlatformsFile;
    }

    /**
     * Plateformes partagées : fichier, plus le réglage shared_platforms_extra.
     * @return array liste de noms
     */
    public function sharedPlatforms()
    {
        $names = array_fill_keys(self::sharedPlatformsFromFile(), true);
        foreach (self::splitList($this->general->shared_platforms_extra) as $item) {
            $item = self::normalizeHost($item);
            if ($item !== '') {
                $names[$item] = true;
            }
        }
        return array_keys($names);
    }

    /**
     * Refus d'une règle « domain: » (ou nom seul) trop large :
     * « tld » pour un domaine de premier niveau seul, « shared » quand le nom
     * est égal à une plateforme partagée ou parent de l'une d'elles, sinon null.
     * « domain:d123.cloudfront.net » et « full:… » ne sont jamais refusés ici.
     */
    public static function sharedRefusal($rule, $platforms)
    {
        $rule = trim((string)$rule);
        if (preg_match('/^([a-z]+):(.*)$/i', $rule, $m)) {
            if (strtolower($m[1]) !== 'domain') {
                return null;
            }
            $rule = $m[2];
        }
        $value = self::normalizeHost($rule);
        if ($value === '') {
            return null;
        }
        if (strpos($value, '.') === false) {
            return 'tld';
        }
        foreach ($platforms as $platform) {
            if ($platform === $value || str_ends_with($platform, '.' . $value)) {
                return 'shared';
            }
        }
        return null;
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
                // règle de découverte : conservée, jamais appliquée par le relais
                return strpos($value, ',') === false;
            }
            return strlen($value) <= 253 && preg_match($host, $value) === 1;
        }
        return strlen($rule) <= 253 && preg_match($host, $rule) === 1;
    }

    /**
     * Vrai si $name est un nom d'hôte (labels LDH, 253 caractères au plus,
     * point final toléré) ou une adresse IP : nom TLS d'un amont.
     */
    public static function validTlsName($name)
    {
        $name = trim((string)$name);
        if ($name === '') {
            return false;
        }
        if (filter_var($name, FILTER_VALIDATE_IP) !== false) {
            return true;
        }
        $name = rtrim($name, '.');
        $label = '[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?';
        return strlen($name) > 0 && strlen($name) <= 253 &&
            preg_match('/^(' . $label . '\.)*' . $label . '$/i', $name) === 1;
    }

    /**
     * Vrai si l'amont de secours est valide : « ip », « ip:port », « [ipv6] »,
     * « [ipv6]:port » ou IPv6 nue, suivi éventuellement de « #nom-tls »
     * (syntaxe à la Unbound). Sans port, celui du protocole est employé.
     */
    private static function validUpstream($item)
    {
        $item = trim((string)$item);
        $hash = strpos($item, '#');
        if ($hash !== false) {
            if (!self::validTlsName(substr($item, $hash + 1))) {
                return false;
            }
            $item = substr($item, 0, $hash);
        }
        $port = null;
        if (preg_match('/^\[([0-9a-f:.]+)\](?::([0-9]{1,5}))?$/i', $item, $m)) {
            $ok = filter_var($m[1], FILTER_VALIDATE_IP, FILTER_FLAG_IPV6) !== false;
            $port = isset($m[2]) ? $m[2] : null;
        } elseif (preg_match('/^([0-9.]+)(?::([0-9]{1,5}))?$/', $item, $m)) {
            $ok = filter_var($m[1], FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) !== false;
            $port = isset($m[2]) ? $m[2] : null;
        } else {
            $ok = filter_var($item, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6) !== false;
        }
        if ($ok && $port !== null && $port !== '') {
            $ok = (int)$port >= 1 && (int)$port <= 65535;
        }
        return $ok;
    }

    /**
     * Port explicite d'un amont de secours (« ip:port », « [ipv6]:port »), ou
     * null s'il n'y en a pas. À appeler sur un amont déjà reconnu valide.
     */
    private static function explicitPort($item)
    {
        $item = trim((string)$item);
        $hash = strpos($item, '#');
        if ($hash !== false) {
            $item = substr($item, 0, $hash);
        }
        if (
            preg_match('/^\[[0-9a-f:.]+\]:([0-9]{1,5})$/i', $item, $m) ||
            preg_match('/^[0-9.]+:([0-9]{1,5})$/', $item, $m)
        ) {
            return (int)$m[1];
        }
        return null;
    }

    /**
     * Message de refus si le port ne correspond pas au protocole (53 en DNS over TLS,
     * 853 en DNS clair), sinon null. Même règle que lib/config.py.
     */
    private static function protocolPortRefusal($dot, $port)
    {
        if ($dot && $port === 53) {
            return gettext('DNS over TLS uses port 853; port 53 only speaks plain DNS.');
        }
        if (!$dot && $port === 853) {
            return gettext('Plain DNS uses port 53; port 853 only speaks DNS over TLS.');
        }
        return null;
    }

    /**
     * {@inheritdoc}
     */
    public function performValidation($validateFullModel = false)
    {
        $messages = parent::performValidation($validateFullModel);

        $general = $this->general;
        $dot = (string)$general->upstream_protocol === 'dot';
        $tlsName = trim((string)$general->upstream_tls_name);
        if (
            $validateFullModel ||
            $general->upstream_fallback->isFieldChanged() ||
            $general->upstream_protocol->isFieldChanged() ||
            $general->upstream_tls_name->isFieldChanged()
        ) {
            foreach (self::splitList($general->upstream_fallback) as $item) {
                if (!self::validUpstream($item)) {
                    $messages->appendMessage(new Message(
                        sprintf(
                            gettext(
                                'Invalid fallback upstream: %s (expected ip, ip:port, [ipv6] or [ipv6]:port, ' .
                                'optionally followed by #tls-name).'
                            ),
                            $item
                        ),
                        $general->upstream_fallback->__reference
                    ));
                } else {
                    $refusal = self::protocolPortRefusal($dot, self::explicitPort($item));
                    if ($refusal !== null) {
                        $messages->appendMessage(new Message(
                            $item . ': ' . $refusal,
                            $general->upstream_fallback->__reference
                        ));
                    }
                }
            }
        }

        // Port de l'amont principal cohérent avec le protocole.
        if (
            $validateFullModel ||
            $general->upstream_port->isFieldChanged() ||
            $general->upstream_protocol->isFieldChanged()
        ) {
            $refusal = self::protocolPortRefusal($dot, (int)(string)$general->upstream_port);
            if ($refusal !== null) {
                $messages->appendMessage(new Message(
                    $refusal,
                    $general->upstream_port->__reference
                ));
            }
        }

        // DNS over TLS : nom TLS obligatoire (certificat vérifié, SNI).
        if (
            $validateFullModel ||
            $general->upstream_protocol->isFieldChanged() ||
            $general->upstream_tls_name->isFieldChanged()
        ) {
            if ($dot && $tlsName === '') {
                $messages->appendMessage(new Message(
                    gettext('A TLS name is required with DNS over TLS, for example dns.quad9.net.'),
                    $general->upstream_tls_name->__reference
                ));
            } elseif ($tlsName !== '' && !self::validTlsName($tlsName)) {
                $messages->appendMessage(new Message(
                    sprintf(gettext('Invalid TLS name: %s'), $tlsName),
                    $general->upstream_tls_name->__reference
                ));
            }
        }

        // Le champ n'est plus Required dans le modèle (la migration d'installation
        // enregistre les valeurs par défaut) : contrôle porté ici.
        if (
            $validateFullModel ||
            $general->enabled->isFieldChanged() ||
            $general->upstream_address->isFieldChanged()
        ) {
            if ((string)$general->enabled === '1' && trim((string)$general->upstream_address) === '') {
                $messages->appendMessage(new Message(
                    gettext('An upstream resolver is required when the service is enabled.'),
                    $general->upstream_address->__reference
                ));
            }
        }

        $prefix = (string)$general->alias_prefix;
        $prefixChanged = $general->alias_prefix->isFieldChanged();
        $aliases = null;
        $managedByUuid = null;
        $configText = null;
        $platforms = null;

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
                        gettext('The alias name "%s" exceeds %d characters (prefix included).'),
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
                    gettext('Enter at least one source: domains, networks or ASNs.'),
                    $key . '.domains'
                ));
            }

            foreach ($domains as $rule) {
                if (!self::validDomainRule($rule)) {
                    $messages->appendMessage(new Message(
                        sprintf(gettext('Invalid domain rule: %s'), $rule),
                        $key . '.domains'
                    ));
                    continue;
                }
                if ($platforms === null) {
                    $platforms = $this->sharedPlatforms();
                }
                $refusal = self::sharedRefusal($rule, $platforms);
                if ($refusal === 'tld') {
                    $messages->appendMessage(new Message(
                        sprintf(
                            gettext('Rule %s covers a whole top-level domain: use full: for an exact name.'),
                            $rule
                        ),
                        $key . '.domains'
                    ));
                } elseif ($refusal === 'shared') {
                    $messages->appendMessage(new Message(
                        sprintf(
                            gettext(
                                'Rule %s covers a shared platform (CDN, cloud or hosting) used by many other ' .
                                'services: use full: for an exact name, or a dedicated sub-domain.'
                            ),
                            $rule
                        ),
                        $key . '.domains'
                    ));
                }
            }

            foreach (self::splitList($app->catalog_refs) as $ref) {
                if (strlen($ref) > 128 || preg_match(self::CATALOG_REF_PATTERN, $ref) !== 1) {
                    $messages->appendMessage(new Message(
                        sprintf(gettext('Invalid catalog reference: %s'), $ref),
                        $key . '.catalog_refs'
                    ));
                }
            }

            foreach ($asns as $asn) {
                if (!preg_match('/^(AS)?[0-9]{1,10}$/i', $asn)) {
                    $messages->appendMessage(new Message(
                        sprintf(gettext('Invalid ASN: %s'), $asn),
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
                            gettext('A firewall alias "%s" already exists and is not managed by os-appalias.'),
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
                                    'Cannot rename: the alias "%s" is still in use (rules, NAT or alias group). ' .
                                    'Remove these references first, or keep the current name and prefix.'
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
