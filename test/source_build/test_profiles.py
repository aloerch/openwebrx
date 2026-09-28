"""Offline profile, installation, launcher and service regression tests.

No network access, native SDR build, system package installation, or real
systemd changes. Launcher tests execute a captured runtime in a fake prefix.
"""
import configparser
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy = load("linux_deploy_tests", "build/linux_deploy.py")
runtime = load("profile_runtime_tests", "build/source_runtime.py")


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("OWRX_")}
        self.env["HOME"] = str(self.home)

    def run_cli(self, *args, success=True, env=None):
        result = subprocess.run(["bash", str(ROOT / "build-linux.sh"), *args], env=env or self.env,
                                text=True, capture_output=True, timeout=15)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def shell(self, body, success=True):
        result = subprocess.run(["bash", "-c", 'source "$1/build-linux.sh"\n' + body, "tests", str(ROOT)],
                                env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_default_is_dev_and_settings_are_read_only(self):
        result = self.run_cli("settings")
        self.assertIn("Mode: dev", result.stdout)
        self.assertIn("openwebrx-dev", result.stdout)
        self.assertIn("127.0.0.1:8073", result.stdout)
        self.assertIn("127.0.0.1:8074", result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_prod_defaults(self):
        result = self.run_cli("settings", "prod")
        for value in ("openwebrx-prod", ".build-linux-prod", "0.0.0.0:8072", "127.0.0.1:8075"):
            self.assertIn(value, result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_mode_before_command_and_dependency_profile(self):
        result = self.run_cli("--profile", "core", "prod", "settings")
        self.assertIn("Mode: prod", result.stdout)

    def test_env_mode_and_explicit_mode_override(self):
        env = dict(self.env, OWRX_MODE="prod")
        self.assertIn("Mode: prod", self.run_cli("settings", env=env).stdout)
        self.assertIn("Mode: dev", self.run_cli("settings", "dev", env=env).stdout)

    def test_legacy_dev_prefix_is_preserved(self):
        legacy = self.home / ".local/openwebrx-source"
        legacy.mkdir(parents=True)
        self.assertIn(str(legacy), self.run_cli("settings", "dev").stdout)
        self.assertNotIn(str(legacy), self.run_cli("settings", "prod").stdout)

    def test_custom_prefix_and_ports(self):
        prefix = self.home / "prefix with spaces"
        result = self.run_cli("settings", "prod", "--prefix", str(prefix), "--port", "9000", "--bind-address", "192.0.2.5")
        self.assertIn(str(prefix / "etc/openwebrx/openwebrx.conf"), result.stdout)
        self.assertIn("192.0.2.5:9000", result.stdout)

    def test_invalid_arguments_do_not_build(self):
        for args in (("build", "stage"), ("build", "dev", "prod"), ("build", "run"),
                     ("--prefix",), ("--port", "--force"), ("settings", "--port", "70000"),
                     ("settings", "--port", "0"), ("settings", "--port", "abc"),
                     ("settings", "--port", "8074"), ("settings", "--port", "08074"),
                     ("settings", "--bind-address", "bad-host"), ("settings", "--profile", "prod"),
                     ("settings", "--linger"), ("settings", "--", "--version")):
            with self.subTest(args=args):
                self.run_cli(*args, success=False)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_dangerous_paths_rejected(self):
        for value in ("/", str(self.home), str(ROOT), "/usr", str(self.home) + "\ninjected"):
            self.run_cli("settings", "--prefix", value, success=False)

    def test_nested_work_and_prefix_rejected(self):
        self.run_cli("settings", "--prefix", str(self.home / "build/prefix"), "--build-root", str(self.home / "build"), success=False)

    def test_production_not_installed_into_checkout(self):
        self.run_cli("settings", "prod", "--prefix", str(ROOT / "prod"), success=False)

    def test_cross_mode_prefix_and_build_cache_rejected(self):
        for flag in ("--prefix", "--build-root"):
            path = self.home / flag[2:]
            path.mkdir()
            (path / ".owrx-mode").write_text("dev\n")
            result = self.run_cli("settings", "prod", flag, str(path), success=False)
            self.assertIn("another mode", result.stderr)

    def test_install_selection_editable_only_for_dev(self):
        for mode in ("dev", "prod"):
            result = self.shell(f'''MODE={mode}
configure_build_mode
python(){{ printf '%s\n' "$@"; }}
deployment_install(){{ :; }}
install_app
''')
            self.assertEqual("--editable" in result.stdout, mode == "dev")
            self.assertIn("--no-build-isolation", result.stdout)

    def test_custom_prefix_derives_every_path(self):
        output = self.shell('''MODE=prod; PREFIX_OVERRIDE="$HOME/custom"
configure_build_mode
printf '%s\n' "$PREFIX" "$VENV" "$CONF" "$DATA" "$TMP" "$WHISPER_MODEL"
''').stdout
        self.assertEqual(len(output.splitlines()), 6)
        self.assertTrue(all(line.startswith(str(self.home / "custom")) for line in output.splitlines()))

    def test_old_unmarked_install_cannot_be_relabelled_prod(self):
        prefix = self.home / "legacy"
        (prefix / "venv").mkdir(parents=True)
        self.shell('''MODE=prod; PREFIX_OVERRIDE="$HOME/legacy"; WORK_OVERRIDE="$HOME/work"
configure_build_mode
prepare_build_mode
''', success=False)
        self.assertFalse((prefix / ".owrx-mode").exists())

    def test_clean_removes_only_requested_marked_cache(self):
        for mode in ("dev", "prod"):
            path = self.home / mode
            path.mkdir()
            (path / ".owrx-mode").write_text(mode + "\n")
        self.run_cli("clean", "prod", "--build-root", str(self.home / "prod"))
        self.assertTrue((self.home / "dev").exists())
        self.assertFalse((self.home / "prod").exists())

    def test_unmarked_directory_not_deleted(self):
        directory = self.home / "important"
        directory.mkdir()
        self.run_cli("clean", "--build-root", str(directory), success=False)
        self.assertTrue(directory.exists())

    def test_app_arguments_preserved(self):
        result = self.shell('''run_app(){ printf '<%s>\n' "${APP_ARGS[@]}"; }
build_linux_main run prod -- admin adduser "a user"
''')
        self.assertIn("<admin>\n<adduser>\n<a user>", result.stdout)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.prefix = self.base / "prod with spaces %value $cash"
        self.checkout = self.base / "checkout"
        (self.checkout / "build").mkdir(parents=True)
        shutil.copy2(ROOT / "build/linux_deploy.py", self.checkout / "build/linux_deploy.py")
        # A captured runtime proves exactly which installed file, argv, env and
        # cwd the real generated shell/Python launcher chooses without SDR deps.
        (self.checkout / "build/source_runtime.py").write_text(
            'import json, os, sys\nprint(json.dumps({"argv": sys.argv, "cwd": os.getcwd(), "env": dict(os.environ)}))\n')
        venv = self.prefix / "venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin/python").symlink_to(sys.executable)
        app = venv / "bin/openwebrx"
        app.write_text('#!/bin/sh\nprintf "CLI"\nprintf " <%s>" "$@"\nprintf "\\n"\n')
        app.chmod(0o755)
        self.args = SimpleNamespace(mode="prod", root=str(self.checkout), prefix=str(self.prefix), venv=str(venv),
                                    config=str(self.prefix / "etc/openwebrx/openwebrx.conf"),
                                    data=str(self.prefix / "var/lib/openwebrx"), tmp=str(self.prefix / "var/tmp"),
                                    state=str(self.base / "state"), port=8072, bind="0.0.0.0", model_name="tiny",
                                    whisper_port=8075, jobs=2, bin_dir=str(self.base / "bin"))

    def do_install(self):
        deploy.install(self.args)
        return deploy.read_manifest(self.prefix / "share/openwebrx/runtime.json")

    def run_launcher(self, *args):
        env = dict(os.environ, PYTHONPATH="/fake/dev/python", PYTHONHOME="/fake/dev/home", LD_LIBRARY_PATH="/fake/dev/lib")
        result = subprocess.run([str(self.base / "bin/openwebrx"), *args], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_prod_listener_and_non_checkout_manifest(self):
        manifest = self.do_install()
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(self.args.config)
        self.assertEqual(cfg["web"]["bind_address"], "0.0.0.0")
        self.assertEqual(cfg.getint("web", "port"), 8072)
        self.assertFalse(cfg.getboolean("web", "ipv6"))
        self.assertNotIn(str(self.checkout), json.dumps(manifest))
        self.assertEqual(manifest["speech_url"], "http://127.0.0.1:8075/inference")

    def test_dev_listener_and_no_global_launcher(self):
        self.args.mode = "dev"; self.args.port = 8073; self.args.bind = "127.0.0.1"
        deploy.install(self.args)
        cfg = configparser.ConfigParser(interpolation=None); cfg.read(self.args.config)
        self.assertEqual(cfg.getint("web", "port"), 8073)
        self.assertEqual(cfg["web"]["bind_address"], "127.0.0.1")
        self.assertFalse((self.base / "bin/openwebrx").exists())

    def test_ipv6_listener(self):
        self.args.bind = "::1"
        self.do_install()
        cfg = configparser.ConfigParser(interpolation=None); cfg.read(self.args.config)
        self.assertTrue(cfg.getboolean("web", "ipv6"))

    def test_rebuild_preserves_exact_user_config(self):
        self.do_install()
        text = "# local custom settings\n[web]\nport = 9999\nbind_address = 192.0.2.2\n"
        Path(self.args.config).write_text(text)
        self.do_install()
        self.assertEqual(Path(self.args.config).read_text(), text)

    def test_existing_launcher_not_clobbered(self):
        (self.base / "bin").mkdir()
        entry = self.base / "bin/openwebrx"
        entry.write_text("unrelated launcher")
        self.do_install()
        self.assertEqual(entry.read_text(), "unrelated launcher")
        self.assertTrue((self.prefix / "bin/openwebrx").is_file())

    def test_installed_launcher_survives_checkout_removal_and_cleans_environment(self):
        self.do_install()
        shutil.rmtree(self.checkout)
        captured = json.loads(self.run_launcher("--debug"))
        self.assertEqual(captured["cwd"], str(self.prefix))
        self.assertEqual(captured["argv"][0], str(self.prefix / "share/openwebrx/source_runtime.py"))
        self.assertIn("prod", captured["argv"])
        self.assertEqual(captured["argv"][-2:], ["--app-args", "--debug"])
        self.assertNotIn("PYTHONPATH", captured["env"])
        self.assertNotIn("PYTHONHOME", captured["env"])
        self.assertNotIn("/fake/dev", captured["env"]["LD_LIBRARY_PATH"])

    def test_help_version_and_admin_bypass_runtime(self):
        self.do_install()
        for args in (("--help",), ("--version",), ("admin", "adduser", "a user"), ("config", "migrate")):
            with self.subTest(args=args):
                output = self.run_launcher(*args)
                self.assertTrue(output.startswith("CLI <-c>"), output)
                for arg in args:
                    self.assertIn("<" + arg + ">", output)

    def test_env_file_quotes_paths(self):
        self.do_install()
        result = subprocess.run(["bash", "-c", 'source "$1"; printf "%s\\n" "$OWRX_PREFIX"', "test", str(self.prefix / "env.sh")],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.prefix))

    def test_unlink_only_own_launcher(self):
        self.do_install()
        link = self.base / "bin/openwebrx"
        deploy.main(["unlink-launcher", str(self.base / "other"), str(self.base / "bin")])
        self.assertTrue(link.is_symlink())
        deploy.main(["unlink-launcher", str(self.prefix), str(self.base / "bin")])
        self.assertFalse(link.is_symlink())

    def test_service_rendering_and_ownership(self):
        manifest = self.do_install()
        unit = self.base / "systemd/user/openwebrx-prod.service"
        manifest_path = self.prefix / "share/openwebrx/runtime.json"
        deploy.main(["service", str(manifest_path), str(unit)])
        text = unit.read_text()
        self.assertIn("%%value $$cash", text)
        self.assertIn("WantedBy=default.target", text)
        self.assertIn("KillMode=mixed", text)
        self.assertNotIn("User=root", text)
        self.assertNotIn(str(self.checkout), text)
        deploy.check_service(manifest, unit)
        unit.write_text("[Service]\nExecStart=/unrelated\n")
        with self.assertRaises(ValueError):
            deploy.main(["service", str(manifest_path), str(unit)])
        self.assertEqual(unit.read_text(), "[Service]\nExecStart=/unrelated\n")

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze not installed")
    def test_generated_unit_passes_systemd_validation(self):
        manifest = self.do_install()
        unit = self.base / "openwebrx-prod.service"
        unit.write_text(deploy.service_text(manifest))
        result = subprocess.run(["systemd-analyze", "verify", str(unit)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_safe_writer_refuses_symlink(self):
        outside = self.base / "outside"
        outside.write_text("keep")
        link = self.base / "link"
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            deploy.atomic_write(link, "replace")
        self.assertEqual(outside.read_text(), "keep")


class RuntimeTests(unittest.TestCase):
    def test_debug_is_only_automatic_for_dev(self):
        args = SimpleNamespace(venv="/venv", config="/config", mode="prod", app_args=[])
        self.assertNotIn("--debug", runtime.application_command(args))
        args.mode = "dev"
        self.assertIn("--debug", runtime.application_command(args))
        args.mode = "prod"; args.app_args = ["--debug"]
        self.assertIn("--debug", runtime.application_command(args))

    def test_each_prefix_has_its_own_instance_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            dev, prod = Path(directory) / "dev", Path(directory) / "prod"
            with runtime.instance_lock(dev):
                with self.assertRaises(RuntimeError):
                    with runtime.instance_lock(dev):
                        pass
                with runtime.instance_lock(prod):
                    pass
            with runtime.instance_lock(dev):
                pass


if __name__ == "__main__":
    unittest.main()
