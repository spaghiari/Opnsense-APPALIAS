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

<script>
    $( document ).ready(function () {
        let appStatus = {};

        function refreshStatus() {
            ajaxGet('/api/appalias/service/status', {}, function (data, status) {
                appStatus = (data && data.apps) ? data.apps : {};
                $("#grid-apps").bootgrid('reload');
            });
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
                    ipcount: function (column, row) {
                        let st = appStatus[row.uuid];
                        if (st === undefined) {
                            return '<span class="text-muted">-</span>';
                        }
                        let text = parseInt(st.v4 || 0) + ' / ' + parseInt(st.v6 || 0);
                        if (st.last_error) {
                            return '<span class="text-danger" title="' +
                                $('<div/>').text(st.last_error.message || '').html() + '">' +
                                text + ' <i class="fa fa-exclamation-triangle"></i></span>';
                        }
                        return text;
                    }
                }
            }
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

        $("#reconfigureAct").SimpleActionButton({
            onAction: function (data, status) {
                updateServiceControlUI('appalias');
                refreshStatus();
            }
        });

        updateServiceControlUI('appalias');
        refreshStatus();
    });
</script>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li class="active"><a data-toggle="tab" href="#apps">{{ lang._('Applications') }}</a></li>
    <li><a data-toggle="tab" href="#settings">{{ lang._('Réglages') }}</a></li>
    <li><a data-toggle="tab" href="#howto">{{ lang._("Comment l'utiliser") }}</a></li>
</ul>

<div class="tab-content content-box">
    <div id="apps" class="tab-pane fade in active">
        <table id="grid-apps" class="table table-condensed table-hover table-striped table-responsive"
               data-editDialog="DialogApp" data-editAlert="appaliasChangeMessage">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th>
                <th data-column-id="enabled" data-width="6em" data-type="string" data-formatter="rowtoggle">{{ lang._('Activée') }}</th>
                <th data-column-id="name" data-type="string">{{ lang._('Nom') }}</th>
                <th data-column-id="description" data-type="string">{{ lang._('Description') }}</th>
                <th data-column-id="domains" data-type="string">{{ lang._('Domaines') }}</th>
                <th data-column-id="networks" data-type="string" data-visible="false">{{ lang._('Réseaux') }}</th>
                <th data-column-id="families" data-type="string">{{ lang._('Familles') }}</th>
                <th data-column-id="ipcount" data-type="string" data-formatter="ipcount" data-sortable="false">{{ lang._('IP v4/v6') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Commandes') }}</th>
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
                {{ lang._("Après une modification, cliquer sur « Appliquer » pour créer les alias et recharger le relais.") }}
            </div>
            <hr/>
            <button class="btn btn-primary" id="reconfigureAct"
                    data-endpoint="/api/appalias/service/reconfigure"
                    data-label="{{ lang._('Appliquer') }}"
                    data-error-title="{{ lang._("Erreur lors de la reconfiguration d'AppAlias") }}"
                    type="button"></button>
            <br/><br/>
        </div>
    </div>

    <div id="settings" class="tab-pane fade in">
        {{ partial("layout_partials/base_form", ['fields': generalForm, 'id': 'frm_general_settings']) }}
        <div class="col-md-12">
            <hr/>
            <button class="btn btn-primary" id="saveAct" type="button"><b>{{ lang._('Enregistrer et appliquer') }}</b></button>
            <br/><br/>
        </div>
    </div>

    <div id="howto" class="tab-pane fade in">
        <div class="col-md-12">
            <h2>Principe</h2>
            <p>
                AppAlias crée un alias pare-feu de type <b>External</b> par application (par exemple <code>APP_EXEMPLE</code>).
                Le relais DNS <code>appaliasd</code> voit passer les réponses DNS&nbsp;: quand un nom correspond à une
                application, ses adresses A et AAAA sont ajoutées à l'alias <b>avant</b> que la réponse soit rendue au client.
                Les réseaux CIDR saisis à la main sont toujours présents dans l'alias.
            </p>
            <p>
                AppAlias ne crée <b>aucune règle</b>&nbsp;: vous utilisez les alias <code>APP_*</code> dans vos propres règles
                (blocage, autorisation, routage par passerelle).
            </p>

            <h2>1. Montage A&nbsp;: placer le relais derrière le résolveur local</h2>
            <p>Les clients continuent d'interroger le résolveur du pare-feu, qui transmet tout au relais&nbsp;:</p>
            <ul>
                <li>
                    <b>Unbound</b>&nbsp;: <i>Services &gt; Unbound DNS &gt; Query Forwarding</i>, ajouter une entrée sans domaine
                    (toutes les requêtes) vers le serveur <code>127.0.0.1</code>, port <code>53535</code>.
                </li>
                <li>
                    <b>Dnsmasq</b>&nbsp;: déclarer l'amont <code>127.0.0.1#53535</code> et ne plus utiliser les serveurs DNS
                    système.
                </li>
                <li>
                    Dans l'onglet <i>Réglages</i>, indiquer le <b>résolveur amont</b> réel (celui que vous utilisiez avant).
                    Ne pas y mettre le résolveur local lui-même&nbsp;: les requêtes tourneraient en boucle.
                </li>
            </ul>

            <h2>2. Exemple&nbsp;: faire passer une application par un VPN (PBR)</h2>
            <p>Dans <i>Pare-feu &gt; Règles &gt; LAN</i>, au-dessus de la règle d'autorisation par défaut&nbsp;:</p>
            <ol>
                <li>
                    <b>Pass</b>, IPv4, source <code>LAN net</code>, destination <code>APP_EXEMPLE</code>,
                    passerelle&nbsp;: votre passerelle VPN (réglages avancés de la règle).
                </li>
                <li>
                    <b>Block</b>, IPv6, source <code>LAN net</code>, destination <code>APP_EXEMPLE</code>&nbsp;:
                    sans cette règle, le trafic IPv6 de l'application sortirait hors du VPN (fuite).
                </li>
            </ol>

            <h2>3. Exemple&nbsp;: bloquer une application</h2>
            <p>
                <b>Block</b> (ou <b>Reject</b>), IPv4+IPv6, source <code>LAN net</code>, destination <code>APP_EXEMPLE</code>.
                Pour un exemple de réseau manuel, utiliser des plages de documentation comme <code>192.0.2.0/24</code>
                ou <code>2001:db8::/32</code>.
            </p>

            <h2>4. Limites</h2>
            <ul>
                <li>
                    <b>DNS chiffré (DoH, DoT)</b>&nbsp;: un client qui résout lui-même via HTTPS ne passe pas par le relais&nbsp;;
                    ses IP n'arrivent jamais dans l'alias. Bloquer le DoH/DoT ou le désactiver sur les clients.
                </li>
                <li>
                    <b>CDN partagés</b>&nbsp;: une même IP peut servir de nombreux sites. Bloquer ou router une application
                    peut toucher d'autres services hébergés sur le même CDN.
                </li>
                <li>
                    <b>Cache des clients</b>&nbsp;: une IP déjà en cache sur un poste avant l'activation (ou avant l'ajout du
                    domaine) n'est pas vue par le relais tant que le poste ne refait pas de requête. Vider le cache DNS du
                    poste si besoin. La marge de TTL couvre le cache du résolveur local et des clients ensuite.
                </li>
            </ul>
        </div>
    </div>
</div>

{{ partial("layout_partials/base_dialog", ['fields': formDialogApp, 'id': 'DialogApp', 'label': lang._('Application')]) }}
