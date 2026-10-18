#!/usr/bin/env python3
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

"""Banc de prévisualisation de la page AppAlias, hors OPNsense (stdlib).

  python tools/ui-preview/build.py
  python -m http.server 8765 -d tools/ui-preview/out
  -> http://localhost:8765/index.html?state=stopped

Lit index.volt à chaque lancement (toujours sa version courante) et produit
dans out/ : index.html, mock.js, opnsense-lite.css et fixtures.js.

Traduction de Volt :
- {{ lang._('...')|escape_js }} -> chaîne échappée pour JS ; {{ lang._('...') }}
  -> texte échappé pour HTML (guillemets simples ou doubles) ;
- {{ partial("layout_partials/base_form" | "base_dialog", [...]) }} -> rendu
  simplifié (mêmes structures que base_form.volt, base_dialog.volt et
  form_input_tr.volt du core, stable/26.7) des champs de forms/*.xml ;
- commentaires {# #} retirés ; toute autre syntaxe {{ }} ou {% %} est
  neutralisée (supprimée) avec un avertissement.

Le script liste aussi les endpoints /api/appalias/... trouvés dans index.volt
et avertit de ceux qui n'ont pas de fixture.
"""

import argparse
import html
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MVC = os.path.join(ROOT, "net", "appalias", "src", "opnsense", "mvc", "app")
VOLT = os.path.join(MVC, "views", "OPNsense", "AppAlias", "index.volt")
FORMS = os.path.join(MVC, "controllers", "OPNsense", "AppAlias", "forms")
MODEL = os.path.join(MVC, "models", "OPNsense", "AppAlias", "AppAlias.xml")
FIXTURES = os.path.join(HERE, "fixtures")
DEFAULT_OUT = os.path.join(HERE, "out")
COPIED = ("mock.js", "opnsense-lite.css")

# Page d'aide : la page dans une iframe de largeur choisie. Un navigateur sans
# fenêtre (Edge --headless) ne descend pas sous ~490 px : sans iframe, une
# capture « 420 px » est mise en page à 492 px puis rognée.
MOBILE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>os-appalias - narrow preview</title></head>
<body style="margin:0;background:#ddd">
<!-- ?w=420&state=empty&tab=discovery&log=1 : largeur, état, onglet ; log=1 liste les éléments qui dépassent -->
<iframe id="f" style="border:0;background:#fff;display:block" height="1400"></iframe>
<pre id="log" style="margin:0;font-size:12px"></pre>
<script>
var q = new URLSearchParams(location.search), w = parseInt(q.get('w') || '420', 10), f = document.getElementById('f');
f.width = w;
f.src = 'index.html' + (q.get('state') ? '?state=' + q.get('state') : '') + (q.get('tab') ? '#' + q.get('tab') : '');
if (q.get('log')) {
    setTimeout(function () {
        var d = f.contentDocument, out = ['scrollWidth=' + d.documentElement.scrollWidth + ' viewport=' + w];
        d.querySelectorAll('body *').forEach(function (e) {
            if (e.closest('.modal, .table-responsive') || !e.getClientRects().length) { return; }
            var r = e.getBoundingClientRect();
            if (r.width && r.right > w + 1) {
                out.push(e.tagName + (e.id ? '#' + e.id : '') + '.' + String(e.className).split(' ').join('.') +
                    ' right=' + Math.round(r.right) + ' width=' + Math.round(r.width));
            }
        });
        document.getElementById('log').textContent = out.join('\\n');
    }, 6000);
}
</script></body></html>
"""

# Variables de la vue (IndexController::indexAction) -> formulaire XML.
FORM_VARS = {"generalForm": "general.xml", "formDialogApp": "dialogApp.xml"}

CDN = {
    "css": [
        "https://cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap/3.4.1/css/bootstrap.min.css",
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css",
    ],
    "js": [
        "https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js",
        "https://cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap/3.4.1/js/bootstrap.min.js",
    ],
}

STRING = r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")"""
LANG_RE = re.compile(r"\{\{\s*lang\._\(\s*" + STRING + r"\s*\)\s*(?:\|\s*(\w+)\s*)?\}\}")
PARTIAL_RE = re.compile(r"\{\{\s*partial\(\s*[\"']([^\"']+)[\"']\s*,\s*\[(.*?)\]\s*\)\s*\}\}", re.S)
COMMENT_RE = re.compile(r"\{#.*?#\}", re.S)
ARG_RE = re.compile(r"""['"](\w+)['"]\s*:\s*(?:lang\._\(\s*""" + STRING + r"""\s*\)|'([^']*)'|"([^"]*)"|(\w+))""")
# URL construite par concaténation dans index.volt (ignoreRule : ignore ou unignore).
DYNAMIC = ("ignore", "unignore")
API_RE = re.compile(r"""['"](/api/appalias/[A-Za-z0-9_/]*)""")


class BuildError(Exception):
    pass


def unescape(text):
    """Chaîne PHP/Volt entre guillemets : \\' \\" et \\\\ sont des échappements."""
    return re.sub(r"\\(['\"\\])", r"\1", text)


def js_text(text):
    """Contenu d'une chaîne JS entre guillemets simples ou doubles."""
    return json.dumps(text, ensure_ascii=False)[1:-1].replace("'", "\\'").replace("</", "<\\/")


def esc(text):
    return html.escape(text, quote=True)


# --- formulaires -------------------------------------------------------------
def model_options():
    """{chemin du champ: [(valeur, libellé)]} des OptionField du modèle."""
    root = ET.parse(MODEL).getroot()
    out = {}

    def walk(node, path):
        for child in node:
            sub = path + [child.tag]
            if child.get("type") == "OptionField":
                values = child.find("OptionValues")
                out[".".join(sub)] = [(v.tag, v.text or v.tag) for v in values] if values is not None else []
            elif child.get("type") is None or child.get("type") == "ArrayField":
                walk(child, sub)

    walk(root.find("items"), [])
    return out


def field_options(field_id, options):
    parts = field_id.split(".")
    key = ".".join(parts[1:]) if parts[0] == "appalias" else "apps." + field_id
    return options.get(key, [])


def field_row(field, options):
    """Ligne <tr> de form_input_tr.volt (colonnes : libellé | champ | message)."""
    fid = field.findtext("id") or ""
    ftype = field.findtext("type") or "text"
    label = field.findtext("label") or ""
    help_text = field.findtext("help") or ""
    style = field.findtext("style") or ""
    advanced = (field.findtext("advanced") or "") == "true"
    allownew = field.findtext("allownew") or "false"
    e_id, e_label = esc(fid), esc(label)
    out = ['<tr id="row_%s"%s>' % (e_id, ' data-advanced="true"' if advanced else "")]
    out.append('<td><div class="control-label" id="control_label_%s">' % e_id)
    if help_text:
        out.append('<a id="help_for_%s" href="#" class="showhelp"><i class="fa fa-info-circle fa-fw"></i></a>' % e_id)
    else:
        out.append('<i class="fa fa-info-circle fa-fw text-muted"></i>')
    out.append("<b>%s</b>" % e_label)
    if advanced:
        out.append(' <i class="fa fa-cog fa-sm fa-fw text-warning"></i>')
    out.append("</div></td><td>")
    if ftype == "text":
        out.append('<input type="text" aria-label="%s" class="form-control %s" size="50" id="%s">' % (
            e_label, esc(style), e_id))
    elif ftype == "checkbox":
        out.append('<input type="checkbox" class="%s" id="%s" aria-label="%s">' % (esc(style), e_id, e_label))
    elif ftype in ("dropdown", "select_multiple"):
        multiple = ftype == "select_multiple"
        opts = "".join('<option value="%s">%s</option>' % (esc(v), esc(t)) for v, t in field_options(fid, options))
        out.append('<div id="select_%s"><select aria-label="%s" %s data-size="10" id="%s" class="%s" '
                   'data-allownew="%s" data-width="346px">%s</select>' % (
                       e_id, e_label, 'multiple="multiple"' if multiple else "", e_id,
                       esc(style or "selectpicker"), esc(allownew), opts))
        if multiple:
            out.append('<a href="#" class="text-danger" id="clear-options_%s"><i class="fa fa-times-circle"></i> '
                       '<small>Clear All</small></a>' % e_id)
            if "tokenize" in style.split():
                out.append('&nbsp;&nbsp;<a href="#" class="text-danger" id="copy-options_%s"><i class="fa fa-copy">'
                           '</i> <small>Copy</small></a>' % e_id)
                if allownew != "false":
                    out.append('&nbsp;&nbsp;<a href="#" class="text-danger" id="to-text_%s"><i class="fa '
                               'fa-file-text-o"></i> <small>Text</small></a>' % e_id)
        out.append("</div>")
    elif ftype == "textbox":
        out.append('<textarea class="%s" rows="5" id="%s" aria-label="%s"></textarea>' % (esc(style), e_id, e_label))
    else:
        out.append('<span class="%s" id="%s"></span>' % (esc(style), e_id))
    if help_text:
        out.append('<div class="hidden" data-for="help_for_%s"><small>%s</small></div>' % (e_id, esc(help_text)))
    out.append('</td><td><span class="help-block" id="help_block_%s"></span></td></tr>' % e_id)
    return "".join(out)


def form_table(form_file, options, form_id, dialog):
    """Table des champs ; ligne « advanced mode / full help » comme base_form (pas dans la modale)."""
    root = ET.parse(os.path.join(FORMS, form_file)).getroot()
    fields = root.findall("field")
    has_adv = any((f.findtext("advanced") or "") == "true" for f in fields)
    has_help = any(f.findtext("help") for f in fields)
    rows = []
    if has_adv or has_help:
        left = ('<a href="#"><i class="fa fa-toggle-off fa-fw text-danger" id="show_advanced_%s"></i></a> '
                '<small>advanced mode</small> <i class="fa fa-cog fa-sm fa-fw text-warning"></i>' % esc(form_id)
                if has_adv else "")
        right = ('<i class="fa fa-info-circle fa-sm fa-fw text-primary"></i> <small>full help</small> '
                 '<a href="#"><i class="fa fa-toggle-off fa-fw text-danger" id="show_all_help_%s"></i></a>'
                 % esc(form_id) if has_help else "")
        rows.append('<tr><td colspan="3"><span>%s</span><span class="pull-right">%s</span></td></tr>' % (left, right))
    for field in fields:
        rows.append(field_row(field, options))
    return ('<div class="table-responsive"><table class="table table-striped table-condensed" '
            'style="table-layout: fixed; width: 100%%;"><colgroup><col style="width: 25%%;" />'
            '<col style="width: 40%%;" /><col style="width: 35%%;" /></colgroup><tbody>%s</tbody></table></div>'
            % "".join(rows))


def partial_args(text):
    out = {}
    for m in ARG_RE.finditer(text):
        value = next((g for g in m.groups()[1:] if g is not None), "")
        out[m.group(1)] = unescape(value)
    return out


def render_partial(match, options):
    name = match.group(1)
    args = partial_args(match.group(2))
    form = FORM_VARS.get(args.get("fields", ""))
    if form is None:
        raise BuildError("unknown form variable in partial: %s" % args.get("fields"))
    form_id = args.get("id", "")
    if name == "layout_partials/base_form":
        return '<form id="%s" class="form-inline" data-title="">%s</form>' % (
            esc(form_id), form_table(form, options, form_id, False))
    if name == "layout_partials/base_dialog":
        return (
            '<div class="modal fade" id="%(id)s" tabindex="-1" role="dialog" aria-labelledby="%(id)sLabel">'
            '<div class="modal-dialog modal-lg"><div class="modal-content"><div class="modal-header">'
            '<button type="button" class="close" data-dismiss="modal" aria-label="Close">'
            '<span aria-hidden="true">&times;</span></button>'
            '<div class="input-group form-search pull-right" style="width: 250px; margin-top: -6px; margin-right: 15px;">'
            '<span class="input-group-addon"><i class="fa fa-search" aria-hidden="true"></i></span>'
            '<input type="search" class="form-control" placeholder="Search form" aria-label="Search form"></div>'
            '<h4 class="modal-title" id="%(id)sLabel">%(label)s</h4></div>'
            '<div class="modal-body"><form id="frm_%(id)s">%(table)s</form></div>'
            '<div class="modal-footer"><button type="button" class="btn btn-default" data-dismiss="modal">Cancel'
            '</button><button type="button" class="btn btn-primary" id="btn_%(id)s_save">Save '
            '<i id="btn_%(id)s_save_progress" class=""></i></button></div></div></div></div>'
            % {"id": esc(form_id), "label": esc(args.get("label", "")),
               "table": form_table(form, options, form_id, True)})
    raise BuildError("unknown partial: %s" % name)


# --- page --------------------------------------------------------------------
def lang_sub(match):
    text = unescape(match.group(1) if match.group(1) is not None else match.group(2))
    if match.group(3) == "escape_js":
        return js_text(text)
    return esc(text)


def translate(volt):
    options = model_options()
    text = COMMENT_RE.sub("", volt)
    text = PARTIAL_RE.sub(lambda m: render_partial(m, options), text)
    text = LANG_RE.sub(lang_sub, text)
    left = re.findall(r"\{\{.*?\}\}|\{%.*?%\}", text, re.S)
    for item in left:
        print("warning: Volt syntax neutralised: %s" % item[:80].replace("\n", " "), file=sys.stderr)
    return re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", text, flags=re.S)


def endpoint_key(url):
    """/api/appalias/service/status -> service_status (fichier de fixture)."""
    parts = [p for p in url[len("/api/appalias/"):].split("/") if p]
    return "_".join(parts[:2])


def volt_endpoints(volt):
    found = {}
    for m in API_RE.finditer(volt):
        key = endpoint_key(m.group(1))
        if "_" in key:   # « /api/appalias/discovery/ » + variable : voir DYNAMIC
            found.setdefault(key, m.group(1))
    for name in DYNAMIC:
        if "'/api/appalias/discovery/' + " in volt:
            found.setdefault("discovery_" + name, "/api/appalias/discovery/" + name)
    return found


def read_dir(path):
    out = {}
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        if name.endswith(".json") and os.path.isfile(full):
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    out[name[:-5]] = json.load(fh)
            except ValueError as exc:
                raise BuildError("%s: invalid JSON (%s)" % (full, exc))
    return out


def load_fixtures():
    base = read_dir(FIXTURES) if os.path.isdir(FIXTURES) else {}
    states = {}
    if os.path.isdir(FIXTURES):
        for name in sorted(os.listdir(FIXTURES)):
            if os.path.isdir(os.path.join(FIXTURES, name)):
                states[name] = read_dir(os.path.join(FIXTURES, name))
    return base, states


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>os-appalias - UI preview</title>
@@CSS@@
<link rel="stylesheet" href="opnsense-lite.css">
@@JS@@
<script src="fixtures.js"></script>
<script src="mock.js"></script>
</head>
<body>
<header class="opn-topbar">
    <span class="opn-brand">OPNsense<small>preview</small></span>
    <span class="text-muted">Services &gt; AppAlias</span>
    <span class="opn-states" id="opn-states">@@STATES@@</span>
</header>
<div class="page-content-head">
    <div class="container-fluid">
        <ul class="breadcrumb"><li>Services</li><li>AppAlias</li></ul>
        <div class="flex-head" style="display: flex; align-items: center;">
            <h1>Services: AppAlias</h1>
            <div id="service_status_container" style="margin-left: auto"></div>
        </div>
    </div>
</div>
<main class="page-content-main">
    <div class="container-fluid">
        <div class="row">
            <section class="col-xs-12">
@@BODY@@
            </section>
        </div>
    </div>
</main>
</body>
</html>
"""


def build(out_dir=DEFAULT_OUT):
    with open(VOLT, "r", encoding="utf-8") as fh:
        volt = fh.read()
    body = translate(volt)
    base, states = load_fixtures()

    endpoints = volt_endpoints(volt)
    print("endpoints found in index.volt (%d):" % len(endpoints))
    for key, url in sorted(endpoints.items()):
        status = "ok" if key in base else "NO FIXTURE (console warning, answer {})"
        print("  %-28s %-40s %s" % (key, url, status))

    state_links = ['<a href="?">default</a>'] + ['<a href="?state=%s">%s</a>' % (s, s) for s in states]
    page = (PAGE.replace("@@CSS@@", "\n".join('<link rel="stylesheet" href="%s">' % u for u in CDN["css"]))
            .replace("@@JS@@", "\n".join('<script src="%s"></script>' % u for u in CDN["js"]))
            .replace("@@STATES@@", " ".join(state_links))
            .replace("@@BODY@@", body))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    data = {"base": base, "states": states}
    with open(os.path.join(out_dir, "fixtures.js"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("window.APPALIAS_FIXTURES = %s;\n" % json.dumps(data, indent=1, sort_keys=True))
    with open(os.path.join(out_dir, "mobile.html"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(MOBILE)
    for name in COPIED:
        shutil.copyfile(os.path.join(HERE, name), os.path.join(out_dir, name))
    return os.path.join(out_dir, "index.html")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the os-appalias UI preview page.")
    parser.add_argument("--out", default=DEFAULT_OUT, help="output directory (default: tools/ui-preview/out)")
    args = parser.parse_args(argv)
    try:
        path = build(args.out)
    except (BuildError, OSError, ET.ParseError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print(path)
    print("serve: python -m http.server 8765 -d %s" % os.path.dirname(path))
    print("open:  http://localhost:8765/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
