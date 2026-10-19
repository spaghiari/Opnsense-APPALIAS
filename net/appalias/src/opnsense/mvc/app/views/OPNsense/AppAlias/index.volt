{#
 # Copyright (C) 2026 spaghiari
 # All rights reserved.
 #
 # Redistribution and use in source and binary forms, with or without
 # modification, are permitted provided that the following conditions are met:
 #
 # 1. Redistributions of source code must retain the above copyright notice,
 #    this list of conditions and the following disclaimer.
 #
 # 2. Redistributions in binary form must reproduce the above copyright
 #    notice, this list of conditions and the following disclaimer in the
 #    documentation and/or other materials provided with the distribution.
 #
 # THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 # INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 # AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 # AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 # OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 # SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 # INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 # CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 # ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 # POSSIBILITY OF SUCH DAMAGE.
 #}

<style>
    /* Aperçu d'une liste du catalogue */
    .appalias-rules { max-height: 50vh; overflow-y: auto; }
    .appalias-rules .checkbox { margin: 2px 0; }
    .appalias-rules .label { margin-left: 4px; }
    .appalias-group-head { margin-top: 12px; }

    /* Colonne « rules » de la grille : compteur court sur une ligne, lignes cliquables */
    .appalias-grid-count { white-space: nowrap; }
    #grid-apps tbody tr, #grid-apps .tabulator-row { cursor: pointer; }
    /* Lignes flexibles : éléments centrés verticalement, espacés sans marges */
    .appalias-flex { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
    .appalias-small { color: inherit; opacity: .7; font-size: 12px; }
    .appalias-tabular { font-variant-numeric: tabular-nums; white-space: nowrap; }

    /* Badge du relais */
    .appalias-relay { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0; }
    .appalias-relay .label { font-size: 12px; }
    .appalias-relay .alert { flex-basis: 100%; margin: 0; }

    .appalias-toolbar-actions { display: flex; align-items: center; gap: 4px; margin-left: auto; }

    /* Cartes du catalogue : même hauteur par ligne, bouton en bas */
    .appalias-cards { display: flex; flex-wrap: wrap; margin: 0; }
    .appalias-cards > div { display: flex; }
    .appalias-card { display: flex; flex-direction: column; width: 100%; margin-bottom: 10px; }
    .appalias-card .panel-body { display: flex; flex: 1; flex-direction: column; gap: 4px; padding: 10px; }
    .appalias-card-head { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
    .appalias-card-head strong { margin-right: 4px; word-break: break-all; }
    .appalias-card-head .badge { margin-left: auto; }
    .appalias-card-foot { margin-top: auto; padding-top: 6px; }

    /* Galerie « Popular apps » : titre de catégorie à gauche, boutons à droite */
    .appalias-popular {
        display: grid; grid-template-columns: 160px minmax(0, 1fr); gap: 6px 12px;
        align-items: start; padding: 6px 0; border-bottom: 1px solid rgba(128, 128, 128, .25);
    }
    .appalias-popular:last-child { border-bottom: 0; }
    .appalias-popular-title { font-weight: 600; line-height: 30px; }
    .appalias-popular-lists { display: flex; flex-wrap: wrap; gap: 4px; }

    /* Provenance du catalogue */
    .appalias-source { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    .appalias-source p { flex-basis: 100%; margin: 0; }

    .tab-content .appalias-title { margin: 24px 0 10px; font-size: 18px; font-weight: 500; line-height: 1.2; }
    .tab-content summary.appalias-title, .tab-content h3.appalias-title { display: block; color: inherit; font-size: 18px; font-weight: 500; }
    .appalias-title .badge { margin-left: 6px; vertical-align: middle; }

    /* Section « Automatic updates » */
    .appalias-follow-entry {
        display: grid; grid-template-columns: 140px minmax(0, 1fr) auto;
        align-items: center; gap: 4px 12px; padding: 6px 0; border-bottom: 1px solid rgba(128, 128, 128, .25);
    }
    .appalias-follow-entry:last-child { border-bottom: 0; }
    .appalias-follow-entry details > summary { cursor: pointer; color: inherit; opacity: .7; font-size: 12px; }
    .appalias-follow-rules { margin-top: 4px; font-size: 12px; }
    .appalias-follow-rules div { word-break: break-all; }

    @media (max-width: 767px) {
        .appalias-toolbar-actions { flex-wrap: wrap; }
        .appalias-toolbar-actions { margin-left: 0; }
        .appalias-popular { grid-template-columns: 1fr; }
        .appalias-popular-title { line-height: normal; }
        .appalias-follow-entry { grid-template-columns: 1fr; }
        .appalias-cards > div { width: 100%; }
    }
</style>

<script>
    $( document ).ready(function () {
        const T = {
            rules: "{{ lang._('rules')|escape_js }}",
            ruleOne: "{{ lang._('rule')|escape_js }}",
            andMore: "{{ lang._('... and %d more')|escape_js }}",
            includes: "{{ lang._('includes:')|escape_js }}",
            preview: "{{ lang._('Preview')|escape_js }}",
            broken: "{{ lang._('Broken list (not importable):')|escape_js }}",
            missing: "{{ lang._('Missing included lists:')|escape_js }}",
            unknown: "{{ lang._('Unknown lines ignored:')|escape_js }}",
            noResult: "{{ lang._('No list found.')|escape_js }}",
            byDomain: "{{ lang._('contains')|escape_js }}",
            refusedTld: "{{ lang._('Refused: a domain rule on a whole top-level domain. Use full: instead.')|escape_js }}",
            refusedShared: "{{ lang._('Refused: a domain rule on a shared platform. Use full: or a dedicated sub-domain.')|escape_js }}",
            refusedComma: "{{ lang._('Refused: a comma cannot be stored in the rule list.')|escape_js }}",
            tooMany: "{{ lang._('Too many rules for one import (1000 at most).')|escape_js }}",
            followedRules: "{{ lang._('rules followed:')|escape_js }}",
            followedList: "{{ lang._('Rules followed')|escape_js }}",
            excludedList: "{{ lang._('Rules not followed')|escape_js }}",
            exclUnapplied: "{{ lang._('rule not applied by the relay')|escape_js }}",
            exclAds: "{{ lang._('advertising')|escape_js }}",
            exclShared: "{{ lang._('shared platform')|escape_js }}",
            exclRefused: "{{ lang._('refused')|escape_js }}",
            adding: "{{ lang._('Adding the application and applying the configuration...')|escape_js }}",
            addedApplied: "{{ lang._('Application added and applied. It now updates itself with the catalog.')|escape_js }}",
            addedNotApplied: "{{ lang._('Application added, but applying the configuration failed. Click Apply to retry.')|escape_js }}",
            capped: "{{ lang._('Catalog update paused: this application would exceed 1000 rules')|escape_js }}",
            cappedTrim: "{{ lang._('Trim its rules to resume.')|escape_js }}",
            failed: "{{ lang._('Not saved:')|escape_js }}",
            updates: "{{ lang._('catalog updates')|escape_js }}",
            added: "{{ lang._('New in the catalog')|escape_js }}",
            removed: "{{ lang._('Removed from the catalog')|escape_js }}",
            custom: "{{ lang._('Rules added by hand (kept):')|escape_js }}",
            diffRefused: "{{ lang._('New catalog rules not offered (shared platform or top-level domain):')|escape_js }}",
            noChange: "{{ lang._('No change.')|escape_js }}",
            catalogError: "{{ lang._('Catalog error:')|escape_js }}",
            integrity: "{{ lang._('Integrity check failed: the embedded catalog differs from its manifest.')|escape_js }}",
            lists: "{{ lang._('lists')|escape_js }}",
            applied: "{{ lang._('Configuration saved and applied.')|escape_js }}",
            relay: "{{ lang._('Relay:')|escape_js }}",
            appsEnabled: "{{ lang._('applications enabled.')|escape_js }}",
            ubUnknown: "{{ lang._('No forwarding status yet: click Apply.')|escape_js }}",
            ubOff: "{{ lang._('Automatic forwarding is off (or the service is disabled): no zone is forwarded to the relay. Check Forward application domains from Unbound below, then Apply, to send only the application domains to the relay.')|escape_js }}",
            ubForwarded: "{{ lang._('domain(s) forwarded by Unbound to the relay')|escape_js }}",
            ubErrors: "{{ lang._('Errors (see the log):')|escape_js }}",
            ubRestartTitle: "{{ lang._('Restart Unbound')|escape_js }}",
            ubRestartConfirm: "{{ lang._('Restart Unbound now? DNS resolution is interrupted for a few seconds.')|escape_js }}",
            ubRestartFailed: "{{ lang._('Unbound restart failed:')|escape_js }}",
            ubRestarted: "{{ lang._('Unbound restarted.')|escape_js }}",
            ubSkipped: "{{ lang._('name(s) not forwarded:')|escape_js }}",
            upDot: "{{ lang._('Upstream: DNS over TLS')|escape_js }}",
            upPlain: "{{ lang._('Upstream: plain DNS')|escape_js }}",
            latUpstream: "{{ lang._('Upstream')|escape_js }}",
            latPf: "{{ lang._('pf')|escape_js }}",
            latNone: "{{ lang._('no measurement yet')|escape_js }}",
            latSamples: "{{ lang._('samples')|escape_js }}",
            upPort853: "{{ lang._('Port set to 853 for DNS over TLS')|escape_js }}",
            upPort53: "{{ lang._('Port set to 53 for plain DNS')|escape_js }}",
            ubRootWarn: "{{ lang._('While the relay is down, forwarded domains fall back to direct recursion and bypass DNS over TLS (and the . Query Forwarding). If outgoing port 53 is blocked, they fail until the relay is back.')|escape_js }}",
            commit: "{{ lang._('commit')|escape_js }}",
            stRunning: "{{ lang._('Running')|escape_js }}",
            stStopped: "{{ lang._('Stopped')|escape_js }}",
            stDisabled: "{{ lang._('Stopped (disabled)')|escape_js }}",
            stConfigError: "{{ lang._('Config error')|escape_js }}",
            stPid: "{{ lang._('PID')|escape_js }}",
            stVersion: "{{ lang._('version')|escape_js }}",
            stRestart: "{{ lang._('Restart needed: the relay still runs version %s, version %s is installed. Restart AppAlias to use the new code.')|escape_js }}",
            catEmbedded: "{{ lang._('Embedded snapshot')|escape_js }}",
            catOnline: "{{ lang._('Downloaded')|escape_js }}",
            catUpdateTitle: "{{ lang._('Update catalog now')|escape_js }}",
            catUpdateConfirm: "{{ lang._('Download the latest v2fly catalog from GitHub now? The firewall contacts api.github.com and codeload.github.com. Applications that follow the catalog (auto) receive the changes right away; the others show them as catalog updates to review.')|escape_js }}",
            catUpdated: "{{ lang._('Catalog downloaded:')|escape_js }}",
            catUnchanged: "{{ lang._('The catalog is already up to date.')|escape_js }}",
            catAddedLists: "{{ lang._('lists added')|escape_js }}",
            catRemovedLists: "{{ lang._('lists removed')|escape_js }}",
            catUpdateFailed: "{{ lang._('Catalog update failed, the previous catalog is kept:')|escape_js }}",
            catResetTitle: "{{ lang._('Revert to embedded')|escape_js }}",
            catResetConfirm: "{{ lang._('Delete the downloaded catalog and use the embedded snapshot again?')|escape_js }}",
            catResetDone: "{{ lang._('The embedded snapshot is used again.')|escape_js }}",
            auto: "{{ lang._('auto')|escape_js }}",
            autoHelp: "{{ lang._('Follows the catalog automatically: catalog changes are applied after each catalog update.')|escape_js }}",
            proactive: "{{ lang._('proactive')|escape_js }}",
            proactiveHelp: "{{ lang._('Proactive resolution: the relay resolves the names of this application at regular intervals.')|escape_js }}",
            fNone: "{{ lang._('No automatic change yet.')|escape_js }}",
            fUndo: "{{ lang._('Undo')|escape_js }}",
            fUndone: "{{ lang._('undone')|escape_js }}",
            fUndoKind: "{{ lang._('undo')|escape_js }}",
            fStarted: "{{ lang._('Automatic updates of followed applications started: see the log below.')|escape_js }}",
            fUndoOf: "{{ lang._('undo of')|escape_js }}",
            fDeleted: "{{ lang._('The application was deleted.')|escape_js }}",
            fDetails: "{{ lang._('details')|escape_js }}",
            fUndoTitle: "{{ lang._('Undo an automatic change')|escape_js }}",
            fUndoConfirm: "{{ lang._('Undo this change and apply now? Added rules are removed (and not added again by the next updates), removed rules come back as rules added by hand.')|escape_js }}",
            fUndoDone: "{{ lang._('Change undone and applied.')|escape_js }}",
            fUndoFailed: "{{ lang._('Undo failed:')|escape_js }}",
            fApplyFailed: "{{ lang._('Saved, but applying failed at step:')|escape_js }}",
            fUpdated: "{{ lang._('application(s) following the catalog updated and applied.')|escape_js }}",
            fErrors: "{{ lang._('application(s) not updated automatically, see the log.')|escape_js }}"
        };
        const MAX_IMPORT = 1000;

        let appStatus = {};
        let catalogDiff = {};
        let firstStatus = true;
        let gridLoaded = false;

        function reloadGridIfLoaded() {
            if (gridLoaded) {
                $("#grid-apps").bootgrid('reload');
            }
        }

        function refreshStatus(reloadGrid) {
            ajaxGet('/api/appalias/service/status', {}, function (data, status) {
                appStatus = (data && data.apps) ? data.apps : {};
                // Premier statut : la grille a pu être rendue avant (course), la recharger une fois.
                if (firstStatus) {
                    firstStatus = false;
                    if (gridLoaded && !reloadGrid) {
                        reloadGrid = true;
                    }
                }
                if (reloadGrid) {
                    $("#grid-apps").bootgrid('reload');
                }
            });
        }

        function refreshDiff() {
            ajaxGet('/api/appalias/catalog/diff', {}, function (data, status) {
                catalogDiff = (data && data.apps) ? data.apps : {};
                reloadGridIfLoaded();
            });
        }

        function splitList(value) {
            if (Array.isArray(value)) {
                return value;
            }
            return String(value || '').split(',').map(function (s) { return s.trim(); }).filter(function (s) { return s !== ''; });
        }

        // Valeur d'un champ liste renvoyée par getApp : chaîne CSV, ou objet {valeur: {...}}.
        function hasValues(value) {
            if (value === null || value === undefined) {
                return false;
            }
            if (typeof value === 'object') {
                return Object.keys(value).some(function (key) { return key !== ''; });
            }
            return splitList(value).length > 0;
        }

        function isOn(value) {
            return value === true || value === 1 || String(value) === '1';
        }

        function errorText(data) {
            let parts = [];
            if (data && data.message) {
                parts.push(String(data.message));
            }
            if (data && data.error) {
                parts.push(String(data.error));
            }
            if (data && data.validations) {
                $.each(data.validations, function (key, msg) {
                    parts.push(String(msg));
                });
            }
            return parts.join(' ');
        }

        $("#grid-apps").UIBootgrid({
            search: '/api/appalias/settings/searchApp/',
            get: '/api/appalias/settings/getApp/',
            set: '/api/appalias/settings/setApp/',
            add: '/api/appalias/settings/addApp/',
            del: '/api/appalias/settings/delApp/',
            toggle: '/api/appalias/settings/toggleApp/',
            options: {
                formatters: {
                    rules: function (column, row) {
                        // compteur court ; la liste complete reste dans l'infobulle (5 regles au plus)
                        let parts = splitList(row[column.id]);
                        let n = parts.length;
                        let out = $('<span class="appalias-grid-count"></span>')
                            .text(n + ' ' + (n === 1 ? T.ruleOne : T.rules));
                        if (n === 0) {
                            out.addClass('text-muted');
                        } else {
                            let tip = parts.slice(0, 5).join(', ');
                            if (n > 5) {
                                tip += ' ' + T.andMore.replace('%d', String(n - 5));
                            }
                            out.attr('title', tip);
                        }
                        return out.prop('outerHTML');
                    },
                    appname: function (column, row) {
                        let out = $('<span class="appalias-flex"></span>').append($('<span></span>').text(row.name || ''));
                        let refs = splitList(row.catalog_refs);
                        if (refs.length > 0) {
                            // un seul badge v2fly par application ; les references dans l'infobulle
                            out.append(
                                $('<span class="label label-info"></span>')
                                    .attr('title', refs.join(', '))
                                    .text('v2fly' + (refs.length > 1 ? ' (' + refs.length + ')' : ''))
                            );
                        }
                        let follow = refs.length > 0 && isOn(row.catalog_follow);
                        if (follow) {
                            out.append($('<span class="label label-primary"></span>').attr('title', T.autoHelp).text(T.auto));
                        }
                        // badge seulement si le champ est fourni par la grille et actif
                        if (row.proactive !== undefined && isOn(row.proactive)) {
                            out.append($('<span class="label label-default"></span>').attr('title', T.proactiveHelp).text(T.proactive));
                        }
                        let d = catalogDiff[row.uuid];
                        if (d && d.error) {
                            out.append(
                                $('<i class="fa fa-exclamation-triangle text-warning"></i>').attr('title', T.catalogError + ' ' + d.error)
                            );
                        } else if (d && follow && d.capped) {
                            out.append(
                                $('<i class="fa fa-exclamation-triangle text-warning"></i>')
                                    .attr('title', T.capped + ' (' + parseInt(d.projected || 0) + '). ' + T.cappedTrim)
                            );
                        } else if (d && !follow) {
                            // diff manuel : seulement pour les applications qui ne suivent pas le catalogue
                            let n = (d.added || []).length + (d.removed || []).length;
                            if (n > 0) {
                                out.append(
                                    $('<a href="#" class="appalias-diff-link label label-warning"></a>')
                                        .attr('data-uuid', row.uuid)
                                        .text(n + ' ' + T.updates)
                                );
                            }
                        }
                        return out.prop('outerHTML');
                    },
                    ipcount: function (column, row) {
                        let st = appStatus[row.uuid];
                        if (st === undefined) {
                            return '<span class="text-muted">-</span>';
                        }
                        let text = parseInt(st.v4 || 0) + ' / ' + parseInt(st.v6 || 0);
                        if (st.last_error) {
                            return $('<span class="text-danger"></span>')
                                .attr('title', st.last_error.message || '')
                                .text(text + ' ')
                                .append('<i class="fa fa-exclamation-triangle"></i>')
                                .prop('outerHTML');
                        }
                        return text;
                    }
                },
                // Case « Follow catalog automatically » : seulement pour une ancienne application
                // du catalogue qui ne suit plus (catalog_follow=0), pour la remettre en automatique.
                onBeforeRenderDialog: function (payload) {
                    let app = (payload && payload.frm_DialogApp && payload.frm_DialogApp.app) || {};
                    $('[id="row_app.catalog_follow"]').toggle(hasValues(app.catalog_refs) && !isOn(app.catalog_follow));
                    return $.Deferred().resolve();
                }
            }
        });

        $("#grid-apps").on('loaded.rs.jquery.bootgrid', function () {
            gridLoaded = true;
        });
        // Clic sur une ligne : ouvre le dialogue d'edition (bouton edit de la ligne).
        // Delegation unique sur #grid-apps (survit aux rechargements), valable pour
        // jQuery-bootgrid (tr) comme pour Tabulator (.tabulator-row).
        $('#grid-apps').off('click.appaliasrow').on('click.appaliasrow', 'tr, .tabulator-row', function (e) {
            // Selection de texte a la souris : pas d'ouverture
            if (String(window.getSelection()) !== '') {
                return;
            }
            // En-tete, liens, boutons, champs, bascule, cellules de selection / commandes
            if ($(e.target).closest('thead, .tabulator-header, a, button, input, select, textarea, label, ' +
                '.command-toggle, .command-edit, .command-copy, .command-delete, .command-info, .bootgrid-tooltip, ' +
                'td.select-cell, td.commands, td:has(.command-toggle), .tabulator-cell[tabulator-field="commands"], ' +
                '.tabulator-cell[tabulator-field="select"], .tabulator-row-header, .tabulator-cell.tabulator-row-handle'
            ).length) {
                return;
            }
            let $edit = $(this).find('.command-edit').first();
            if ($edit.length) {
                $edit.trigger('click');
            }
        });

        mapDataToFormUI({'frm_general_settings': "/api/appalias/settings/get"}).done(function () {
            formatTokenizersUI();
            $('.selectpicker').selectpicker('refresh');
            // Liés après le chargement : un change() émis pendant le remplissage
            // du formulaire ne doit ni modifier le port ni afficher de note.
            $upProto.on('change', onUpstreamProtocolChange);
            $upAddr.on('change', prefillTlsName);
        });

        // --- Réglages : port et nom TLS suivent le protocole de l'amont ---
        // Résolveurs publics connus -> nom TLS (IPv4 et IPv6, minuscules).
        const DOT_NAMES = {
            '9.9.9.9': 'dns.quad9.net',
            '149.112.112.112': 'dns.quad9.net',
            '2620:fe::fe': 'dns.quad9.net',
            '2620:fe::9': 'dns.quad9.net',
            '1.1.1.1': 'cloudflare-dns.com',
            '1.0.0.1': 'cloudflare-dns.com',
            '2606:4700:4700::1111': 'cloudflare-dns.com',
            '2606:4700:4700::1001': 'cloudflare-dns.com',
            '8.8.8.8': 'dns.google',
            '8.8.4.4': 'dns.google',
            '2001:4860:4860::8888': 'dns.google',
            '2001:4860:4860::8844': 'dns.google'
        };
        const $upProto = $('[id="appalias.general.upstream_protocol"]');
        const $upAddr = $('[id="appalias.general.upstream_address"]');
        const $upPort = $('[id="appalias.general.upstream_port"]');
        const $upTls = $('[id="appalias.general.upstream_tls_name"]');
        const $upNote = $('<div id="upstream-port-note" class="help-block" style="display: none"></div>');
        $upPort.after($upNote);

        function prefillTlsName() {
            if ($upProto.val() !== 'dot' || $.trim($upTls.val()) !== '') {
                return;
            }
            let addr = $.trim($upAddr.val()).toLowerCase().replace(/^\[|\]$/g, '');
            if (Object.prototype.hasOwnProperty.call(DOT_NAMES, addr)) {
                $upTls.val(DOT_NAMES[addr]);
            }
        }

        function onUpstreamProtocolChange() {
            let port = $.trim($upPort.val());
            $upNote.empty().hide();
            if ($upProto.val() === 'dot') {
                if (port === '' || port === '53') {
                    $upPort.val('853');
                    $upNote.text(T.upPort853).show();
                }
                prefillTlsName();
            } else if ($upProto.val() === 'dns' && port === '853') {
                $upPort.val('53');
                $upNote.text(T.upPort53).show();
            }
        }

        $("#saveAct").click(function () {
            saveFormToEndpoint("/api/appalias/settings/set", 'frm_general_settings', function () {
                $("#reconfigureAct").click();
            }, true);
        });

        // Confirmation visible après Appliquer (l'échec reste signalé par
        // SimpleActionButton, avec status_msg).
        function showApplied() {
            ajaxGet('/api/appalias/service/status', {}, function (data, status) {
                let apps = (data && data.apps) ? data.apps : {};
                let total = 0;
                let enabled = 0;
                $.each(apps, function (key, app) {
                    total++;
                    if (app && app.enabled) {
                        enabled++;
                    }
                });
                let state = String((data && data.status) || 'unknown');
                $('#appaliasChangeMessage').hide();
                $('#appaliasApplyMessage').removeClass('alert-warning').addClass('alert-success')
                    .text(T.applied + ' ' + T.relay + ' ' + state + ', ' + enabled + ' / ' + total + ' ' + T.appsEnabled)
                    .show();
                if (state !== 'running' && state !== 'disabled') {
                    $('#appaliasApplyMessage').removeClass('alert-success').addClass('alert-warning');
                }
            });
        }

        $("#reconfigureAct").SimpleActionButton({
            onAction: function (data, status) {
                updateServiceControlUI('appalias');
                refreshRelayBadge();
                refreshStatus(true);
                refreshDiff();
                if (data && String(data.status || '').toLowerCase().trim() === 'ok') {
                    showApplied();
                    loadUnboundStatus();
                } else {
                    $('#appaliasApplyMessage').hide();
                }
            }
        });

        // --- Réglages : latences mesurées par le relais (status.py, clé latency) ---
        function latencyText(label, s) {
            if (!s || !s.window) {
                return label + ' ' + T.latNone;
            }
            return label + ' avg/p95 ' + Math.round(s.avg_ms) + '/' + Math.round(s.p95_ms) + ' ms (' +
                parseInt(s.window) + ' ' + T.latSamples + ')';
        }

        function loadLatency() {
            ajaxGet('/api/appalias/service/status', {}, function (data, status) {
                let lat = (data && data.latency) ? data.latency : {};
                let box = $('#latency-summary').empty().hide();
                let parts = [];
                $.each(lat.upstreams || {}, function (key, s) {
                    parts.push(latencyText(T.latUpstream + ' ' + String(key), s));
                });
                if (!parts.length && !lat.pf) {
                    return;
                }
                if (!parts.length) {
                    parts.push(latencyText(T.latUpstream, null));
                }
                parts.push(latencyText(T.latPf, lat.pf));
                box.text(parts.join(', ')).show();
            });
        }

        // --- Réglages : état du branchement d'Unbound ---
        function loadUnboundStatus() {
            loadLatency();
            ajaxGet('/api/appalias/service/unboundStatus', {}, function (data, status) {
                data = data || {};
                let upstream = $('#upstream-summary').empty().hide();
                if (data.upstream_protocol === 'dot') {
                    upstream.text(T.upDot + (data.upstream_tls_name ? ' (' + String(data.upstream_tls_name) + ')' : '')).show();
                } else if (data.upstream_protocol === 'dns') {
                    upstream.text(T.upPlain).show();
                }
                let summary = $('#unbound-summary').empty();
                if (data.active === undefined) {
                    summary.text(T.ubUnknown);
                } else if (!data.active) {
                    summary.text(T.ubOff);
                } else {
                    summary.text(parseInt(data.count || 0) + ' ' + T.ubForwarded +
                        (data.forward_addr ? ' (' + String(data.forward_addr) + ')' : '') + '.');
                }
                let warn = $('#unbound-root-warning').empty().hide();
                if (data.root_forward && parseInt(data.count || 0) > 0) {
                    warn.text(T.ubRootWarn).show();
                }
                applyRootWarningClass();
                let errors = data.errors || [];
                let box = $('#unbound-errors').empty().hide();
                if (errors.length) {
                    box.append($('<div></div>').text(T.ubErrors));
                    errors.forEach(function (err) {
                        box.append($('<div></div>').append($('<code></code>').text(String(err))));
                    });
                    box.show();
                }
                let list = $('#unbound-conflicts').empty();
                let conflicts = data.conflicts || [];
                if (conflicts.length) {
                    list.append($('<p></p>').text(conflicts.length + ' ' + T.ubSkipped));
                    let table = $('<table class="table table-condensed"></table>');
                    conflicts.forEach(function (c) {
                        let detail = c.detail && c.detail !== c.name ? ' (' + c.detail + ')' : '';
                        table.append($('<tr></tr>')
                            .append($('<td></td>').append($('<code></code>').text(String(c.name || ''))))
                            .append($('<td></td>').text((c.apps || []).join(', ')))
                            .append($('<td></td>').text(String(c.text || c.reason || '') + detail)));
                    });
                    list.append(table);
                }
            });
        }

        // Redémarrage manuel d'Unbound, toujours après confirmation.
        $('#unbound-restart').on('click', function () {
            BootstrapDialog.confirm({
                title: T.ubRestartTitle,
                message: T.ubRestartConfirm,
                type: BootstrapDialog.TYPE_WARNING,
                btnOKLabel: T.ubRestartTitle,
                callback: function (confirmed) {
                    if (!confirmed) {
                        return;
                    }
                    $('#unbound-restart').prop('disabled', true);
                    ajaxCall('/api/appalias/service/unboundRestart', {}, function (data, status) {
                        $('#unbound-restart').prop('disabled', false);
                        let ok = data && data.status === 'ok';
                        $('#appaliasApplyMessage').removeClass('alert-success alert-warning')
                            .addClass(ok ? 'alert-success' : 'alert-warning')
                            .text(ok ? T.ubRestarted : T.ubRestartFailed + ' ' + String((data && data.status_msg) || ''))
                            .show();
                        loadUnboundStatus();
                    });
                }
            });
        });

        // relectures différées du journal de suivi (une seule paire en attente)
        let followTimers = [];

        // --- Catalogue : informations ---
        function loadCatalogInfo() {
            ajaxGet('/api/appalias/catalog/info', {}, function (data, status) {
                let box = $('#catalog-info').empty().removeClass('text-danger');
                $('#catalog-origin-kind, #catalog-origin').toggle(!(!data || data.error));
                if (!data || data.error) {
                    box.addClass('text-danger').text(T.catalogError + ' ' + errorText(data));
                    return;
                }
                box.text('v2fly domain-list-community, ' + T.commit + ' ' + String(data.commit || '').substring(0, 12) +
                    ' (' + String(data.date || '').substring(0, 10) + '), ' + parseInt(data.lists || 0) + ' ' + T.lists +
                    ', MIT License.');
                let online = data.origin === 'online';
                $('#catalog-origin-kind').removeClass('label-default label-info')
                    .addClass(online ? 'label-info' : 'label-default')
                    .text(online ? T.catOnline : T.catEmbedded);
                $('#catalog-origin').text(String(data.date || '').substring(0, 10) + ' (' +
                    String(data.commit || '').substring(0, 7) + ')');
                $('#catalog-update').toggle(!!data.online_update);
                $('#catalog-reset').toggle(online);
                if (!data.integrity) {
                    box.append($('<div class="text-danger"></div>').text(T.integrity));
                }
            });
        }

        // --- Catalogue : galerie « Popular apps » (noms seulement, pas de logos) ---
        function loadPopular() {
            ajaxGet('/api/appalias/catalog/popular', {}, function (data, status) {
                let box = $('#catalog-popular').empty();
                if (!data || data.error) {
                    box.append($('<p class="text-danger"></p>').text(T.catalogError + ' ' + errorText(data)));
                    return;
                }
                (data.categories || []).forEach(function (cat) {
                    let row = $('<div class="appalias-popular"></div>');
                    row.append($('<div class="appalias-popular-title"></div>').text(String(cat.name || '')));
                    let lists = $('<div class="appalias-popular-lists"></div>').appendTo(row);
                    (cat.lists || []).forEach(function (item) {
                        let btn = $('<button type="button" class="btn btn-default btn-sm"></button>')
                            .attr('title', parseInt(item.rules || 0) + ' ' + T.rules)
                            .text(item.name)
                            .prop('disabled', !!item.broken);
                        btn.on('click', function () {
                            openPreview(item.name);
                        });
                        lists.append(btn);
                    });
                    box.append(row);
                });
            });
        }

        // --- Catalogue : recherche ---
        let searchTimer = null;
        let searchSeq = 0;

        function renderCard(item) {
            let panel = $('<div class="panel panel-default appalias-card"></div>');
            let body = $('<div class="panel-body"></div>').appendTo(panel);
            let head = $('<div class="appalias-card-head"></div>').appendTo(body);
            head.append($('<strong></strong>').text(item.name));
            (item.attrs || []).forEach(function (attr) {
                head.append($('<span class="label label-default"></span>').text('@' + attr));
            });
            head.append($('<span class="badge"></span>').text(parseInt(item.rules || 0) + ' ' + T.rules));
            if (item.match === 'domain' && item.via) {
                body.append($('<div class="text-muted"></div>').text(T.byDomain + ' ' + item.via));
            }
            if ((item.includes || []).length) {
                body.append($('<div class="text-muted"></div>').text(T.includes + ' ' + item.includes.join(', ')));
            }
            if (item.broken) {
                body.append($('<div class="text-danger"></div>').text(T.broken + ' ' + item.broken));
            }
            if ((item.missing || []).length) {
                body.append($('<div class="text-warning"></div>').text(T.missing + ' ' + item.missing.join(', ')));
            }
            let btn = $('<button type="button" class="btn btn-xs btn-primary"></button>').text(T.preview);
            btn.prop('disabled', !!item.broken);
            btn.on('click', function () {
                openPreview(item.name);
            });
            body.append($('<div class="appalias-card-foot"></div>').append(btn));
            return $('<div class="col-md-4 col-sm-6"></div>').append(panel);
        }

        function runSearch() {
            let q = String($('#catalog-q').val() || '').trim().toLowerCase();
            let box = $('#catalog-results');
            if (q === '') {
                box.empty();
                return;
            }
            let seq = ++searchSeq;
            ajaxGet('/api/appalias/catalog/search', {q: q}, function (data, status) {
                if (seq !== searchSeq) {
                    return; // réponse dépassée
                }
                box.empty();
                if (!data || data.error) {
                    box.append($('<div class="col-md-12 text-danger"></div>').text(errorText(data)));
                    return;
                }
                if (!(data.results || []).length) {
                    box.append($('<div class="col-md-12 text-muted"></div>').text(T.noResult));
                    return;
                }
                data.results.forEach(function (item) {
                    box.append(renderCard(item));
                });
            });
        }

        $('#catalog-q').on('input', function () {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(runSearch, 250);
        });

        // --- Catalogue : aperçu et import ---
        let preview = null;

        function refusalText(rule) {
            if (rule.refused === 'tld') {
                return T.refusedTld;
            }
            if (rule.refused === 'shared') {
                return T.refusedShared;
            }
            if (rule.refused === 'comma') {
                return T.refusedComma;
            }
            return '';
        }

        // Règles suivies par défaut (même sélection que le serveur) ; raison d'exclusion sinon.
        function exclusionOf(rule) {
            if (rule.refused) {
                return T.exclRefused + ': ' + refusalText(rule);
            }
            if (rule.unapplied) {
                return T.exclUnapplied;
            }
            if (rule.shared) {
                return T.exclShared;
            }
            if ((rule.attrs || []).indexOf('ads') !== -1) {
                return T.exclAds;
            }
            if (String(rule.rule || '').indexOf(',') !== -1) {
                return T.exclRefused + ': ' + T.refusedComma;
            }
            return '';
        }

        function followedCount() {
            if (!preview) {
                return 0;
            }
            return (preview.rules || []).filter(function (rule) { return exclusionOf(rule) === ''; }).length;
        }

        function updateSelection() {
            let n = followedCount();
            let box = $('#catalog-count').removeClass('text-danger').text(T.followedRules + ' ' + n);
            if (n > MAX_IMPORT) {
                box.addClass('text-danger').append(' - ').append(document.createTextNode(T.tooMany));
            }
            $('#catalog-import').prop('disabled', n === 0 || n > MAX_IMPORT || !preview || !preview.importable);
        }

        function renderRuleLine(rule, reason) {
            let line = $('<div></div>');
            line.append($('<code></code>').text(rule.rule));
            (rule.attrs || []).forEach(function (attr) {
                line.append(' ').append($('<span class="label label-default"></span>').text('@' + attr));
            });
            if (reason !== '') {
                line.addClass('text-muted').append(' ').append($('<small></small>').text('(' + reason + ')'));
            }
            return line;
        }

        function renderPreview() {
            let data = preview;
            $('#catalog-preview-title').text('v2fly: ' + data.ref);
            $('#catalog-preview-error').hide().empty();
            let notes = $('#catalog-notes').empty();
            if (data.broken) {
                notes.append($('<div class="alert alert-danger"></div>').text(T.broken + ' ' + data.broken));
            }
            if ((data.missing || []).length) {
                notes.append($('<div class="alert alert-warning"></div>').text(T.missing + ' ' + data.missing.join(', ')));
            }
            if (data.unknown) {
                notes.append($('<div class="text-muted"></div>').text(T.unknown + ' ' + parseInt(data.unknown)));
            }

            // liste en lecture seule : règles suivies, puis règles exclues en grisé avec la raison
            let followed = [];
            let excluded = [];
            (data.rules || []).forEach(function (rule) {
                let reason = exclusionOf(rule);
                (reason === '' ? followed : excluded).push([rule, reason]);
            });
            let box = $('#catalog-rules').empty();
            box.append($('<strong></strong>').text(T.followedList + ' (' + followed.length + ')'));
            followed.forEach(function (pair) {
                box.append(renderRuleLine(pair[0], ''));
            });
            if (excluded.length) {
                box.append($('<strong class="appalias-group-head" style="display: block"></strong>')
                    .text(T.excludedList + ' (' + excluded.length + ')'));
                excluded.forEach(function (pair) {
                    box.append(renderRuleLine(pair[0], pair[1]));
                });
            }
            $('#catalog-rules-box').prop('open', false);

            $('#catalog-name').val(data.suggested_name || '');
            updateSelection();
        }

        function openPreview(ref) {
            ajaxGet('/api/appalias/catalog/preview', {ref: ref}, function (data, status) {
                if (!data || data.error) {
                    $('#catalog-message').removeClass('alert-info').addClass('alert-danger')
                        .text(T.catalogError + ' ' + errorText(data)).show();
                    return;
                }
                preview = data;
                renderPreview();
                $('#catalogPreview').modal('show');
            });
        }

        // Après un import : applique la configuration (même appel que le bouton Apply),
        // puis recharge la grille. L'application reste enregistrée si l'application échoue.
        function applyAfterImport(warning) {
            let msg = $('#catalog-message').removeClass('alert-danger alert-warning').addClass('alert-info')
                .text(T.adding).show();
            ajaxCall('/api/appalias/service/reconfigure', {}, function (data, status) {
                let ok = data && String(data.status || '').toLowerCase().trim() === 'ok';
                updateServiceControlUI('appalias');
                refreshRelayBadge();
                refreshStatus(false);
                refreshDiff();
                if (ok) {
                    msg.removeClass('alert-danger alert-warning').addClass('alert-info').text(T.addedApplied);
                    showApplied();
                    loadUnboundStatus();
                } else {
                    let detail = String((data && (data.status_msg || data.message)) || '');
                    msg.removeClass('alert-info').addClass('alert-danger')
                        .text(T.addedNotApplied + (detail !== '' ? ' ' + detail : ''));
                    $('#appaliasChangeMessage').show();
                }
                if (warning) {
                    msg.append($('<div></div>').text(String(warning)));
                }
                msg.show();
                // la grille est rechargée une seule fois, par refreshDiff()
            });
        }

        $('#catalog-import').on('click', function () {
            if (!preview) {
                return;
            }
            // pas de clé « rules » : le serveur applique la sélection par défaut
            let payload = {
                ref: preview.ref,
                mode: 'new',
                name: String($('#catalog-name').val() || '').trim()
            };
            $('#catalog-import').prop('disabled', true);
            $('#catalog-preview-error').hide().empty();
            ajaxCall('/api/appalias/catalog/import', payload, function (data, status) {
                if (data && data.result === 'saved') {
                    $('#catalogPreview').modal('hide');
                    applyAfterImport(data.warning);
                } else {
                    // nom en doublon, trop de règles... : l'utilisateur corrige et recommence
                    $('#catalog-preview-error').text(T.failed + ' ' + errorText(data)).show();
                    updateSelection();
                }
            });
        });

        // --- Grille : écarts avec le catalogue ---
        let diffUuid = null;

        function renderDiffList(box, rules, sign) {
            box.empty();
            if (!rules.length) {
                box.append($('<div class="text-muted"></div>').text(T.noChange));
                return;
            }
            rules.forEach(function (rule) {
                let input = $('<input type="checkbox" class="appalias-diff-rule"/>').attr('data-rule', rule).attr('data-sign', sign);
                box.append($('<div class="checkbox"></div>').append(
                    $('<label></label>').append(input).append(' ' + sign + ' ').append($('<code></code>').text(rule))
                ));
            });
        }

        function openDiff(uuid) {
            ajaxGet('/api/appalias/catalog/diff', {uuid: uuid}, function (data, status) {
                let d = data && data.apps ? data.apps[uuid] : null;
                if (!d) {
                    return;
                }
                diffUuid = uuid;
                $('#catalog-diff-title').text(String(d.name || '') + ' - v2fly: ' + (d.refs || []).join(', '));
                $('#catalog-diff-error').hide().empty();
                $('#catalog-diff-warning').hide().empty();
                if (d.error) {
                    $('#catalog-diff-error').text(T.catalogError + ' ' + d.error).show();
                }
                renderDiffList($('#catalog-diff-added'), d.added || [], '+');
                renderDiffList($('#catalog-diff-removed'), d.removed || [], '-');
                let refused = d.refused || [];
                if (refused.length) {
                    $('#catalog-diff-warning').text(T.diffRefused + ' ' + refused.join(', ')).show();
                }
                let custom = d.custom || [];
                $('#catalog-diff-custom').text(T.custom + ' ' + custom.length);
                $('#catalogDiff').modal('show');
            });
        }

        $("#grid-apps").on('click', '.appalias-diff-link', function (e) {
            e.preventDefault();
            e.stopPropagation();
            openDiff(String($(this).attr('data-uuid')));
        });

        $('#catalog-diff-apply').on('click', function () {
            let add = [];
            let remove = [];
            $('#catalogDiff input.appalias-diff-rule:checked').each(function () {
                if ($(this).attr('data-sign') === '+') {
                    add.push($(this).attr('data-rule'));
                } else {
                    remove.push($(this).attr('data-rule'));
                }
            });
            ajaxCall('/api/appalias/catalog/applyDiff', {app_uuid: diffUuid, add: add, remove: remove}, function (data, status) {
                if (data && data.result === 'saved') {
                    $('#catalogDiff').modal('hide');
                    $('#appaliasChangeMessage').show();
                    if (data.warning) {
                        $('#appaliasApplyMessage').removeClass('alert-success').addClass('alert-warning')
                            .text(String(data.warning)).show();
                    }
                    refreshDiff();
                } else {
                    $('#catalog-diff-error').text(T.failed + ' ' + errorText(data)).show();
                }
            });
        });

        $('#catalogDiff').on('click', '.appalias-diff-all', function (e) {
            e.preventDefault();
            $('#' + $(this).attr('data-target')).find('input').prop('checked', true);
        });

        // --- État du relais (badge en haut de page) ---
        let relayRunning = null;

        function applyRootWarningClass() {
            // Informatif quand le relais tourne, en rouge seulement s'il est arrêté.
            $('#unbound-root-warning').removeClass('alert-warning alert-info alert-danger')
                .addClass(relayRunning === false ? 'alert-danger' : 'alert-info');
        }

        function refreshRelayBadge() {
            ajaxGet('/api/appalias/service/status', {}, function (data, status) {
                data = data || {};
                let state = String(data.status || 'unknown');
                let badge = $('#appalias-relay-badge').empty();
                let label = $('<span class="label"></span>');
                if (data.error) {
                    label.addClass('label-danger').text(T.stConfigError);
                    badge.append(label).append(' ').append($('<span class="text-danger"></span>').text(String(data.error)));
                } else if (state === 'running') {
                    label.addClass('label-success').text(T.stRunning);
                    badge.append(label);
                    let details = [];
                    if (data.pid) {
                        details.push(T.stPid + ' ' + parseInt(data.pid));
                    }
                    if (data.version) {
                        details.push(T.stVersion + ' ' + String(data.version));
                    }
                    if (details.length) {
                        badge.append(' ').append($('<span class="text-muted"></span>').text(details.join(', ')));
                    }
                } else {
                    label.addClass(state === 'disabled' ? 'label-default' : 'label-danger').text(state === 'disabled' ? T.stDisabled : T.stStopped);
                    badge.append(label);
                }
                let restart = $('#appalias-restart-needed').empty().hide();
                if (data.restart_needed) {
                    restart.text(T.stRestart.replace('%s', String(data.version || '?'))
                        .replace('%s', String(data.installed_version || '?'))).show();
                }
                relayRunning = !data.error && state === 'running';
                applyRootWarningClass();
            });
        }

        // --- Catalogue : provenance, mise à jour en ligne, retour à l'embarqué ---
        function catalogResult(data) {
            let msg = $('#catalog-message').removeClass('alert-info alert-danger alert-warning');
            if (data && data.result === 'updated') {
                let text = T.catUpdated + ' ' + parseInt(data.lists || 0) + ' ' + T.lists + ', ' +
                    parseInt(data.added_count || 0) + ' ' + T.catAddedLists + ', ' +
                    parseInt(data.removed_count || 0) + ' ' + T.catRemovedLists + '.';
                msg.addClass('alert-info').text(text);
                if (data.note) {
                    msg.append($('<div></div>').text(String(data.note)));
                }
            } else if (data && data.result === 'unchanged') {
                msg.addClass('alert-info').text(T.catUnchanged);
            } else {
                msg.addClass('alert-danger').text(T.catUpdateFailed + ' ' + errorText(data));
            }
            // Suivi automatique lancé après le téléchargement (catalog_follow.php).
            let follow = data && data.follow;
            if (follow && typeof follow === 'object') {
                if (follow.result === 'started') {
                    msg.append($('<div></div>').text(T.fStarted));
                    // le suivi tourne en tâche détachée : relire le journal deux fois, sans boucle
                    followTimers.forEach(clearTimeout);
                    followTimers = [setTimeout(loadFollowLog, 5000), setTimeout(loadFollowLog, 15000)];
                }
                if (parseInt(follow.changed || 0) > 0) {
                    msg.append($('<div></div>').text(parseInt(follow.changed) + ' ' + T.fUpdated));
                }
                if (parseInt(follow.errors || 0) > 0) {
                    msg.removeClass('alert-info').addClass('alert-warning')
                        .append($('<div></div>').text(parseInt(follow.errors) + ' ' + T.fErrors));
                }
                if (follow.apply_error) {
                    msg.removeClass('alert-info').addClass('alert-warning')
                        .append($('<div></div>').text(T.fApplyFailed + ' ' + String(follow.apply_error)));
                }
                if (follow.result === 'failed') {
                    msg.removeClass('alert-info').addClass('alert-warning')
                        .append($('<div></div>').text(T.catalogError + ' ' + errorText(follow)));
                }
            }
            msg.show();
        }

        // --- Catalogue : suivi automatique (journal et annulation) ---
        function pad2(n) {
            return ('0' + n).slice(-2);
        }

        function dateText(seconds) {
            let d = new Date(parseFloat(seconds || 0) * 1000);
            return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' +
                pad2(d.getHours()) + ':' + pad2(d.getMinutes());
        }

        function undoFollow(entry) {
            BootstrapDialog.confirm({
                title: T.fUndoTitle,
                message: T.fUndoConfirm,
                type: BootstrapDialog.TYPE_WARNING,
                btnOKLabel: T.fUndo,
                callback: function (confirmed) {
                    if (!confirmed) {
                        return;
                    }
                    ajaxCall('/api/appalias/catalog/undoFollow', {entry_id: String(entry.id)}, function (data, status) {
                        let ok = data && data.result === 'undone';
                        let msg = $('#catalog-message').removeClass('alert-info alert-danger alert-warning')
                            .addClass(ok ? 'alert-info' : 'alert-danger')
                            .text(ok ? T.fUndoDone : T.fUndoFailed + ' ' + errorText(data));
                        if (ok && data.apply_error) {
                            msg.removeClass('alert-info').addClass('alert-warning')
                                .append($('<div></div>').text(T.fApplyFailed + ' ' + String(data.apply_error)));
                        }
                        msg.show();
                        loadFollowLog();
                        refreshStatus(true);
                        refreshDiff();
                    });
                }
            });
        }

        function renderFollowEntry(entry, byId) {
            let added = entry.added || [];
            let removed = entry.removed || [];
            let row = $('<div class="appalias-follow-entry"></div>');
            row.append($('<div class="appalias-small appalias-tabular"></div>').text(dateText(entry.time)));
            let main = $('<div></div>').appendTo(row);
            let line = $('<div class="appalias-flex"></div>').appendTo(main);
            line.append($('<strong></strong>').text(String(entry.app || '')));
            line.append($('<span class="label label-success"></span>').text('+' + added.length));
            line.append($('<span class="label label-danger"></span>').text('-' + removed.length));
            if (entry.kind === 'undo') {
                line.append($('<span class="label label-default"></span>').text(T.fUndoKind));
            }
            if (entry.revision) {
                line.append($('<span class="appalias-small"></span>').text(T.commit + ' ' + String(entry.revision).substring(0, 7)));
            }
            if (added.length + removed.length > 0) {
                let details = $('<details></details>').appendTo(main);
                details.append($('<summary></summary>').text(T.fDetails));
                let rules = $('<div class="appalias-follow-rules"></div>').appendTo(details);
                added.forEach(function (rule) {
                    rules.append($('<div class="text-success"></div>').append('+ ').append($('<code></code>').text(String(rule))));
                });
                removed.forEach(function (rule) {
                    rules.append($('<div class="text-danger"></div>').append('- ').append($('<code></code>').text(String(rule))));
                });
            }
            let side = $('<div class="appalias-flex"></div>').appendTo(row);
            if (entry.kind === 'undo') {
                let target = entry.undo_of && byId ? byId[String(entry.undo_of)] : null;
                side.append($('<span class="label label-default"></span>')
                    .text(T.fUndoOf + (target ? ' ' + dateText(target.time) : '')));
            } else if (entry.undone_by) {
                side.append($('<span class="label label-default"></span>').text(T.fUndone));
            } else {
                let btn = $('<button type="button" class="btn btn-xs btn-default"></button>')
                    .append('<i class="fa fa-undo"></i> ').append(document.createTextNode(T.fUndo));
                if (!entry.app_exists) {
                    btn.prop('disabled', true).attr('title', T.fDeleted);
                }
                btn.on('click', function () {
                    undoFollow(entry);
                });
                side.append(btn);
            }
            return row;
        }

        function loadFollowLog() {
            ajaxGet('/api/appalias/catalog/followLog', {}, function (data, status) {
                let box = $('#catalog-follow-log').empty();
                if (!data || data.error) {
                    box.append($('<p class="text-danger"></p>').text(T.catalogError + ' ' + errorText(data)));
                    return;
                }
                let entries = data.entries || [];
                if (!entries.length) {
                    box.append($('<p class="text-muted"></p>').text(T.fNone));
                    return;
                }
                let byId = {};
                entries.forEach(function (entry) {
                    byId[String(entry.id)] = entry;
                });
                entries.forEach(function (entry) {
                    box.append(renderFollowEntry(entry, byId));
                });
            });
        }

        $('#catalog-update').on('click', function () {
            BootstrapDialog.confirm({
                title: T.catUpdateTitle,
                message: T.catUpdateConfirm,
                type: BootstrapDialog.TYPE_INFO,
                btnOKLabel: T.catUpdateTitle,
                callback: function (confirmed) {
                    if (!confirmed) {
                        return;
                    }
                    $('#catalog-update').prop('disabled', true);
                    ajaxCall('/api/appalias/catalog/update', {}, function (data, status) {
                        $('#catalog-update').prop('disabled', false);
                        catalogResult(data);
                        loadCatalogInfo();
                        loadPopular();
                        loadFollowLog();
                        refreshStatus(true);
                        refreshDiff();
                    });
                }
            });
        });

        $('#catalog-reset').on('click', function () {
            BootstrapDialog.confirm({
                title: T.catResetTitle,
                message: T.catResetConfirm,
                type: BootstrapDialog.TYPE_WARNING,
                btnOKLabel: T.catResetTitle,
                callback: function (confirmed) {
                    if (!confirmed) {
                        return;
                    }
                    ajaxCall('/api/appalias/catalog/reset', {}, function (data, status) {
                        let msg = $('#catalog-message').removeClass('alert-info alert-danger alert-warning');
                        if (data && data.result === 'reset') {
                            msg.addClass('alert-info').text(T.catResetDone);
                        } else {
                            msg.addClass('alert-danger').text(T.catalogError + ' ' + errorText(data));
                        }
                        msg.show();
                        loadCatalogInfo();
                        loadPopular();
                        refreshDiff();
                    });
                }
            });
        });

        updateServiceControlUI('appalias');
        refreshRelayBadge();
        refreshStatus();
        refreshDiff();
        loadCatalogInfo();
        loadPopular();
        loadUnboundStatus();
        loadFollowLog();
    });
</script>

<div id="appalias-relay-status" class="appalias-relay">
    <strong>{{ lang._('Relay') }}</strong>
    <span id="appalias-relay-badge" class="appalias-flex"></span>
    <div id="appalias-restart-needed" class="alert alert-warning" style="display: none" role="alert"></div>
</div>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li class="active"><a data-toggle="tab" href="#apps">{{ lang._('Applications') }}</a></li>
    <li><a data-toggle="tab" href="#catalog">{{ lang._('Catalog') }}</a></li>
    <li><a data-toggle="tab" href="#settings">{{ lang._('Settings') }}</a></li>
    <li><a data-toggle="tab" href="#howto">{{ lang._('How to use') }}</a></li>
</ul>

<div id="appaliasApplyMessage" class="alert alert-success" style="display: none" role="alert"></div>

<div class="tab-content content-box">
    <div id="apps" class="tab-pane fade in active">
        <table id="grid-apps" class="table table-condensed table-hover table-striped table-responsive"
               data-editDialog="DialogApp" data-editAlert="appaliasChangeMessage">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th>
                <th data-column-id="enabled" data-width="6em" data-type="string" data-formatter="rowtoggle">{{ lang._('Enabled') }}</th>
                <th data-column-id="name" data-type="string" data-formatter="appname">{{ lang._('Name') }}</th>
                <th data-column-id="description" data-type="string">{{ lang._('Description') }}</th>
                <th data-column-id="domains" data-type="string" data-formatter="rules">{{ lang._('Domains') }}</th>
                <th data-column-id="networks" data-type="string" data-visible="false">{{ lang._('Networks') }}</th>
                <th data-column-id="families" data-type="string">{{ lang._('Families') }}</th>
                <th data-column-id="ipcount" data-type="string" data-formatter="ipcount" data-sortable="false">{{ lang._('IPs v4/v6') }}</th>
                <th data-column-id="commands" data-width="9em" style="min-width: 9em; white-space: nowrap" data-formatter="commands" data-sortable="false">{{ lang._('Commands') }}</th>
            </tr>
            </thead>
            <tbody>
            </tbody>
            <tfoot>
            <tr>
                <td></td>
                <td>
                    <button data-action="add" type="button" class="btn btn-xs btn-primary"><span class="fa fa-fw fa-plus"></span></button>
                    <button data-action="deleteSelected" type="button" class="btn btn-xs btn-default"><span class="fa fa-fw fa-trash-o"></span></button>
                </td>
            </tr>
            </tfoot>
        </table>
        <div class="col-md-12">
            <div id="appaliasChangeMessage" class="alert alert-info" style="display: none" role="alert">
                {{ lang._('After a change, click Apply to create the aliases and reload the relay.') }}
            </div>
            <hr/>
            <button class="btn btn-primary" id="reconfigureAct"
                    data-endpoint="/api/appalias/service/reconfigure"
                    data-label="{{ lang._('Apply') }}"
                    data-error-title="{{ lang._('Error reconfiguring AppAlias') }}"
                    type="button"></button>
            <br/><br/>
        </div>
    </div>

    <div id="catalog" class="tab-pane fade in">
        <div class="col-md-12">
            <div class="panel panel-default" style="margin-top: 10px">
                <div class="panel-body appalias-source">
                    <span id="catalog-origin-kind" class="label label-default"></span>
                    <strong id="catalog-origin"></strong>
                    <span class="appalias-toolbar-actions">
                        <button type="button" class="btn btn-xs btn-default" id="catalog-update" style="display: none">
                            <i class="fa fa-refresh"></i> {{ lang._('Update catalog now') }}
                        </button>
                        <button type="button" class="btn btn-xs btn-default" id="catalog-reset" style="display: none">
                            {{ lang._('Revert to embedded') }}
                        </button>
                    </span>
                    <p id="catalog-info" class="text-muted"></p>
                </div>
            </div>
            <div id="catalog-message" class="alert alert-info" style="display: none" role="alert"></div>
            <div class="panel panel-default">
                <div class="panel-heading"><strong>{{ lang._('Automatic updates') }}</strong></div>
                <div class="panel-body">
                    <p class="text-muted">
                        {{ lang._('Applications marked auto follow the catalog: after each catalog update, the new rules of their catalog lists are added and the rules removed from the catalog are removed, then applied. Rules added by hand are never changed. The last 20 changes are listed below; Undo reverts one and applies again.') }}
                    </p>
                    <div id="catalog-follow-log"></div>
                </div>
            </div>
            <h3 class="appalias-title">{{ lang._('Popular apps') }}</h3>
            <div id="catalog-popular"></div>
            <h3 class="appalias-title">{{ lang._('Search the catalog') }}</h3>
            <div class="form-group">
                <input type="text" id="catalog-q" class="form-control" maxlength="64" autocomplete="off"
                       placeholder="{{ lang._('Search a list (netflix) or a domain (nflxvideo.net)') }}"/>
            </div>
            <p class="text-muted">
                {{ lang._('Importing only saves the application: nothing changes on the firewall before you click Apply.') }}
            </p>
        </div>
        <div id="catalog-results" class="row appalias-cards"></div>
        <div class="col-md-12"><br/></div>
    </div>

    <div id="settings" class="tab-pane fade in">
        <div class="col-md-12">
            <div id="unbound-status" class="panel panel-default" style="margin-top: 10px">
                <div class="panel-heading"><strong>{{ lang._('Unbound forwarding') }}</strong></div>
                <div class="panel-body">
                    <p id="upstream-summary" style="display: none"></p>
                    <p id="latency-summary" class="text-muted" style="display: none"></p>
                    <p id="unbound-summary" class="text-muted"></p>
                    <div id="unbound-root-warning" class="alert alert-info" style="display: none" role="alert"></div>
                    <div id="unbound-errors" class="alert alert-warning" style="display: none" role="alert"></div>
                    <button type="button" class="btn btn-xs btn-default" id="unbound-restart">
                        {{ lang._('Restart Unbound') }}
                    </button>
                    <div id="unbound-conflicts"></div>
                </div>
            </div>
        </div>
        {{ partial("layout_partials/base_form", ['fields': generalForm, 'id': 'frm_general_settings']) }}
        <div class="col-md-12">
            <hr/>
            <button class="btn btn-primary" id="saveAct" type="button"><b>{{ lang._('Save and apply') }}</b></button>
            <br/><br/>
        </div>
    </div>

    <div id="howto" class="tab-pane fade in">
        <div class="col-md-12">
            <h2>{{ lang._('Principle') }}</h2>
            <p>
                AppAlias creates one <b>External</b> firewall alias per application (for example <code>APP_EXAMPLE</code>).
                The <code>appaliasd</code> DNS relay sees the DNS answers: when a name matches an application, its A and
                AAAA addresses are added to the alias <b>before</b> the answer is returned to the client. CIDR networks
                entered by hand are always present in the alias.
            </p>
            <p>
                AppAlias creates <b>no rule</b>: use the <code>APP_*</code> aliases in your own rules (block, pass,
                policy routing through a gateway).
            </p>

            <h2>{{ lang._('1. Setup: put the relay behind the local resolver') }}</h2>
            <p>Clients keep querying the firewall resolver, which sends the application domains to the relay:</p>
            <ul>
                <li>
                    <b>Unbound, automatic (opt-in)</b>: check <i>Forward application domains from Unbound</i> in the
                    <i>Settings</i> tab. Apply then writes one forward zone per application domain to the relay and
                    restarts Unbound when the list of forwarded domains changes. Other queries keep their path (DNS
                    over TLS, Query Forwarding). Names already configured in Unbound and local names are skipped and
                    listed in the <i>Settings</i> tab. Unbound pages then show a notice about manual overwrites: this
                    is expected. A <code>full:</code> rule forwards its sub-domains too. Zones use forward-first:
                    while the relay is down, these domains fall back to direct recursion to the authoritative servers
                    (plain DNS, port 53), bypassing DNS over TLS and the "." Query Forwarding, and their addresses
                    are not added to the aliases. If outgoing port 53 is blocked, they fail until the relay is back. After adding a Query Forwarding entry for a domain also used by AppAlias, press
                    Apply in AppAlias.
                </li>
                <li>
                    <b>Unbound, manual</b>: in <i>Services &gt; Unbound DNS &gt; Query Forwarding</i> add an entry to
                    server <code>127.0.0.1</code>, port <code>53535</code>, for each domain (an entry without domain
                    is overridden by a DNS over TLS forward).
                </li>
                <li>
                    <b>Dnsmasq</b>: set the upstream server <code>127.0.0.1#53535</code> and stop using the system DNS
                    servers.
                </li>
                <li>
                    In the <i>Settings</i> tab, enter the real <b>upstream resolver</b> (the one used before). Never enter
                    the local resolver itself: queries would loop.
                </li>
                <li>
                    To encrypt the queries of the relay too, select <b>DNS over TLS</b> as upstream protocol, for
                    example Quad9: resolver <code>9.9.9.9</code>, port <code>853</code>, TLS name
                    <code>dns.quad9.net</code>, fallback <code>149.112.112.112#dns.quad9.net</code>. The relay never
                    falls back to plain DNS.
                </li>
            </ul>

            <h2>{{ lang._('2. Catalog') }}</h2>
            <p>
                The <i>Catalog</i> tab searches a snapshot of
                <a href="https://github.com/v2fly/domain-list-community" target="_blank" rel="noopener noreferrer">v2fly domain-list-community</a>
                (MIT License): embedded in the package, or checked hourly by default when the online update is enabled in
                <i>Settings</i> (downloaded only when the list changed). The preview lets you choose the rules to import:
                rules tagged <code>@ads</code> and rules on shared platforms (CDN) are unchecked by default;
                <code>keyword:</code> and <code>regexp:</code> are rules that are kept but never applied by the relay.
            </p>
            <p>
                An imported application follows the catalog automatically (<span class="label label-primary">auto</span>):
                after each catalog update, new rules of its lists are added and rules removed from the catalog are
                removed, then applied without a click. Rules you unchecked at import, rules added by hand, shared
                platforms and rules not applied by the relay are never added or changed. Every change is listed in
                <i>Catalog &gt; Automatic updates</i> and can be undone. Uncheck <i>Follow catalog automatically</i>
                in the application to review the catalog updates by hand instead.
            </p>

            <h2>{{ lang._('3. Example: send an application through a VPN (policy routing)') }}</h2>
            <p>In <i>Firewall &gt; Rules &gt; LAN</i>, above the default allow rule:</p>
            <ol>
                <li>
                    <b>Pass</b>, IPv4, source <code>LAN net</code>, destination <code>APP_EXAMPLE</code>,
                    gateway: your VPN gateway (advanced options of the rule).
                </li>
                <li>
                    <b>Block</b>, IPv6, source <code>LAN net</code>, destination <code>APP_EXAMPLE</code>: without this
                    rule, the IPv6 traffic of the application would leave outside the VPN (leak).
                </li>
            </ol>

            <h2>{{ lang._('4. Example: block an application') }}</h2>
            <p>
                <b>Block</b> (or <b>Reject</b>), IPv4+IPv6, source <code>LAN net</code>, destination <code>APP_EXAMPLE</code>.
                For a manual network example, use documentation ranges such as <code>192.0.2.0/24</code> or
                <code>2001:db8::/32</code>.
            </p>

            <h2>{{ lang._('5. Limits') }}</h2>
            <ul>
                <li>
                    <b>Encrypted DNS (DoH, DoT)</b>: a client resolving by itself over HTTPS bypasses the relay; its
                    addresses never reach the alias. Block DoH/DoT or disable it on the clients.
                </li>
                <li>
                    <b>Shared CDNs</b>: one address can serve many sites. Blocking or routing an application can affect
                    other services hosted on the same CDN. A <code>domain:</code> rule on a whole shared platform is
                    refused.
                </li>
                <li>
                    <b>Client caches</b>: an address cached on a client before activation (or before the domain was
                    added) is not seen by the relay until the client resolves again. Flush the DNS cache of the client
                    if needed. The TTL margin then covers the caches of the local resolver and of the clients.
                </li>
            </ul>
        </div>
    </div>
</div>

<div class="modal fade" id="catalogPreview" tabindex="-1" role="dialog" aria-labelledby="catalog-preview-title">
    <div class="modal-dialog modal-lg" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <button type="button" class="close" data-dismiss="modal" aria-label="{{ lang._('Close') }}"><span aria-hidden="true">&times;</span></button>
                <h4 class="modal-title" id="catalog-preview-title"></h4>
            </div>
            <div class="modal-body">
                <div id="catalog-notes"></div>
                <div class="form-group">
                    <label for="catalog-name">{{ lang._('Application name') }}</label>
                    <input type="text" id="catalog-name" class="form-control" maxlength="31"/>
                </div>
                <p id="catalog-count"></p>
                <details id="catalog-rules-box">
                    <summary>{{ lang._('Show the rules') }}</summary>
                    <div id="catalog-rules" class="appalias-rules"></div>
                </details>
                <p class="text-muted">{{ lang._('The application is created, applied and then kept up to date with the catalog automatically.') }}</p>
                <div id="catalog-preview-error" class="alert alert-danger" style="display: none" role="alert"></div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-default" data-dismiss="modal">{{ lang._('Cancel') }}</button>
                <button type="button" class="btn btn-primary" id="catalog-import">{{ lang._('Add') }}</button>
            </div>
        </div>
    </div>
</div>

<div class="modal fade" id="catalogDiff" tabindex="-1" role="dialog" aria-labelledby="catalog-diff-title">
    <div class="modal-dialog modal-lg" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <button type="button" class="close" data-dismiss="modal" aria-label="{{ lang._('Close') }}"><span aria-hidden="true">&times;</span></button>
                <h4 class="modal-title" id="catalog-diff-title"></h4>
            </div>
            <div class="modal-body">
                <div id="catalog-diff-error" class="alert alert-danger" style="display: none" role="alert"></div>
                <div id="catalog-diff-warning" class="alert alert-warning" style="display: none" role="alert"></div>
                <h4>
                    {{ lang._('New in the catalog') }}
                    <small><a href="#" class="appalias-diff-all" data-target="catalog-diff-added">{{ lang._('select all') }}</a></small>
                </h4>
                <div id="catalog-diff-added" class="appalias-rules"></div>
                <h4>
                    {{ lang._('Removed from the catalog') }}
                    <small><a href="#" class="appalias-diff-all" data-target="catalog-diff-removed">{{ lang._('select all') }}</a></small>
                </h4>
                <div id="catalog-diff-removed" class="appalias-rules"></div>
                <p id="catalog-diff-custom" class="text-muted"></p>
                <p class="text-muted">{{ lang._('Checked changes are saved in the application; click Apply afterwards.') }}</p>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-default" data-dismiss="modal">{{ lang._('Cancel') }}</button>
                <button type="button" class="btn btn-primary" id="catalog-diff-apply">{{ lang._('Save checked changes') }}</button>
            </div>
        </div>
    </div>
</div>

{{ partial("layout_partials/base_dialog", ['fields': formDialogApp, 'id': 'DialogApp', 'label': lang._('Application')]) }}
