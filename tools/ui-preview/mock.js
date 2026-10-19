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

/*
 * Faux environnement OPNsense du banc de prévisualisation (tools/ui-preview) :
 * fonctions globales de opnsense.js et plugins jQuery employés par index.volt.
 *
 * Fixtures : window.APPALIAS_FIXTURES = {base: {clé: JSON}, states: {état: {clé: JSON}}}.
 * Clé = les deux premiers segments de l'URL après /api/appalias/, joints par « _ »
 * (/api/appalias/service/status -> service_status). L'état vient de ?state=
 * et recouvre les fixtures de base. Certaines fixtures sont indexées par un
 * paramètre : settings_getApp (uuid, « new » pour une création) et
 * catalog_preview (paramètre ref) ; la clé « * » sert de réponse par défaut.
 * Sans fixture : avertissement en console et réponse {}.
 */
(function () {
    'use strict';

    const FX = window.APPALIAS_FIXTURES || {base: {}, states: {}};
    const STATE = new URLSearchParams(window.location.search).get('state') || '';
    const DELAY = 60;
    const SELECTORS = {
        settings_getApp: function (url) { return decodeURIComponent((url.split('/')[5] || '')) || 'new'; },
        catalog_preview: function (url, data) { return String((data && data.ref) || ''); }
    };

    if (STATE !== '' && !FX.states[STATE]) {
        console.warn('[mock] unknown state "' + STATE + '", available: ' + Object.keys(FX.states).join(', '));
    }

    function clone(value) {
        return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
    }

    function keyOf(url) {
        let parts = String(url).split('?')[0].replace(/^\/api\/appalias\//, '').split('/').filter(Boolean);
        return parts.slice(0, 2).join('_');
    }

    function lookup(key) {
        let st = FX.states[STATE] || {};
        if (Object.prototype.hasOwnProperty.call(st, key)) {
            return st[key];
        }
        return FX.base[key];
    }

    function answer(url, data) {
        let key = keyOf(url);
        let fx = lookup(key);
        if (fx === undefined) {
            console.warn('[mock] no fixture "' + key + '.json" for ' + url + ', answering {}');
            return {};
        }
        let res = fx;
        if (SELECTORS[key]) {
            let sel = SELECTORS[key](String(url), data);
            if (Object.prototype.hasOwnProperty.call(fx, sel)) {
                res = fx[sel];
            } else if (Object.prototype.hasOwnProperty.call(fx, '*')) {
                res = clone(fx['*']);
                if (key === 'catalog_preview' && res && res.ref !== undefined && sel !== '') {
                    res.ref = sel;
                    res.name = sel.split('@')[0];
                    res.suggested_name = res.name.toUpperCase().replace(/-/g, '_');
                }
            } else {
                console.warn('[mock] fixture "' + key + '" has no entry "' + sel + '" nor "*", answering {}');
                return {};
            }
        }
        res = clone(res);
        if (key === 'catalog_diff' && data && data.uuid && res && res.apps) {
            let one = {};
            if (res.apps[data.uuid]) {
                one[data.uuid] = res.apps[data.uuid];
            }
            res.apps = one;
        }
        return res;
    }

    function call(kind, url, data, callback) {
        let res = answer(url, data);
        console.log('[mock] ' + kind + ' ' + url, data || {}, '->', res);
        let dfd = $.Deferred();
        setTimeout(function () {
            if (typeof callback === 'function') {
                callback(res, 'success');
            }
            dfd.resolve(res);
        }, DELAY);
        return dfd.promise();
    }

    window.ajaxGet = function (url, data, callback) {
        return call('GET', url, data, callback);
    };
    window.ajaxCall = function (url, data, callback) {
        return call('POST', url, data, callback);
    };

    // --- formulaires ---------------------------------------------------------
    function isOptionMap(value) {
        if (value === null || typeof value !== 'object' || Array.isArray(value)) {
            return false;
        }
        let keys = Object.keys(value);
        if (!keys.length) {
            return false;
        }
        return keys.every(function (k) {
            return value[k] !== null && typeof value[k] === 'object' && 'value' in value[k] && 'selected' in value[k];
        });
    }

    function setField($el, value) {
        if ($el.is(':checkbox')) {
            $el.prop('checked', String(value) === '1');
        } else if ($el.is('select')) {
            if (isOptionMap(value)) {
                // {clé: {value: libellé, selected: 0|1}} : (re)construit les options
                $el.empty();
                let picked = [];
                $.each(value, function (k, opt) {
                    $el.append($('<option></option>').attr('value', k).text(opt.value));
                    if (String(opt.selected) === '1') {
                        picked.push(k);
                    }
                });
                $el.val($el.prop('multiple') ? picked : (picked[0] || ''));
            } else if (value !== null && typeof value === 'object') {
                $el.val($el.prop('multiple') ? Object.keys(value) : '');
            } else {
                $el.val(String(value));
            }
        } else {
            $el.val(value === null || value === undefined ? '' : String(value));
        }
    }

    // {modèle: {section: {champ: valeur}}} -> champs d'id « modèle.section.champ ».
    function fillForm(prefix, values) {
        $.each(values || {}, function (key, value) {
            let id = prefix ? prefix + '.' + key : key;
            let $el = $('[id="' + id + '"]');
            if ($el.length) {
                setField($el, value);
            } else if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
                fillForm(id, value);
            }
        });
    }

    window.mapDataToFormUI = function (map) {
        let dfd = $.Deferred();
        let pending = Object.keys(map || {});
        if (!pending.length) {
            dfd.resolve();
        }
        pending.forEach(function (formId) {
            ajaxGet(map[formId], {}, function (data) {
                fillForm('', data);
                pending = pending.filter(function (f) { return f !== formId; });
                if (!pending.length) {
                    dfd.resolve();
                }
            });
        });
        return dfd.promise();
    };

    function formData(formId) {
        let out = {};
        $('#' + formId).find('input, select, textarea').each(function () {
            let id = this.id;
            if (!id) {
                return;
            }
            out[id] = $(this).is(':checkbox') ? ($(this).prop('checked') ? '1' : '0') : $(this).val();
        });
        return out;
    }

    window.saveFormToEndpoint = function (url, formId, callback) {
        ajaxCall(url, formData(formId), function (data, status) {
            if (typeof callback === 'function') {
                callback(data, status);
            }
        });
    };

    // Listes « tokenize » : la liste cachée est remplacée par des jetons.
    window.formatTokenizersUI = function () {
        $('select.tokenize[multiple]').each(function () {
            let $sel = $(this);
            $sel.siblings('.tokenize').remove();
            let ul = $('<ul class="tokens-container form-control"></ul>');
            $sel.find('option:selected').each(function () {
                ul.append($('<li class="token"></li>').append($('<span></span>').text($(this).text()))
                    .append('<a class="dismiss">&times;</a>'));
            });
            ul.append('<li class="token-input"><input type="text"/></li>');
            $sel.hide().after($('<div class="tokenize"></div>').append(ul));
        });
    };

    // Aide, mode avancé : mêmes bascules que la page OPNsense.
    $(document).on('click', 'a.showhelp', function (e) {
        e.preventDefault();
        $('[data-for="' + this.id + '"]').toggleClass('hidden');
    });
    $(document).on('click', '[id^="show_all_help_"]', function (e) {
        e.preventDefault();
        let on = $(this).hasClass('fa-toggle-off');
        $(this).toggleClass('fa-toggle-off fa-toggle-on');
        $(this).closest('form, .modal-body').find('[data-for]').toggleClass('hidden', !on);
    });
    $(document).on('click', '[id^="show_advanced_"]', function (e) {
        e.preventDefault();
        let on = $(this).hasClass('fa-toggle-off');
        $(this).toggleClass('fa-toggle-off fa-toggle-on');
        $(this).closest('form, .modal-body').find('tr[data-advanced="true"]').toggle(on);
    });
    $(function () {
        $('tr[data-advanced="true"]').hide();
        $('[data-for]').addClass('hidden');
    });

    $.fn.selectpicker = function () { return this; };

    // --- en-tête : boutons du service ----------------------------------------
    window.updateServiceControlUI = function (name) {
        console.log('[mock] updateServiceControlUI(' + name + ')');
        ajaxGet('/api/appalias/service/status', {}, function (data) {
            let running = data && data.status === 'running';
            $('#service_status_container').empty()
                .append($('<span class="label"></span>').addClass(running ? 'label-success' : 'label-default')
                    .text(running ? 'running' : String((data && data.status) || 'unknown')))
                .append(' ')
                .append($('<button class="btn btn-xs btn-default" type="button"></button>')
                    .append('<i class="fa fa-repeat fa-fw"></i>').attr('title', 'Restart'))
                .append($('<button class="btn btn-xs btn-default" type="button"></button>')
                    .append('<i class="fa fa-' + (running ? 'stop' : 'play') + ' fa-fw"></i>'));
        });
    };

    // --- boîtes de dialogue --------------------------------------------------
    function dialog(opts) {
        let $m = $(
            '<div class="modal fade" tabindex="-1" role="dialog"><div class="modal-dialog"><div class="modal-content">' +
            '<div class="modal-header"><button type="button" class="close" data-dismiss="modal">&times;</button>' +
            '<h4 class="modal-title"></h4></div><div class="modal-body"></div>' +
            '<div class="modal-footer"></div></div></div></div>'
        );
        $m.find('.modal-title').text(String(opts.title || ''));
        $m.find('.modal-body').text(String(opts.message || ''));
        let answered = false;
        function finish(ok) {
            if (answered) {
                return;
            }
            answered = true;
            $m.modal('hide');
            if (typeof opts.callback === 'function') {
                opts.callback(ok);
            }
        }
        let foot = $m.find('.modal-footer');
        if (opts.cancel !== false) {
            foot.append($('<button type="button" class="btn btn-default"></button>')
                .text(opts.btnCancelLabel || 'Cancel').on('click', function () { finish(false); }));
        }
        let kind = String(opts.type || '').replace('type-', '') || 'primary';
        foot.append($('<button type="button" class="btn"></button>')
            .addClass(kind === 'danger' ? 'btn-danger' : (kind === 'warning' ? 'btn-warning' : 'btn-primary'))
            .text(opts.btnOKLabel || 'OK').on('click', function () { finish(true); }));
        $m.on('hidden.bs.modal', function () {
            finish(false);
            $m.remove();
        });
        $m.modal('show');
        return $m;
    }

    window.BootstrapDialog = {
        TYPE_DEFAULT: 'type-default',
        TYPE_INFO: 'type-info',
        TYPE_PRIMARY: 'type-primary',
        TYPE_SUCCESS: 'type-success',
        TYPE_WARNING: 'type-warning',
        TYPE_DANGER: 'type-danger',
        confirm: function (opts) { return dialog(opts || {}); },
        show: function (opts) {
            return dialog($.extend({cancel: false, btnOKLabel: 'Close'}, opts || {}));
        },
        alert: function (message) {
            return dialog({title: 'Alert', message: typeof message === 'object' ? message.message : message, cancel: false});
        }
    };

    window.stdDialogConfirm = function (title, message, accept, decline, callback, type) {
        return dialog({title: title, message: message, btnOKLabel: accept, btnCancelLabel: decline,
            callback: function (ok) { if (ok && typeof callback === 'function') { callback(); } },
            type: type});
    };
    window.stdDialogRemove = function (title, message, accept, decline, callback) {
        return dialog({title: title || 'Remove', message: message || 'Remove this item?',
            btnOKLabel: accept || 'Yes', btnCancelLabel: decline || 'No', type: 'type-danger',
            callback: function (ok) { if (ok && typeof callback === 'function') { callback(); } }});
    };
    window.stdDialogInform = function (title, message, close, callback) {
        return dialog({title: title, message: message, btnOKLabel: close || 'Close', cancel: false,
            callback: function () { if (typeof callback === 'function') { callback(); } }});
    };

    // --- bouton « Apply » ----------------------------------------------------
    $.fn.SimpleActionButton = function (options) {
        options = options || {};
        return this.each(function () {
            let $btn = $(this);
            $btn.empty().append($('<b></b>').text($btn.attr('data-label') || 'Apply'))
                .append(' <i class="fa fa-spinner fa-spin" style="display: none"></i>');
            $btn.on('click', function () {
                $btn.find('.fa-spinner').show();
                ajaxCall($btn.attr('data-endpoint'), {}, function (data, status) {
                    $btn.find('.fa-spinner').hide();
                    if (typeof options.onAction === 'function') {
                        options.onAction(data, status);
                    }
                });
            });
        });
    };

    // --- bootgrid minimal ----------------------------------------------------
    function columnsOf($table) {
        return $table.find('thead th').map(function () {
            let $th = $(this);
            return {
                id: $th.attr('data-column-id'),
                text: $th.text(),
                formatter: $th.attr('data-formatter'),
                visible: $th.attr('data-visible') !== 'false',
                width: $th.attr('data-width')
            };
        }).get();
    }

    function defaultFormatters() {
        return {
            rowtoggle: function (column, row) {
                let on = String(row[column.id]) === '1';
                return '<span style="cursor: pointer" class="fa fa-fw ' +
                    (on ? 'fa-check-square-o' : 'fa-square-o') + ' command-toggle bootgrid-tooltip" data-value="' +
                    (on ? '1' : '0') + '" data-row-id="' + $('<i></i>').text(row.uuid).html() + '"></span>';
            },
            commands: function (column, row) {
                let id = $('<i></i>').text(row.uuid).html();
                return '<button type="button" class="btn btn-xs btn-default command-edit bootgrid-tooltip" data-row-id="' +
                    id + '"><span class="fa fa-fw fa-pencil"></span></button> ' +
                    '<button type="button" class="btn btn-xs btn-default command-copy bootgrid-tooltip" data-row-id="' +
                    id + '"><span class="fa fa-fw fa-clone"></span></button>' +
                    '<button type="button" class="btn btn-xs btn-default command-delete bootgrid-tooltip" data-row-id="' +
                    id + '"><span class="fa fa-fw fa-trash-o"></span></button>';
            }
        };
    }

    function gridRender($table) {
        let st = $table.data('mock-bootgrid');
        let query = String(st.search || '').toLowerCase();
        ajaxCall(st.opts.url, {current: 1, rowCount: -1, searchPhrase: st.search || ''}, function (data) {
            let rows = (data && data.rows) || [];
            if (query) {
                rows = rows.filter(function (r) { return JSON.stringify(r).toLowerCase().indexOf(query) !== -1; });
            }
            let $body = $table.find('tbody').empty();
            st.rows = rows;
            st.append(rows);
            $table.trigger('loaded.rs.jquery.bootgrid');
        });
    }

    $.fn.bootgrid = function (command, arg) {
        if (typeof command === 'object' || command === undefined) {
            return this.each(function () {
                let $table = $(this);
                if ($table.data('mock-bootgrid')) {
                    return;
                }
                let opts = command || {};
                let cols = columnsOf($table);
                let formatters = $.extend(defaultFormatters(), opts.formatters || {});
                let st = {opts: opts, cols: cols, formatters: formatters, rows: [], search: ''};
                st.append = function (rows) {
                    let $body = $table.find('tbody');
                    (rows || []).forEach(function (row) {
                        let $tr = $('<tr></tr>').attr('data-row-id', row.uuid);
                        cols.forEach(function (col) {
                            if (!col.visible) {
                                return;
                            }
                            let fmt = col.formatter && formatters[col.formatter];
                            let html = fmt ? fmt(col, row) :
                                $('<span></span>').text(row[col.id] === undefined ? '' : String(row[col.id])).html();
                            $tr.append($('<td></td>').html(html));
                        });
                        $body.append($tr);
                    });
                    $table.parent().find('.infos').text(
                        st.rows.length ? 'Showing 1 to ' + st.rows.length + ' of ' + st.rows.length + ' entries' :
                            'No results found!');
                };
                $table.addClass('bootgrid-table');
                $table.find('thead th').each(function (i) {
                    $(this).toggle(cols[i].visible);
                    if (cols[i].width) {
                        $(this).css('width', cols[i].width);
                    }
                });
                $table.data('mock-bootgrid', st);
                let $header = $('<div class="bootgrid-header container-fluid"></div>');
                let $search = $('<input type="search" class="form-control input-sm search" placeholder="Search"/>');
                let $actions = $('<div class="actions"></div>');
                // comme UIBootgrid : les boutons du pied de table passent dans l'en-tête
                $table.find('tfoot [data-action]').appendTo($actions);
                $table.find('tfoot').remove();
                $actions.append('<button type="button" class="btn btn-xs btn-default" data-action="refresh">' +
                    '<span class="fa fa-fw fa-refresh"></span></button>');
                $header.append($search).append($actions);
                $table.before($header);
                $table.after('<div class="bootgrid-footer"><span class="infos"></span></div>');
                $search.on('input', function () {
                    st.search = $search.val();
                    gridRender($table);
                });
                $actions.on('click', '[data-action="refresh"]', function () { gridRender($table); });
                $table.trigger('initialized.rs.jquery.bootgrid');
                gridRender($table);
            });
        }
        let st = this.data('mock-bootgrid');
        if (!st) {
            return this;
        }
        let $table = this;
        switch (command) {
            case 'reload':
                gridRender($table);
                break;
            case 'append':
                st.rows = st.rows.concat(arg || []);
                st.append(arg || []);
                $table.trigger('loaded.rs.jquery.bootgrid');
                break;
            case 'clear':
                st.rows = [];
                $table.find('tbody').empty();
                break;
            case 'getCurrentRows':
                return st.rows;
            case 'getSelectedRows':
                return [];
            case 'destroy':
                $table.removeData('mock-bootgrid');
                break;
        }
        return this;
    };

    // UIBootgrid : grille + dialogue d'édition (get/set/add/del/toggle).
    $.fn.UIBootgrid = function (params) {
        params = params || {};
        let $table = this;
        let dialogId = $table.attr('data-editDialog') || $table.attr('data-editdialog');
        let alertId = $table.attr('data-editAlert') || $table.attr('data-editalert');
        let opts = params.options || {};
        let editing = null;

        function reload() { $table.bootgrid('reload'); }

        function openDialog(uuid, clone) {
            editing = clone ? null : (uuid || null);
            ajaxGet(params.get + (uuid || ''), {}, function (payload) {
                let $dlg = $('#' + dialogId);
                $dlg.find('input:checkbox').prop('checked', false);
                $dlg.find('input:text, textarea').val('');
                fillForm('', payload);
                window.formatTokenizersUI();
                $dlg.find('tr[data-advanced="true"]').hide();
                let done = function () { $dlg.modal('show'); };
                if (typeof opts.onBeforeRenderDialog === 'function') {
                    $.when(opts.onBeforeRenderDialog(payload)).done(done);
                } else {
                    done();
                }
            });
        }

        this.bootgrid({url: params.search, formatters: opts.formatters || {}});

        $table.parent().on('click', '[data-action="add"]', function () { openDialog('', false); });
        $table.on('click', '.command-edit', function () { openDialog(String($(this).attr('data-row-id')), false); });
        $table.on('click', '.command-copy', function () { openDialog(String($(this).attr('data-row-id')), true); });
        $table.on('click', '.command-delete', function () {
            let uuid = String($(this).attr('data-row-id'));
            window.stdDialogRemove('Remove', 'Do you want to remove the selected item?', 'Yes', 'Cancel', function () {
                ajaxCall(params.del + uuid, {}, reload);
            });
        });
        $table.on('click', '.command-toggle', function () {
            let $el = $(this);
            let on = $el.attr('data-value') === '1';
            ajaxCall(params.toggle + $el.attr('data-row-id') + '/' + (on ? 0 : 1), {}, function () {
                $el.attr('data-value', on ? '0' : '1').toggleClass('fa-check-square-o fa-square-o');
            });
        });
        $('#btn_' + dialogId + '_save').on('click', function () {
            let url = editing ? params.set + editing : params.add;
            ajaxCall(url, formData('frm_' + dialogId), function () {
                $('#' + dialogId).modal('hide');
                if (alertId) {
                    $('#' + alertId).show();
                }
                reload();
            });
        });
        return this;
    };
})();

/* Ancre d'URL (#catalog, #settings, ...) : ouvre l'onglet correspondant, pour
   les captures du banc. */
$(function () {
    if (location.hash) {
        setTimeout(function () {
            $('a[data-toggle="tab"][href="' + location.hash + '"]').tab('show');
        }, 300);
    }
});
