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
            upDotPort53: "{{ lang._('DNS over TLS on port 53: DNS over TLS servers listen on port 853 (Quad9: 9.9.9.9, port 853, TLS name dns.quad9.net). Change the upstream port, then Save and apply.')|escape_js }}",
            ubRootWarn: "{{ lang._('While the relay is down, forwarded domains fall back to direct recursion and bypass DNS over TLS (and the . Query Forwarding). If outgoing port 53 is blocked, they fail until the relay is back.')|escape_js }}",
            commit: "{{ lang._('commit')|escape_js }}"
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
        });

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
                let upWarn = $('#upstream-port-warning').empty().hide();
                if (data.upstream_protocol === 'dot') {
                    upstream.text(T.upDot + (data.upstream_tls_name ? ' (' + String(data.upstream_tls_name) + ')' : '')).show();
                    if (parseInt(data.upstream_port) === 53) {
                        upWarn.text(T.upDotPort53).show();
                    }
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

        updateServiceControlUI('appalias');
        refreshStatus();
        refreshDiff();
        loadCatalogInfo();
        loadPopular();
        loadUnboundStatus();
    });
</script>

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
            <p id="catalog-info" class="text-muted"></p>
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

    <div id="settings" class="tab-pane fade in">
        <div class="col-md-12">
            <div id="unbound-status" class="panel panel-default" style="margin-top: 10px">
                <div class="panel-heading"><strong>{{ lang._('Unbound forwarding') }}</strong></div>
                <div class="panel-body">
                    <p id="upstream-summary" style="display: none"></p>
                    <p id="latency-summary" class="text-muted" style="display: none"></p>
                    <div id="upstream-port-warning" class="alert alert-warning" style="display: none" role="alert"></div>
                    <p id="unbound-summary" class="text-muted"></p>
                    <div id="unbound-root-warning" class="alert alert-warning" style="display: none" role="alert"></div>
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
