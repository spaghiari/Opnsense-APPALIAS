"""Contrôles statiques du plugin : textes source en anglais (ASCII), XML bien
formés, champs du modèle 0.3.0 (plugin 0.4), actions configd du catalogue."""

import configparser
import glob
import os
import re
import unittest
import xml.etree.ElementTree as ET

import appalias_testutil  # noqa: F401
from appalias_testutil import ROOT

PLUGIN = os.path.join(ROOT, "net", "appalias")
SRC = os.path.join(PLUGIN, "src")
MVC = os.path.join(SRC, "opnsense", "mvc", "app")
MODEL_DIR = os.path.join(MVC, "models", "OPNsense", "AppAlias")
FORMS = os.path.join(MVC, "controllers", "OPNsense", "AppAlias", "forms")
ACTIONS = os.path.join(SRC, "opnsense", "service", "conf", "actions.d", "actions_appalias.conf")

CALL_RE = re.compile(r"(?:gettext|lang\._)\(\s*('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")", re.S)


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def source_files():
    out = []
    for pattern in ("**/*.php", "**/*.volt", "**/*.inc"):
        out.extend(glob.glob(os.path.join(SRC, pattern), recursive=True))
    return sorted(out)


class TextTests(unittest.TestCase):
    def test_gettext_strings_ascii(self):
        files = source_files()
        self.assertTrue(files)
        found = 0
        for path in files:
            for match in CALL_RE.finditer(read(path)):
                found += 1
                self.assertTrue(match.group(1).isascii(), "%s : %s" % (path, match.group(1)))
        self.assertGreater(found, 50)

    def test_xml_texts_ascii(self):
        paths = glob.glob(os.path.join(FORMS, "*.xml")) + [
            os.path.join(MODEL_DIR, "AppAlias.xml"),
            os.path.join(MODEL_DIR, "Menu", "Menu.xml"),
            os.path.join(MODEL_DIR, "ACL", "ACL.xml"),
        ]
        for path in paths:
            root = ET.parse(path).getroot()
            for node in root.iter():
                if node.tag in ("label", "help", "ValidationMessage", "description", "name") or \
                        (node.text and node.tag in ("error", "warning", "info", "debug", "manual",
                                                    "catalog", "v2fly", "both", "inet", "inet6",
                                                    "dns", "dot")):
                    self.assertTrue((node.text or "").isascii(), "%s : <%s>" % (path, node.tag))
                for key, value in node.attrib.items():
                    self.assertTrue(value.isascii(), "%s : %s=%s" % (path, key, value))

    def test_pkg_descr_english(self):
        self.assertTrue(read(os.path.join(PLUGIN, "pkg-descr")).isascii())


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.root = ET.parse(os.path.join(MODEL_DIR, "AppAlias.xml")).getroot()

    def test_version_and_fields(self):
        self.assertEqual(self.root.findtext("version"), "0.3.0")
        proto = self.root.find("items/general/upstream_protocol")
        self.assertEqual((proto.get("type"), proto.findtext("Default")), ("OptionField", "dns"))
        self.assertEqual(sorted(c.tag for c in proto.find("OptionValues")), ["dns", "dot"])
        name = self.root.find("items/general/upstream_tls_name")
        self.assertEqual((name.get("type"), name.findtext("Required")), ("TextField", "N"))
        mask = re.compile(name.findtext("Mask")[1:-1])
        for ok in ("", "dns.quad9.net", "dns.quad9.net.", "DOT-1.example"):
            self.assertIsNotNone(mask.match(ok), ok)
        for bad in ("-a.example", "a..b", "x y", "dns.quad9.net#x"):
            self.assertIsNone(mask.match(bad), bad)
        app = self.root.find("items/apps/app")
        for name in ("catalog_refs", "catalog_rules", "catalog_revision"):
            field = app.find(name)
            self.assertIsNotNone(field, name)
            self.assertEqual(field.findtext("Required"), "N")
        self.assertIsNotNone(self.root.find("items/general/shared_platforms_extra"))
        auto = self.root.find("items/general/unbound_autoforward")
        self.assertEqual((auto.get("type"), auto.findtext("Default")), ("BooleanField", "0"))   # opt-in
        origins = app.find("origin/OptionValues")
        self.assertEqual(origins.findtext("v2fly"), "v2fly domain-list-community")
        self.assertIsNotNone(origins.find("catalog"))

    def test_origin_not_in_dialog(self):
        root = ET.parse(os.path.join(FORMS, "dialogApp.xml")).getroot()
        ids = [f.findtext("id") for f in root.findall("field")]
        self.assertNotIn("app.origin", ids)
        self.assertIn("app.domains", ids)
        for hidden in ("app.catalog_refs", "app.catalog_rules", "app.catalog_revision"):
            self.assertNotIn(hidden, ids)

    def test_export_catalog_refs(self):
        text = read(os.path.join(SRC, "opnsense", "scripts", "OPNsense", "AppAlias", "export_config.php"))
        self.assertIn("'catalog_refs' =>", text)
        self.assertIn("'shared_platforms_extra' =>", text)
        self.assertIn("'upstream_protocol' =>", text)
        self.assertIn("'upstream_tls_name' =>", text)
        # « Use System Nameservers » d'Unbound (modèle Unbound du core, stable/26.7)
        self.assertIn("$ub->forwarding->enabled", text)

    def test_settings_form_dot_fields(self):
        root = ET.parse(os.path.join(FORMS, "general.xml")).getroot()
        ids = [f.findtext("id") for f in root.findall("field")]
        for name in ("upstream_protocol", "upstream_tls_name"):
            self.assertIn("appalias.general." + name, ids)
        helps = " ".join(f.findtext("help") or "" for f in root.findall("field"))
        self.assertIn("dns.quad9.net", helps)
        self.assertIn("853", helps)

    def test_volt_diff_refused_is_warning(self):
        volt = read(os.path.join(MVC, "views", "OPNsense", "AppAlias", "index.volt"))
        self.assertIn('<div id="catalog-diff-warning" class="alert alert-warning"', volt)
        self.assertIn("$('#catalog-diff-warning').text(T.diffRefused", volt)
        self.assertNotIn("$('#catalog-diff-error').text(T.diffRefused", volt)

    def test_volt_dot_port_prefill(self):
        volt = read(os.path.join(MVC, "views", "OPNsense", "AppAlias", "index.volt"))
        self.assertIn("lang._('Port set to 853 for DNS over TLS')|escape_js", volt)
        self.assertIn("$upNote.text(T.upPort853)", volt)
        for name in ("dns.quad9.net", "cloudflare-dns.com", "dns.google", "2620:fe::fe",
                     "2606:4700:4700::1001", "2001:4860:4860::8844"):
            self.assertIn(name, volt)

    def test_plugin_version(self):
        self.assertRegex(read(os.path.join(PLUGIN, "Makefile")), r"PLUGIN_VERSION=\s+0.4.2\n")
        self.assertIn("\n0.4.2\n", read(os.path.join(PLUGIN, "pkg-descr")))


class ActionTests(unittest.TestCase):
    def test_catalog_actions(self):
        cp = configparser.ConfigParser(delimiters=(":",), interpolation=None)
        cp.read_string(read(ACTIONS))
        expected = {"catalog_info": "info", "catalog_search": "search",
                    "catalog_preview": "preview", "catalog_resolve": "resolve",
                    "catalog_popular": "popular"}
        for section, sub in expected.items():
            self.assertIn(section, cp.sections())
            self.assertEqual(cp[section]["type"], "script_output")
            self.assertEqual(cp[section]["command"],
                             "/usr/local/opnsense/scripts/OPNsense/AppAlias/catalog.py " + sub)
            self.assertEqual(cp[section]["parameters"], "" if sub in ("info", "popular") else "%s")

    def test_unbound_actions(self):
        cp = configparser.ConfigParser(delimiters=(":",), interpolation=None)
        cp.read_string(read(ACTIONS))
        script = "/usr/local/opnsense/scripts/OPNsense/AppAlias/unbound_forward.py"
        self.assertEqual((cp["unbound"]["command"], cp["unbound"]["type"]), (script + " --apply", "script"))
        self.assertEqual((cp["unbound_write"]["command"], cp["unbound_write"]["type"]), (script, "script"))
        self.assertEqual(cp["unbound_status"]["command"], script + " --status")
        inc = read(os.path.join(SRC, "etc", "inc", "plugins.inc.d", "appalias.inc"))
        self.assertNotIn("POST_INSTALL", inc)
        self.assertNotIn("--apply", inc)          # jamais de redémarrage au boot ni sur événement
        self.assertIn("'unbound_start' => ['appalias_configure_unbound']", inc)
        self.assertNotIn("unbound-control", read(os.path.join(PLUGIN, "+PRE_DEINSTALL.post")))
        volt = read(os.path.join(MVC, "views", "OPNsense", "AppAlias", "index.volt"))
        script_part = volt[volt.index("<script>"):volt.index("</script>")]
        self.assertNotRegex(script_part, r"\{\{ lang\._\('[^']*'\) \}\}")
        ctrl = read(os.path.join(MVC, "controllers", "OPNsense", "AppAlias", "Api", "ServiceController.php"))
        self.assertIn("foreach (['export', 'sync', 'unbound'] as $step)", ctrl)
        self.assertTrue(os.path.isfile(os.path.join(PLUGIN, "+PRE_DEINSTALL.post")))

    def test_packaged_files_under_src(self):
        # plugins.mk empaquette tout src/ : le catalogue et data/ doivent y être.
        scripts = os.path.join(SRC, "opnsense", "scripts", "OPNsense", "AppAlias")
        for rel in ("catalog/v2fly.lst", "catalog/v2fly.manifest.json", "catalog/LICENSE.v2fly",
                    "catalog/popular.json", "../../../service/templates/OPNsense/Syslog/local/appalias.conf",
                    "data/shared_platforms.txt", "catalog.py", "lib/v2fly.py", "lib/shared.py"):
            self.assertTrue(os.path.isfile(os.path.join(scripts, rel)), rel)
        self.assertFalse(glob.glob(os.path.join(SRC, "**", "v2fly-snapshot.py"), recursive=True))


if __name__ == "__main__":
    unittest.main()
