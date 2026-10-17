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
    .appalias-card { margin-bottom: 10px; }
    .appalias-card .panel-body { padding: 10px; }
    .appalias-card .label { margin-right: 3px; }
    .appalias-rules { max-height: 50vh; overflow-y: auto; }
    .appalias-rules .checkbox { margin: 2px 0; }
    .appalias-rules .label { margin-left: 4px; }
    .appalias-discovery { color: #999; }
    .appalias-group-head { margin-top: 12px; }
    .appalias-popular { margin-bottom: 8px; }
    .appalias-popular .btn { margin: 2px 0; }
    .appalias-proposal { padding: 6px 0; border-bottom: 1px solid #eee; }
    .appalias-score { display: inline-block; width: 120px; height: 14px; margin: 0; vertical-align: middle; }
    .appalias-score .progress-bar { font-size: 10px; line-height: 14px; }
    #appalias-relay-status { margin: 6px 0; }
</style>

<script>
    $( document ).ready(function () {
        const T = {
            rules: "{{ lang._('rules')|escape_js }}",
            includes: "{{ lang._('includes:')|escape_js }}",
            preview: "{{ lang._('Preview')|escape_js }}",
            broken: "{{ lang._('Broken list (not importable):')|escape_js }}",
            missing: "{{ lang._('Missing included lists:')|escape_js }}",
            unknown: "{{ lang._('Unknown lines ignored:')|escape_js }}",
            noResult: "{{ lang._('No list found.')|escape_js }}",
            byDomain: "{{ lang._('contains')|escape_js }}",
            sharedCdn: "{{ lang._('shared CDN')|escape_js }}",
            sharedHelp: "{{ lang._('Shared platform (CDN, cloud, hosting): its addresses also serve many other sites.')|escape_js }}",
            refusedTld: "{{ lang._('Refused: a domain rule on a whole top-level domain. Use full: instead.')|escape_js }}",
            refusedShared: "{{ lang._('Refused: a domain rule on a shared platform. Use full: or a dedicated sub-domain.')|escape_js }}",
            refusedComma: "{{ lang._('Refused: a comma cannot be stored in the rule list.')|escape_js }}",
            discovery: "{{ lang._('Discovery rules: kept, but never applied by the relay.')|escape_js }}",
            all: "{{ lang._('all')|escape_js }}",
            none: "{{ lang._('none')|escape_js }}",
            selected: "{{ lang._('selected rules:')|escape_js }}",
            tooMany: "{{ lang._('Too many rules for one import (1000 at most).')|escape_js }}",
            many: "{{ lang._('Large selection: the alias may receive many addresses.')|escape_js }}",
            saved: "{{ lang._('Application saved. Click Apply on the Applications tab to create or update the alias.')|escape_js }}",
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
            catUpdateConfirm: "{{ lang._('Download the latest v2fly catalog from GitHub now? The firewall contacts api.github.com and codeload.github.com. Your applications do not change: new rules appear as catalog updates to review.')|escape_js }}",
            catUpdated: "{{ lang._('Catalog downloaded:')|escape_js }}",
            catUnchanged: "{{ lang._('The catalog is already up to date.')|escape_js }}",
            catAddedLists: "{{ lang._('lists added')|escape_js }}",
            catRemovedLists: "{{ lang._('lists removed')|escape_js }}",
            catUpdateFailed: "{{ lang._('Catalog update failed, the previous catalog is kept:')|escape_js }}",
            catResetTitle: "{{ lang._('Revert to embedded')|escape_js }}",
            catResetConfirm: "{{ lang._('Delete the downloaded catalog and use the embedded snapshot again?')|escape_js }}",
            catResetDone: "{{ lang._('The embedded snapshot is used again.')|escape_js }}",
            dProposals: "{{ lang._('proposals')|escape_js }}",
            dObserved: "{{ lang._('names observed')|escape_js }}",
            dUncovered: "{{ lang._('not covered')|escape_js }}",
            dUnassigned: "{{ lang._('unassigned')|escape_js }}",
            dDropped: "{{ lang._('events lost')|escape_js }}",
            dRetention: "{{ lang._('retention (days)')|escape_js }}",
            dDataAt: "{{ lang._('data at')|escape_js }}",
            dNoData: "{{ lang._('No data yet: the relay writes its data every 10 minutes, or on Refresh.')|escape_js }}",
            dNotApplied: "{{ lang._('Discovery is enabled in Settings but not applied yet: click Apply.')|escape_js }}",
            dNone: "{{ lang._('No proposal.')|escape_js }}",
            dAccept: "{{ lang._('Accept')|escape_js }}",
            dAcceptChecked: "{{ lang._('Accept checked')|escape_js }}",
            dIgnore: "{{ lang._('Ignore')|escape_js }}",
            dUnignore: "{{ lang._('Stop ignoring')|escape_js }}",
            dOtherApp: "{{ lang._('Other app...')|escape_js }}",
            dAddTo: "{{ lang._('Add to...')|escape_js }}",
            dCreateApp: "{{ lang._('Create app')|escape_js }}",
            dStrong: "{{ lang._('strong')|escape_js }}",
            dNew: "{{ lang._('new')|escape_js }}",
            dCname: "{{ lang._('CNAME')|escape_js }}",
            dCooc: "{{ lang._('co-occurrence')|escape_js }}",
            dViews: "{{ lang._('views')|escape_js }}",
            dSaved: "{{ lang._('Rule saved in the application. Click Apply on the Applications tab.')|escape_js }}",
            dPurgeTitle: "{{ lang._('Erase discovery data')|escape_js }}",
            dPurgeConfirm: "{{ lang._('Erase all the names collected by discovery? This cannot be undone.')|escape_js }}",
            dLearnAny: "{{ lang._('any application')|escape_js }}",
            dLearnLeft: "{{ lang._('s left')|escape_js }}",
            dLearnSeen: "{{ lang._('names seen during the last learning session:')|escape_js }}",
            dNoIgnored: "{{ lang._('No ignored rule.')|escape_js }}",
            dRegexTimeout: "{{ lang._('Some regexp: rules were skipped (time limit).')|escape_js }}",
            rCname: "{{ lang._('Same CNAME target as')|escape_js }}",
            rChain: "{{ lang._('Its CNAME chain goes through')|escape_js }}",
            rV2fly: "{{ lang._('Listed in the imported v2fly list')|escape_js }}",
            rCooc: "{{ lang._('Often resolved together with this application')|escape_js }}",
            rSite: "{{ lang._('Same site as a rule of this application:')|escape_js }}",
            rRule: "{{ lang._('Matches the discovery rule')|escape_js }}"
        };
        const KIND_LABELS = {
            domain: "{{ lang._('domain: (domain and sub-domains)')|escape_js }}",
            full: "{{ lang._('full: (exact name)')|escape_js }}",
            keyword: "{{ lang._('keyword: (discovery rule)')|escape_js }}",
            regexp: "{{ lang._('regexp: (discovery rule)')|escape_js }}"
        };
        const MAX_IMPORT = 1000;
        const WARN_IMPORT = 300;

        let appStatus = {};
        let catalogDiff = {};
        let discoveryCounts = {};
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
                    appname: function (column, row) {
                        let out = $('<span></span>').append($('<span></span>').text(row.name || ''));
                        splitList(row.catalog_refs).forEach(function (ref) {
                            out.append(' ').append($('<span class="label label-info"></span>').text('v2fly:' + ref));
                        });
                        let d = catalogDiff[row.uuid];
                        if (d && d.error) {
                            out.append(' ').append(
                                $('<i class="fa fa-exclamation-triangle text-warning"></i>').attr('title', T.catalogError + ' ' + d.error)
                            );
                        } else if (d) {
                            let n = (d.added || []).length + (d.removed || []).length;
                            if (n > 0) {
                                out.append(' ').append(
                                    $('<a href="#" class="appalias-diff-link label label-warning"></a>')
                                        .attr('data-uuid', row.uuid)
                                        .text(n + ' ' + T.updates)
                                );
                            }
                        }
                        let nd = parseInt(discoveryCounts[row.uuid] || 0);
                        if (nd > 0) {
                            out.append(' ').append(
                                $('<a href="#" class="appalias-disc-link label label-success"></a>').text(nd + ' ' + T.dProposals)
                            );
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
                }
            }
        });

        $("#grid-apps").on('loaded.rs.jquery.bootgrid', function () {
            gridLoaded = true;
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

        // --- Catalogue : informations ---
        function loadCatalogInfo() {
            ajaxGet('/api/appalias/catalog/info', {}, function (data, status) {
                let box = $('#catalog-info').empty();
                if (!data || data.error) {
                    box.addClass('text-danger').text(T.catalogError + ' ' + errorText(data));
                    return;
                }
                box.text('v2fly domain-list-community, ' + T.commit + ' ' + String(data.commit || '').substring(0, 12) +
                    ' (' + String(data.date || '').substring(0, 10) + '), ' + parseInt(data.lists || 0) + ' ' + T.lists +
                    ', MIT License.');
                let online = data.origin === 'online';
                $('#catalog-origin').text((online ? T.catOnline : T.catEmbedded) + ' ' +
                    String(data.date || '').substring(0, 10) + ' (' + String(data.commit || '').substring(0, 7) + ')');
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
                    row.append($('<strong></strong>').text(String(cat.name || '')));
                    row.append($('<br/>'));
                    (cat.lists || []).forEach(function (item) {
                        let btn = $('<button type="button" class="btn btn-default btn-sm"></button>')
                            .attr('title', parseInt(item.rules || 0) + ' ' + T.rules)
                            .text(item.name)
                            .prop('disabled', !!item.broken);
                        btn.on('click', function () {
                            openPreview(item.name);
                        });
                        row.append(btn).append(' ');
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
            let head = $('<div></div>').appendTo(body);
            head.append($('<strong></strong>').text(item.name));
            head.append(' ').append($('<span class="badge"></span>').text(parseInt(item.rules || 0) + ' ' + T.rules));
            (item.attrs || []).forEach(function (attr) {
                head.append(' ').append($('<span class="label label-default"></span>').text('@' + attr));
            });
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
            body.append($('<div style="margin-top: 6px"></div>').append(btn));
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

        function updateSelection() {
            let n = $('#catalog-rules input.appalias-rule:checked').length;
            let box = $('#catalog-count').removeClass('text-warning text-danger').text(T.selected + ' ' + n);
            if (n > MAX_IMPORT) {
                box.addClass('text-danger').append(' - ').append(document.createTextNode(T.tooMany));
            } else if (n > WARN_IMPORT) {
                box.addClass('text-warning').append(' - ').append(document.createTextNode(T.many));
            }
            $('#catalog-import').prop('disabled', n === 0 || n > MAX_IMPORT || !preview || !preview.importable);
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

            let groups = {domain: [], full: [], keyword: [], regexp: []};
            (data.rules || []).forEach(function (rule, idx) {
                if (groups[rule.kind] !== undefined) {
                    groups[rule.kind].push([rule, idx]);
                }
            });
            let box = $('#catalog-rules').empty();
            ['domain', 'full', 'keyword', 'regexp'].forEach(function (kind) {
                if (!groups[kind].length) {
                    return;
                }
                let group = $('<div></div>').attr('data-kind', kind);
                let head = $('<div class="appalias-group-head"></div>').appendTo(group);
                head.append($('<strong></strong>').text(KIND_LABELS[kind] + ' (' + groups[kind].length + ')'));
                let selAll = $('<a href="#"></a>').text(T.all);
                let selNone = $('<a href="#"></a>').text(T.none);
                head.append(' ').append(selAll).append(' / ').append(selNone);
                selAll.on('click', function (e) {
                    e.preventDefault();
                    group.find('input.appalias-rule:enabled').prop('checked', true);
                    updateSelection();
                });
                selNone.on('click', function (e) {
                    e.preventDefault();
                    group.find('input.appalias-rule').prop('checked', false);
                    updateSelection();
                });
                if (kind === 'keyword' || kind === 'regexp') {
                    group.append($('<div class="text-muted"></div>').text(T.discovery));
                }
                groups[kind].forEach(function (pair) {
                    let rule = pair[0];
                    let attrs = rule.attrs || [];
                    let discovery = !!rule.discovery;
                    let checked = !discovery && !rule.shared && !rule.refused && attrs.indexOf('ads') === -1;
                    let input = $('<input type="checkbox" class="appalias-rule"/>')
                        .attr('data-idx', pair[1])
                        .prop('checked', checked)
                        .prop('disabled', !!rule.refused || !data.importable);
                    let label = $('<label></label>').append(input).append(' ').append($('<code></code>').text(rule.rule));
                    attrs.forEach(function (attr) {
                        label.append($('<span class="label label-default"></span>').text('@' + attr));
                    });
                    if (rule.shared) {
                        label.append(' ').append(
                            $('<span class="text-warning"></span>').attr('title', T.sharedHelp)
                                .append('<i class="fa fa-share-alt"></i> ')
                                .append(document.createTextNode(T.sharedCdn))
                        );
                    }
                    if (rule.refused) {
                        label.attr('title', refusalText(rule)).addClass('text-muted');
                    }
                    let row = $('<div class="checkbox"></div>').append(label);
                    if (discovery) {
                        row.addClass('appalias-discovery');
                    }
                    group.append(row);
                });
                box.append(group);
            });

            $('#catalog-name').val(data.suggested_name || '');
            $('input[name="catalog-target"][value="new"]').prop('checked', true);
            updateSelection();
        }

        function loadApps(selected) {
            ajaxCall('/api/appalias/settings/searchApp', {current: 1, rowCount: -1, sort: {name: 'asc'}}, function (data, status) {
                let sel = $('#catalog-app').empty();
                ((data && data.rows) || []).forEach(function (row) {
                    sel.append($('<option></option>').attr('value', row.uuid).text(row.name));
                });
                if (selected) {
                    sel.val(selected);
                }
            });
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
                loadApps();
                $('#catalogPreview').modal('show');
            });
        }

        $('#catalog-rules').on('change', 'input.appalias-rule', updateSelection);

        $('#catalog-import').on('click', function () {
            if (!preview) {
                return;
            }
            let rules = [];
            let adsTotal = 0;
            let adsChecked = 0;
            $('#catalog-rules input.appalias-rule').each(function () {
                let rule = preview.rules[parseInt($(this).attr('data-idx'))];
                let isAds = (rule.attrs || []).indexOf('ads') !== -1;
                if (isAds) {
                    adsTotal++;
                }
                if ($(this).prop('checked')) {
                    rules.push(rule.rule);
                    if (isAds) {
                        adsChecked++;
                    }
                }
            });
            // Toutes les règles @ads décochées : la référence l'enregistre (« netflix@-ads »),
            // les mises à jour du catalogue ne les reproposeront pas.
            let ref = preview.ref;
            if (adsTotal > 0 && adsChecked === 0 && ref.split('@').indexOf('-ads') === -1) {
                ref = ref + '@-ads';
            }
            let mode = $('input[name="catalog-target"]:checked').val() === 'merge' ? 'merge' : 'new';
            let payload = {ref: ref, mode: mode, rules: rules};
            if (mode === 'merge') {
                payload.app_uuid = String($('#catalog-app').val() || '');
            } else {
                payload.name = String($('#catalog-name').val() || '').trim();
            }
            $('#catalog-import').prop('disabled', true);
            ajaxCall('/api/appalias/catalog/import', payload, function (data, status) {
                if (data && data.result === 'saved') {
                    $('#catalogPreview').modal('hide');
                    let msg = $('#catalog-message').removeClass('alert-danger').addClass('alert-info').text(T.saved);
                    if (data.warning) {
                        msg.append($('<div></div>').text(String(data.warning)));
                    }
                    msg.show();
                    $('#appaliasChangeMessage').show();
                    refreshDiff();
                    reloadGridIfLoaded();
                } else {
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
                    label.addClass('label-default').text(state === 'disabled' ? T.stDisabled : T.stStopped);
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
            msg.show();
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

        // --- Découverte ---
        let discApps = [];
        let learnTimer = null;

        function loadDiscApps(done) {
            ajaxCall('/api/appalias/settings/searchApp', {current: 1, rowCount: -1, sort: {name: 'asc'}}, function (data, status) {
                discApps = ((data && data.rows) || []).map(function (row) {
                    return {uuid: String(row.uuid), name: String(row.name || '')};
                });
                let sel = $('#discovery-learn-app').empty();
                sel.append($('<option value=""></option>').text(T.dLearnAny));
                discApps.forEach(function (app) {
                    sel.append($('<option></option>').attr('value', app.uuid).text(app.name));
                });
                if (done) {
                    done();
                }
            });
        }

        function appSelect(placeholder) {
            let sel = $('<select class="form-control input-sm" style="display: inline-block; width: auto"></select>');
            sel.append($('<option value=""></option>').text(placeholder));
            discApps.forEach(function (app) {
                sel.append($('<option></option>').attr('value', app.uuid).text(app.name));
            });
            return sel;
        }

        function reasonText(r) {
            switch (r.code) {
                case 'cname_neighbor':
                    return T.rCname + ' ' + String(r.name || '') + ' (' + String(r.target || '') + ')';
                case 'chain':
                    return T.rChain + ' ' + String(r.name || '');
                case 'v2fly_imported':
                    return T.rV2fly + ' ' + String(r.list || '');
                case 'cooccurrence':
                    return T.rCooc + ' (' + parseInt(r.count || 0) + '/' + parseInt(r.views || 0) + ' ' +
                        T.dViews + ', lift ' + String(r.lift || '') + ')';
                case 'same_site':
                    return T.rSite + ' ' + String(r.site || '');
                case 'keyword':
                case 'regexp':
                    return T.rRule + ' ' + String(r.rule || '');
            }
            return String(r.code || '');
        }

        function discMessage(ok, text) {
            $('#discovery-message').removeClass('alert-info alert-danger')
                .addClass(ok ? 'alert-info' : 'alert-danger').text(text).show();
        }

        function acceptRule(name, rule, uuid, done) {
            ajaxCall('/api/appalias/discovery/accept', {name: name, rule: rule, app_uuid: uuid}, function (data, status) {
                let ok = data && data.result === 'saved';
                if (ok) {
                    $('#appaliasChangeMessage').show();
                }
                if (done) {
                    done(ok, data);
                } else {
                    discMessage(ok, ok ? T.dSaved : T.failed + ' ' + errorText(data));
                    loadDiscovery();
                    reloadGridIfLoaded();
                }
            });
        }

        function ignoreRule(rule, add) {
            ajaxCall('/api/appalias/discovery/' + (add ? 'ignore' : 'unignore'), {rule: rule}, function (data, status) {
                if (!(data && data.result === 'saved')) {
                    discMessage(false, T.failed + ' ' + errorText(data));
                }
                loadDiscovery();
            });
        }

        function renderProposal(item) {
            let row = $('<div class="appalias-proposal"></div>');
            let check = $('<input type="checkbox" class="appalias-proposal-check"/>').prop('checked', !!item.checked);
            let head = $('<div></div>').appendTo(row);
            head.append(check).append(' ').append($('<code></code>').text(String(item.name)));
            let score = parseInt(item.score || 0);
            let bar = $('<div class="progress appalias-score"><div class="progress-bar"></div></div>');
            bar.find('.progress-bar').css('width', Math.min(100, score) + '%')
                .addClass(item.strong ? 'progress-bar-success' : 'progress-bar-info').text(String(score));
            head.append(' ').append(bar);
            let codes = (item.reasons || []).map(function (r) { return r.code; });
            if (codes.indexOf('cname_neighbor') !== -1 || codes.indexOf('chain') !== -1) {
                head.append(' ').append($('<span class="label label-primary"></span>').text(T.dCname));
            }
            if (codes.indexOf('v2fly_imported') !== -1) {
                head.append(' ').append($('<span class="label label-info"></span>').text('v2fly'));
            }
            if (codes.indexOf('cooccurrence') !== -1) {
                head.append(' ').append($('<span class="label label-default"></span>').text(T.dCooc));
            }
            if (item.strong) {
                head.append(' ').append($('<span class="label label-success"></span>').text(T.dStrong));
            }
            if (item.new) {
                head.append(' ').append($('<span class="label label-warning"></span>').text(T.dNew));
            }
            if (item.shared) {
                head.append(' ').append($('<span class="label label-danger"></span>').attr('title', T.sharedHelp).text(T.sharedCdn));
            }
            head.append(' ').append($('<span class="text-muted"></span>').text(parseInt(item.views || 0) + ' ' + T.dViews));
            row.append($('<div class="text-muted"></div>').text((item.reasons || []).map(reasonText).join('. ')));
            let actions = $('<div class="form-inline"></div>').appendTo(row);
            let ruleSel = $('<select class="form-control input-sm appalias-proposal-rule"></select>');
            (item.options || [item.rule]).forEach(function (opt) {
                ruleSel.append($('<option></option>').attr('value', opt).text(opt));
            });
            ruleSel.val(item.rule);
            actions.append(ruleSel).append(' ');
            let accept = $('<button type="button" class="btn btn-xs btn-primary"></button>').text(T.dAccept);
            accept.on('click', function () {
                acceptRule(item.name, String(ruleSel.val()), item.app_uuid);
            });
            let ignore = $('<button type="button" class="btn btn-xs btn-default"></button>').text(T.dIgnore);
            ignore.on('click', function () {
                ignoreRule(String(ruleSel.val()), true);
            });
            let other = appSelect(T.dOtherApp);
            other.on('change', function () {
                if ($(this).val()) {
                    acceptRule(item.name, String(ruleSel.val()), String($(this).val()));
                }
            });
            actions.append(accept).append(' ').append(ignore).append(' ').append(other);
            row.data('item', item);
            row.data('rule', ruleSel);
            return row;
        }

        function renderProposals(list) {
            let box = $('#discovery-proposals').empty();
            if (!list.length) {
                box.append($('<p class="text-muted"></p>').text(T.dNone));
                return;
            }
            let groups = {};
            let order = [];
            list.forEach(function (item) {
                let key = String(item.app_uuid);
                if (!groups[key]) {
                    groups[key] = [];
                    order.push(key);
                }
                groups[key].push(item);
            });
            order.forEach(function (key) {
                let panel = $('<div class="panel panel-default appalias-card"></div>');
                let heading = $('<div class="panel-heading"></div>').appendTo(panel);
                heading.append($('<strong></strong>').text(String(groups[key][0].app || '')))
                    .append(' ').append($('<span class="badge"></span>').text(groups[key].length + ' ' + T.dProposals));
                let body = $('<div class="panel-body"></div>').appendTo(panel);
                groups[key].forEach(function (item) {
                    body.append(renderProposal(item));
                });
                let bulk = $('<button type="button" class="btn btn-xs btn-primary"></button>').text(T.dAcceptChecked);
                bulk.on('click', function () {
                    let todo = [];
                    body.find('.appalias-proposal').each(function () {
                        if ($(this).find('.appalias-proposal-check').prop('checked')) {
                            todo.push([$(this).data('item'), String($(this).data('rule').val())]);
                        }
                    });
                    let errors = [];
                    let seen = {};
                    (function next() {
                        if (!todo.length) {
                            discMessage(!errors.length, errors.length ? T.failed + ' ' + errors.join(' ') : T.dSaved);
                            loadDiscovery();
                            reloadGridIfLoaded();
                            return;
                        }
                        let pair = todo.shift();
                        if (seen[pair[1]]) {
                            next();   // même règle domain: déjà enregistrée pour ce groupe
                            return;
                        }
                        seen[pair[1]] = true;
                        acceptRule(pair[0].name, pair[1], pair[0].app_uuid, function (ok, data) {
                            if (!ok) {
                                errors.push(String(pair[0].name) + ': ' + errorText(data));
                            }
                            next();
                        });
                    })();
                });
                body.append($('<div style="margin-top: 6px"></div>').append(bulk));
                box.append(panel);
            });
        }

        function renderUnassigned(groups) {
            let box = $('#discovery-unassigned').empty();
            if (!groups.length) {
                box.append($('<p class="text-muted"></p>').text(T.dNone));
                return;
            }
            let table = $('<table class="table table-condensed"></table>').appendTo(box);
            groups.forEach(function (group) {
                table.append($('<tr class="active"></tr>').append(
                    $('<td colspan="3"></td>').append($('<strong></strong>').text(String(group.site || '')))
                        .append(' ').append($('<span class="text-muted"></span>').text(parseInt(group.views || 0) + ' ' + T.dViews))
                ));
                (group.names || []).forEach(function (item) {
                    let name = $('<td></td>').append($('<code></code>').text(String(item.name)));
                    if (item.new) {
                        name.append(' ').append($('<span class="label label-warning"></span>').text(T.dNew));
                    }
                    if (item.shared) {
                        name.append(' ').append($('<span class="label label-danger"></span>').text(T.sharedCdn));
                    }
                    let v2 = $('<td></td>');
                    (item.v2fly || []).forEach(function (list) {
                        let btn = $('<button type="button" class="btn btn-xs btn-default"></button>')
                            .text(T.dCreateApp + ' v2fly:' + String(list));
                        btn.on('click', function () {
                            openPreview(String(list));
                        });
                        v2.append(btn).append(' ');
                    });
                    let actions = $('<td class="form-inline"></td>');
                    let add = appSelect(T.dAddTo);
                    add.on('change', function () {
                        if ($(this).val()) {
                            acceptRule(item.name, 'full:' + item.name, String($(this).val()));
                        }
                    });
                    let ignore = $('<button type="button" class="btn btn-xs btn-default"></button>').text(T.dIgnore);
                    ignore.on('click', function () {
                        ignoreRule('full:' + item.name, true);
                    });
                    actions.append(add).append(' ').append(ignore);
                    table.append($('<tr></tr>').append(name).append(v2).append(actions));
                });
            });
        }

        function renderIgnored(list) {
            let box = $('#discovery-ignored').empty();
            if (!list.length) {
                box.append($('<p class="text-muted"></p>').text(T.dNoIgnored));
                return;
            }
            list.forEach(function (rule) {
                let btn = $('<a href="#"></a>').text(T.dUnignore);
                btn.on('click', function (e) {
                    e.preventDefault();
                    ignoreRule(String(rule), false);
                });
                box.append($('<div></div>').append($('<code></code>').text(String(rule))).append(' ').append(btn));
            });
        }

        function renderLearn(learn) {
            clearInterval(learnTimer);
            let active = !!(learn && learn.active);
            $('#discovery-learn-start').prop('disabled', active);
            $('#discovery-learn-stop').toggle(active);
            let countdown = $('#discovery-learn-countdown').empty();
            let names = $('#discovery-learn-names').empty();
            if (active) {
                let left = parseInt(learn.remaining || 0);
                countdown.text(left + ' ' + T.dLearnLeft);
                learnTimer = setInterval(function () {
                    left--;
                    if (left <= 0) {
                        clearInterval(learnTimer);
                        countdown.empty();
                        // fenêtre écoulée : le relais finit la session, puis écrit ses données
                        setTimeout(refreshDiscovery, 8000);
                        return;
                    }
                    countdown.text(left + ' ' + T.dLearnLeft);
                }, 1000);
            }
            if (learn && (learn.names || []).length) {
                names.append($('<p class="text-muted"></p>').text(learn.names.length + ' ' + T.dLearnSeen));
                let isNew = {};
                (learn.new || []).forEach(function (n) { isNew[n] = true; });
                learn.names.slice(0, 200).forEach(function (n) {
                    let line = $('<div></div>').append($('<code></code>').text(String(n)));
                    if (isNew[n]) {
                        line.append(' ').append($('<span class="label label-warning"></span>').text(T.dNew));
                    }
                    names.append(line);
                });
            }
        }

        function loadDiscovery() {
            ajaxGet('/api/appalias/discovery/status', {}, function (st, status) {
                st = st || {};
                discoveryCounts = st.counts || {};
                reloadGridIfLoaded();
                $('#discovery-off').toggle(!st.enabled);
                $('#discovery-on').toggle(!!st.enabled);
                if (!st.enabled) {
                    return;
                }
                let notes = $('#discovery-notes').empty();
                if (st.error) {
                    notes.append($('<div class="alert alert-danger"></div>').text(String(st.error)));
                } else if (!st.exported) {
                    notes.append($('<div class="alert alert-warning"></div>').text(T.dNotApplied));
                } else if (!st.data) {
                    notes.append($('<div class="alert alert-info"></div>').text(T.dNoData));
                }
                if (st.regex_timeout) {
                    notes.append($('<div class="text-warning"></div>').text(T.dRegexTimeout));
                }
                let saved = st.saved ? new Date(st.saved * 1000) : null;
                let hhmm = saved ? ('0' + saved.getHours()).slice(-2) + ':' + ('0' + saved.getMinutes()).slice(-2) : '-';
                $('#discovery-counters').text(
                    parseInt(st.observed || 0) + ' ' + T.dObserved + ', ' +
                    parseInt(st.uncovered || 0) + ' ' + T.dUncovered + ', ' +
                    parseInt(st.proposals || 0) + ' ' + T.dProposals + ', ' +
                    parseInt(st.unassigned || 0) + ' ' + T.dUnassigned + ', ' +
                    parseInt(st.dropped || 0) + ' ' + T.dDropped + ', ' +
                    T.dRetention + ' ' + parseInt(st.retention_days || 0) + ', ' +
                    T.dDataAt + ' ' + hhmm
                );
                renderLearn(st.learn);
                renderIgnored(st.ignored || []);
                ajaxGet('/api/appalias/discovery/proposals', {}, function (data, status) {
                    renderProposals((data && data.proposals) || []);
                });
                ajaxGet('/api/appalias/discovery/unassigned', {}, function (data, status) {
                    renderUnassigned((data && data.groups) || []);
                });
            });
        }

        function refreshDiscovery() {
            ajaxCall('/api/appalias/discovery/refresh', {}, function (data, status) {
                // le relais écrit ses données à réception du signal
                setTimeout(loadDiscovery, 1500);
            });
        }

        $('#discovery-refresh').on('click', refreshDiscovery);

        $('#discovery-learn-start').on('click', function () {
            let payload = {seconds: 120};
            let app = String($('#discovery-learn-app').val() || '');
            if (app !== '') {
                payload.app_uuid = app;
            }
            ajaxCall('/api/appalias/discovery/learn', payload, function (data, status) {
                if (!(data && data.result === 'ok')) {
                    discMessage(false, T.failed + ' ' + errorText(data));
                    return;
                }
                setTimeout(loadDiscovery, 1500);
            });
        });

        $('#discovery-learn-stop').on('click', function () {
            ajaxCall('/api/appalias/discovery/stopLearn', {}, function (data, status) {
                setTimeout(loadDiscovery, 1500);
            });
        });

        $('#discovery-purge').on('click', function () {
            BootstrapDialog.confirm({
                title: T.dPurgeTitle,
                message: T.dPurgeConfirm,
                type: BootstrapDialog.TYPE_DANGER,
                btnOKLabel: T.dPurgeTitle,
                callback: function (confirmed) {
                    if (!confirmed) {
                        return;
                    }
                    ajaxCall('/api/appalias/discovery/purge', {}, function (data, status) {
                        if (!(data && data.result === 'ok')) {
                            discMessage(false, T.failed + ' ' + errorText(data));
                        }
                        setTimeout(loadDiscovery, 1000);
                    });
                }
            });
        });

        $('#discovery-open-settings').on('click', function () {
            $('#maintabs a[href="#settings"]').tab('show');
        });

        $("#grid-apps").on('click', '.appalias-disc-link', function (e) {
            e.preventDefault();
            e.stopPropagation();
            $('#maintabs a[href="#discovery"]').tab('show');
        });

        $('#maintabs a[href="#discovery"]').on('shown.bs.tab', function () {
            loadDiscApps(loadDiscovery);
        });

        updateServiceControlUI('appalias');
        refreshRelayBadge();
        refreshStatus();
        refreshDiff();
        loadCatalogInfo();
        loadPopular();
        loadUnboundStatus();
        loadDiscApps(loadDiscovery);
    });
</script>

<div id="appalias-relay-status">
    <strong>{{ lang._('Relay') }}</strong> <span id="appalias-relay-badge"></span>
    <div id="appalias-restart-needed" class="alert alert-warning" style="display: none" role="alert"></div>
</div>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li class="active"><a data-toggle="tab" href="#apps">{{ lang._('Applications') }}</a></li>
    <li><a data-toggle="tab" href="#catalog">{{ lang._('Catalog') }}</a></li>
    <li><a data-toggle="tab" href="#discovery">{{ lang._('Discovery') }}</a></li>
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
                <th data-column-id="domains" data-type="string">{{ lang._('Domains') }}</th>
                <th data-column-id="networks" data-type="string" data-visible="false">{{ lang._('Networks') }}</th>
                <th data-column-id="families" data-type="string">{{ lang._('Families') }}</th>
                <th data-column-id="ipcount" data-type="string" data-formatter="ipcount" data-sortable="false">{{ lang._('IPs v4/v6') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Commands') }}</th>
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
                <div class="panel-body">
                    <strong id="catalog-origin"></strong>
                    <button type="button" class="btn btn-xs btn-default" id="catalog-update" style="display: none">
                        {{ lang._('Update catalog now') }}
                    </button>
                    <button type="button" class="btn btn-xs btn-default" id="catalog-reset" style="display: none">
                        {{ lang._('Revert to embedded') }}
                    </button>
                    <p id="catalog-info" class="text-muted"></p>
                </div>
            </div>
            <div id="catalog-message" class="alert alert-info" style="display: none" role="alert"></div>
            <h3>{{ lang._('Popular apps') }}</h3>
            <div id="catalog-popular"></div>
            <h3>{{ lang._('Search the catalog') }}</h3>
            <div class="form-group">
                <input type="text" id="catalog-q" class="form-control" maxlength="64" autocomplete="off"
                       placeholder="{{ lang._('Search a list (netflix) or a domain (nflxvideo.net)') }}"/>
            </div>
            <p class="text-muted">
                {{ lang._('Importing only saves the application: nothing changes on the firewall before you click Apply.') }}
            </p>
        </div>
        <div id="catalog-results" class="row" style="margin: 0"></div>
        <div class="col-md-12"><br/></div>
    </div>

    <div id="discovery" class="tab-pane fade in">
        <div class="col-md-12">
            <div id="discovery-message" class="alert alert-info" style="display: none; margin-top: 10px" role="alert"></div>
            <div id="discovery-off" class="panel panel-default" style="display: none; margin-top: 10px">
                <div class="panel-heading"><strong>{{ lang._('Discovery is off') }}</strong></div>
                <div class="panel-body">
                    <p>
                        {{ lang._('When enabled, the relay counts the DNS names it sees (names only, never which client asked) and suggests rules for your applications: CNAME targets, sub-domains, CDN names, names resolved together. Nothing is added without your validation and Apply.') }}
                    </p>
                    <p class="text-muted">
                        {{ lang._('With the automatic Unbound forwarding, the relay only sees the application domains and their CNAME chains: discovery then completes existing applications. It sees other domains only when all DNS queries go through the relay.') }}
                    </p>
                    <button type="button" class="btn btn-primary btn-sm" id="discovery-open-settings">
                        {{ lang._('Enable it in Settings') }}
                    </button>
                </div>
            </div>
            <div id="discovery-on" style="display: none">
                <div style="margin-top: 10px">
                    <span id="discovery-counters" class="text-muted"></span>
                    <button type="button" class="btn btn-xs btn-default" id="discovery-refresh">
                        <i class="fa fa-refresh"></i> {{ lang._('Refresh') }}
                    </button>
                    <button type="button" class="btn btn-xs btn-default" id="discovery-purge">
                        {{ lang._('Erase discovery data') }}
                    </button>
                </div>
                <div id="discovery-notes" style="margin-top: 6px"></div>
                <div class="panel panel-default" style="margin-top: 10px">
                    <div class="panel-heading"><strong>{{ lang._('Learn 2 min') }}</strong></div>
                    <div class="panel-body form-inline">
                        <p class="text-muted">
                            {{ lang._('Start, then use the application on a client for two minutes: names seen during the window are highlighted, "new" when never seen before.') }}
                        </p>
                        <select id="discovery-learn-app" class="form-control input-sm"></select>
                        <button type="button" class="btn btn-sm btn-primary" id="discovery-learn-start">{{ lang._('Start learning') }}</button>
                        <button type="button" class="btn btn-sm btn-default" id="discovery-learn-stop" style="display: none">{{ lang._('Stop') }}</button>
                        <strong id="discovery-learn-countdown"></strong>
                        <div id="discovery-learn-names" style="margin-top: 6px"></div>
                    </div>
                </div>
                <h3>{{ lang._('Proposals') }}</h3>
                <div id="discovery-proposals"></div>
                <h3>{{ lang._('Unassigned') }}</h3>
                <div id="discovery-unassigned"></div>
                <h3>{{ lang._('Ignored') }}</h3>
                <div id="discovery-ignored"></div>
                <p class="text-muted">
                    {{ lang._('Accepting only saves the rule in the application: nothing changes on the firewall before you click Apply. Ignored rules are kept in the configuration (and its backups).') }}
                </p>
            </div>
        </div>
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
                The <i>Catalog</i> tab searches an embedded snapshot of
                <a href="https://github.com/v2fly/domain-list-community" target="_blank" rel="noopener noreferrer">v2fly domain-list-community</a>
                (MIT License). Nothing is downloaded by the firewall. The preview lets you choose the rules to import:
                rules tagged <code>@ads</code> and rules on shared platforms (CDN) are unchecked by default;
                <code>keyword:</code> and <code>regexp:</code> are discovery rules, kept but never applied by the relay.
                An imported application shows its catalog lists and, after a plugin update, the number of catalog
                updates you can review and accept.
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
                <div id="catalog-rules" class="appalias-rules"></div>
                <hr/>
                <p id="catalog-count"></p>
                <div class="radio">
                    <label>
                        <input type="radio" name="catalog-target" value="new" checked="checked"/>
                        {{ lang._('New application, named:') }}
                    </label>
                    <input type="text" id="catalog-name" class="form-control" maxlength="31" style="display: inline-block; width: auto"/>
                </div>
                <div class="radio">
                    <label>
                        <input type="radio" name="catalog-target" value="merge"/>
                        {{ lang._('Merge into the application:') }}
                    </label>
                    <select id="catalog-app" class="form-control" style="display: inline-block; width: auto"></select>
                </div>
                <div id="catalog-preview-error" class="alert alert-danger" style="display: none" role="alert"></div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-default" data-dismiss="modal">{{ lang._('Cancel') }}</button>
                <button type="button" class="btn btn-primary" id="catalog-import">{{ lang._('Import') }}</button>
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
