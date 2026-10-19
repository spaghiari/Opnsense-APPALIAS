"""Contrôles statiques du plugin : textes source en anglais (ASCII), XML bien
formés, champs du modèle 0.6.0 (plugin 0.6), actions configd du catalogue."""

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
        self.assertEqual(self.root.findtext("version"), "0.6.0")
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
        self.assertRegex(read(os.path.join(PLUGIN, "Makefile")), r"PLUGIN_VERSION=\s+0\.8\n")
        self.assertIn("\n0.8\n", read(os.path.join(PLUGIN, "pkg-descr")))
        from lib.version import VERSION
        self.assertEqual(VERSION, "0.8")     # version écrite par le démon = paquet

    def test_option_defaults_are_options(self):
        """Le défaut de chaque OptionField fait partie de ses OptionValues."""
        count = 0
        for node in self.root.iter():
            if node.get("type") != "OptionField":
                continue
            count += 1
            default = node.findtext("Default")
            options = [c.tag for c in node.find("OptionValues")]
            if default is not None:
                self.assertIn(default, options, node.tag)
        self.assertGreaterEqual(count, 5)
        origin = self.root.find("items/apps/app/origin")
        self.assertEqual(origin.findtext("Default"), "manual")

    def test_online_fields_and_no_discovery(self):
        gen = self.root.find("items/general")
        expected = {"catalog_online_update": ("BooleanField", "1"),
                    "catalog_update_schedule": ("OptionField", "hourly")}
        for name, (kind, default) in expected.items():
            node = gen.find(name)
            self.assertEqual((node.get("type"), node.findtext("Default")), (kind, default), name)
        schedule = [c.tag for c in gen.find("catalog_update_schedule/OptionValues")]
        self.assertEqual(schedule, ["manual", "hourly", "daily"])
        form = read(os.path.join(SRC, "opnsense", "mvc", "app", "controllers", "OPNsense", "AppAlias",
                                 "forms", "general.xml"))
        export_src = read(os.path.join(SRC, "opnsense", "scripts", "OPNsense", "AppAlias", "export_config.php"))
        help_text = re.search(r"<id>appalias\.general\.catalog_update_schedule</id>.*?<help>(.*?)</help>",
                              form, re.S).group(1)
        help_text.encode("ascii")            # aide en ASCII
        self.assertIn("every hour", help_text)
        for name in ("discovery_enabled", "discovery_retention_days", "discovery_max_names",
                     "discovery_window", "discovery_ignored"):
            self.assertIsNone(gen.find(name), name)          # fonction retirée en 0.8
            self.assertNotIn("<id>appalias.general.%s</id>" % name, form)
            self.assertNotIn("'%s'" % name, export_src)
        self.assertIn("'catalog_online_update' =>", export_src)

    def test_proactive_fields(self):
        gen = self.root.find("items/general")
        self.assertEqual((gen.find("proactive_enabled").get("type"), gen.findtext("proactive_enabled/Default")),
                         ("BooleanField", "1"))
        for name, default, lo, hi in (("proactive_interval_min", "300", "60", "86400"),
                                      ("proactive_interval_max", "3600", "300", "86400")):
            node = gen.find(name)
            self.assertEqual((node.get("type"), node.findtext("Default"), node.findtext("MinimumValue"),
                              node.findtext("MaximumValue")), ("IntegerField", default, lo, hi))
        prefixes = gen.find("proactive_prefixes")
        self.assertEqual((prefixes.get("type"), prefixes.findtext("Default")), ("CSVListField", "www"))
        app = self.root.find("items/apps/app/proactive")
        self.assertEqual((app.get("type"), app.findtext("Default"), app.findtext("Required")),
                         ("BooleanField", "1", "Y"))
        export = read(os.path.join(SRC, "opnsense", "scripts", "OPNsense", "AppAlias", "export_config.php"))
        for name in ("proactive_enabled", "proactive_interval_min", "proactive_interval_max",
                     "proactive_prefixes", "proactive"):
            self.assertIn("'%s' =>" % name, export)
        self.assertIn("AppAlias::splitList($gen->proactive_prefixes)", export)
        text = read(os.path.join(MODEL_DIR, "Migrations", "M0_6_0.php"))
        self.assertIn("class M0_6_0 extends BaseModelMigration", text)
        self.assertIn("parent::run($model);", text)
        form = ET.parse(os.path.join(FORMS, "general.xml")).getroot()
        ids = [f.findtext("id") for f in form.findall("field")]
        for name in ("proactive_enabled", "proactive_interval_min", "proactive_interval_max", "proactive_prefixes"):
            self.assertIn("appalias.general." + name, ids)
        form = ET.parse(os.path.join(FORMS, "dialogApp.xml")).getroot()
        fields = {f.findtext("id"): f for f in form.findall("field")}
        self.assertEqual(fields["app.proactive"].findtext("type"), "checkbox")
        self.assertEqual(fields["app.proactive"].findtext("label"), "Proactive resolution")
        php = read(os.path.join(MODEL_DIR, "AppAlias.php"))
        self.assertIn("The minimum interval must not exceed the maximum interval.", php)
        self.assertIn("must stay below minimum TTL + TTL margin", php)

    def test_catalog_follow_fields(self):
        app = self.root.find("items/apps/app")
        follow = app.find("catalog_follow")
        self.assertEqual((follow.get("type"), follow.findtext("Default"), follow.findtext("Required")),
                         ("BooleanField", "0", "Y"))       # manuel = 0 ; import et migration = 1
        declined = app.find("catalog_declined")
        self.assertEqual((declined.get("type"), declined.findtext("Required")), ("CSVListField", "N"))
        text = read(os.path.join(MODEL_DIR, "Migrations", "M0_5_0.php"))
        self.assertIn("namespace OPNsense\\AppAlias\\Migrations;", text)
        self.assertIn("class M0_5_0 extends BaseModelMigration", text)
        self.assertIn("parent::run($model);", text)
        self.assertIn("catalog_declined->setValue", text)
        form = ET.parse(os.path.join(FORMS, "dialogApp.xml")).getroot()
        fields = {f.findtext("id"): f for f in form.findall("field")}
        self.assertEqual(fields["app.catalog_follow"].findtext("type"), "checkbox")
        self.assertEqual(fields["app.catalog_follow"].findtext("label"), "Follow catalog automatically")
        self.assertNotIn("app.catalog_declined", fields)
        ctrl = read(os.path.join(MVC, "controllers", "OPNsense", "AppAlias", "Api", "CatalogController.php"))
        self.assertIn("public function undoFollowAction()", ctrl)
        self.assertIn("public function followLogAction()", ctrl)
        self.assertEqual(ctrl.count("'catalog_follow' => '1'"), 1)     # import : nouvelle app
        self.assertIn("if (!$hadRefs) {", ctrl)                         # fusion : seulement si aucune liste liée
        self.assertIn("$values['catalog_follow'] = '1';", ctrl)
        settings = read(os.path.join(MVC, "controllers", "OPNsense", "AppAlias", "Api", "SettingsController.php"))
        self.assertIn("'catalog_follow'", settings)
        script = read(os.path.join(SRC, "opnsense", "scripts", "OPNsense", "AppAlias", "catalog_follow.php"))
        for needle in ("$cfg->lock();", "$cfg->unlock();", "serializeToConfig()", "$cfg->save();",
                       "export_config.php", "unbound_forward.py', '--apply'", "onereload",
                       "LOG_INFO"):
            self.assertIn(needle, script)
        # le suivi n'appelle plus la synchronisation des alias (le nom n'apparaît plus qu'en commentaire)
        for form in ("/sync_aliases.php'", "/sync_aliases.php\"", "'sync' =>"):
            self.assertNotIn(form, script)
        # le verrou de config.xml est rendu avant l'enchaînement de l'application
        self.assertLess(script.index("$cfg->unlock();"), script.index("$failed = appalias_apply();"))

    def test_follow_enabled_checked_before_apply(self):
        script = read(os.path.join(SRC, "opnsense", "scripts", "OPNsense", "AppAlias", "catalog_follow.php"))
        for fn in ("function appalias_run()", "function appalias_undo($entryId)"):
            body = script[script.index(fn):]
            nxt = body.find(chr(10) + "function ", 10)
            body = body if nxt < 0 else body[:nxt]
            self.assertIn("appalias_enabled()", body)
            self.assertIn("appalias_apply()", body)
            self.assertLess(body.index("appalias_enabled()"), body.index("appalias_apply()"))

    def test_hourly_schedule(self):
        inc = read(os.path.join(SRC, "etc", "inc", "plugins.inc.d", "appalias.inc"))
        self.assertIn("'hourly'", inc)
        xml = read(os.path.join(MODEL_DIR, "AppAlias.xml"))
        self.assertIn("<hourly>Hourly</hourly>", xml)
        self.assertIn("<Default>hourly</Default>", xml)
        mig = read(os.path.join(MODEL_DIR, "Migrations", "M0_6_0.php"))
        self.assertIn("=== 'daily'", mig)
        self.assertIn("setValue('hourly')", mig)
        self.assertNotIn("'manual'", mig)   # manual n'est jamais réécrit

    def test_diff_projected_capped(self):
        ctrl = read(os.path.join(MVC, "controllers", "OPNsense", "AppAlias", "Api", "CatalogController.php"))
        body = ctrl[ctrl.index("public function diffAction()"):ctrl.index("public function applyDiffAction()")]
        self.assertIn("$item['projected']", body)
        self.assertIn("$item['capped']", body)
        self.assertIn("followAdds(", body)
        self.assertIn("catalog_declined", body)

    def test_volt_follow_log(self):
        volt = read(os.path.join(MVC, "views", "OPNsense", "AppAlias", "index.volt"))
        for needle in ("'/api/appalias/catalog/followLog'", "'/api/appalias/catalog/undoFollow'",
                       'id="catalog-follow-log"', "onBeforeRenderDialog",
                       "row_app.catalog_follow", "lang._('Automatic updates')"):
            self.assertIn(needle, volt)
        self.assertNotIn('id="discovery-covered"', volt)
        self.assertNotIn("Already covered", volt)
        style = volt[volt.index("<style>"):volt.index("</style>")]
        self.assertGreater(len(set(re.findall(r"\.(appalias-[a-z0-9-]+)", style))), 15)
        # chaque sélecteur CSS porte une classe appalias- : rien de Bootstrap n'est redéfini globalement
        for line in style.splitlines():
            line = line.strip()
            if line.endswith("{") and not line.startswith("@media"):
                for selector in line[:-1].split(","):
                    if selector.strip():
                        self.assertIn("appalias-", selector, line)

    def test_cron_and_post_install(self):
        inc = read(os.path.join(SRC, "etc", "inc", "plugins.inc.d", "appalias.inc"))
        self.assertIn("function appalias_cron()", inc)
        self.assertIn("$jobs[]['autocron']", inc)
        self.assertIn("crc32((string)gethostname())", inc)
        post = read(os.path.join(PLUGIN, "+POST_INSTALL.post"))
        self.assertIn("/usr/sbin/daemon -f /usr/local/bin/python3 /usr/local/opnsense/scripts/OPNsense/AppAlias/catalog_update.py "
                      "update --if-enabled", post)
        self.assertIn("/usr/sbin/daemon -f /usr/local/opnsense/scripts/OPNsense/AppAlias/catalog_follow.php run", post)
        rc = read(os.path.join(SRC, "etc", "rc.d", "appaliasd"))
        self.assertIn('extra_commands="reload"', rc)
        self.assertNotIn("USR1", rc)
        # données de l'ancienne fonction Discovery : supprimées APRÈS le redémarrage du démon
        rm_line = "rm -f /var/db/appalias/discovery.json"
        self.assertIn(rm_line, post)
        self.assertGreater(post.index(rm_line), post.index("onerestart"))
        self.assertFalse(os.path.exists(os.path.join(ROOT, ".github", "workflows", "v2fly-refresh.yml")))

    def test_volt_relay_badge_and_no_discovery(self):
        volt = read(os.path.join(MVC, "views", "OPNsense", "AppAlias", "index.volt"))
        self.assertIn('<div id="unbound-root-warning" class="alert alert-info"', volt)
        self.assertIn("relayRunning === false ? 'alert-danger' : 'alert-info'", volt)
        self.assertNotIn('href="#discovery"', volt)
        self.assertNotIn("/api/appalias/discovery/", volt)
        self.assertIn("'/api/appalias/catalog/update'", volt)
        self.assertIn("'/api/appalias/catalog/reset'", volt)
        script = volt[volt.index("<script>"):volt.index("</script>")]
        self.assertNotIn(".html(", script)          # tout en .text()


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
        base = "/usr/local/opnsense/scripts/OPNsense/AppAlias/"
        for gone in ("discovery_list", "discovery_ctl"):
            self.assertNotIn(gone, cp.sections())
        self.assertEqual(cp["catalog_update"]["command"], base + "catalog_update.py update")
        self.assertEqual(cp["catalog_reset"]["command"], base + "catalog_update.py reset")
        self.assertEqual((cp["catalog_follow"]["command"], cp["catalog_follow"]["type"]),
                         (base + "catalog_follow.php run", "script_output"))
        self.assertEqual((cp["catalog_follow_undo"]["command"], cp["catalog_follow_undo"]["parameters"],
                          cp["catalog_follow_undo"]["type"]),
                         (base + "catalog_follow.php undo", "%s", "script_output"))
        self.assertEqual(cp["catalog_follow_log"]["command"], base + "catalog_follow.py log")

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
        # fonction Discovery retirée en 0.8
        for rel in ("discovery.py", "lib/discovery.py", "lib/score.py"):
            self.assertFalse(os.path.exists(os.path.join(scripts, rel)), rel)
        self.assertFalse(os.path.exists(os.path.join(
            MVC, "controllers", "OPNsense", "AppAlias", "Api", "DiscoveryController.php")))


if __name__ == "__main__":
    unittest.main()
