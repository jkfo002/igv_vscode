import http.client
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from install import GRAPHICS, deb_packages, digest, install_debs, pixi_environment
from proot_build import BUILD_RECIPE_VERSION, SOURCE_SHA256, install_proot, sha256
from runtime import environment, native_environment, binary
from session import Controller, Lease, private_json, stop_process, reserve_display


class LeaseTests(unittest.TestCase):
    def test_reconnect_extends_lease_but_no_heartbeat_expires_at_ten_minutes(self):
        now = [0]
        lease = Lease(clock=lambda: now[0])
        now[0] = 599
        self.assertFalse(lease.expired())
        lease.renew()
        now[0] = 1198
        self.assertFalse(lease.expired())
        now[0] = 1199
        self.assertTrue(lease.expired())


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller({"token": "private", "sessionId": "owned-session"})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.controller.handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, token=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        headers = {"Authorization": "Bearer " + token} if token else {}
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def test_unauthenticated_stop_does_not_end_session(self):
        self.assertEqual(self.request("POST", "/stop")[0], 401)
        self.assertEqual(self.request("POST", "/stop", "wrong")[0], 401)
        self.assertFalse(self.controller.stop.is_set())

    def test_status_does_not_disclose_credentials_and_heartbeat_renews(self):
        status, body = self.request("GET", "/status", "private")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sessionId"], "owned-session")
        self.assertNotIn(b"private", body)
        self.controller.lease.last_heartbeat = 0
        self.assertEqual(self.request("POST", "/heartbeat", "private")[0], 200)
        self.assertFalse(self.controller.lease.expired())
        self.assertEqual(self.request("POST", "/stop", "private")[0], 200)
        self.assertTrue(self.controller.stop.is_set())

    def test_wrong_method_or_unknown_route_cannot_mutate_session(self):
        self.assertEqual(self.request("GET", "/stop", "private")[0], 404)
        self.assertEqual(self.request("POST", "/unknown", "private")[0], 404)
        self.assertFalse(self.controller.stop.is_set())


class InstallerTests(unittest.TestCase):
    def test_graphics_dependencies_use_the_available_vnc_tools_and_keep_host_glibc(self):
        for ubuntu, vnc_tools in (("20.04", "tigervnc-common"), ("22.04", "tigervnc-tools"),
                                  ("24.04", "tigervnc-tools")):
            with self.subTest(ubuntu=ubuntu):
                def apt_depends(args):
                    if ubuntu == "20.04" and "tigervnc-tools" in args:
                        raise subprocess.CalledProcessError(100, args, stderr="Unable to locate package tigervnc-tools")
                    tools = "tigervnc-tools\n" if "tigervnc-tools" in args else ""
                    return ("tigervnc-standalone-server\n  Depends: tigervnc-common\ntigervnc-common\n"
                            + tools + "libx11-6:amd64\n  Depends: libc6\n"
                            "libc6\nlibc-bin\nlibgcc-s1\ngcc-10-base\nlibx11-6:amd64\n"
                            "<debconf-2.0>\n  debconf\n")

                with patch("install.run", side_effect=apt_depends):
                    packages = deb_packages([], ubuntu)
                self.assertIn(vnc_tools, packages)
                if ubuntu == "20.04":
                    self.assertNotIn("tigervnc-tools", packages)
                self.assertIn("libx11-6", packages)
                self.assertIn("x11-xkb-utils", packages)
                self.assertFalse({"libc6", "libc-bin", "libgcc-s1", "gcc-10-base", "<debconf-2.0>"} & set(packages))
                self.assertEqual(packages.count("libx11-6"), 1)

    def test_setup_rejects_system_python_even_with_an_environment_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"CONDA_PREFIX": directory, "PIXI_PROJECT_ROOT": directory}):
                with self.assertRaisesRegex(RuntimeError, "Python is outside"):
                    pixi_environment()

    def test_setup_without_pixi_gives_the_new_install_commands(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "pixi install"):
                pixi_environment()

    def test_stopped_descriptor_invalidates_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "session.json"
            private_json(target, {"state": "running", "token": "secret"})
            private_json(target, {"state": "stopped"})
            self.assertNotIn("token", json.loads(target.read_text()))
            self.assertFalse(target.with_suffix(".tmp").exists())


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.prefix = Path(self.directory.name) / "graphics"
        self.cache = self.prefix / "cache"
        self.cache.mkdir(parents=True)
        self.records = []
        for name in sorted({*GRAPHICS, "tigervnc-common"}):
            archive = self.cache / (name + ".deb")
            archive.write_bytes(name.encode())
            self.records.append({"package": name, "version": "1", "filename": archive.name, "sha256": digest(archive)})
        self.previous = {"debs": self.records}
        self.extracted = []

    def extract(self, args, **_kwargs):
        self.assertEqual(args[:2], ["dpkg-deb", "--extract"])
        target = Path(args[3])
        target.mkdir(parents=True, exist_ok=True)
        name = Path(args[2]).stem
        (target / name).write_text(name)
        self.extracted.append(name)
        return ""

    def setup_graphics(self, repair=False):
        with redirect_stdout(io.StringIO()), patch("install.run", side_effect=self.extract):
            return install_debs(self.prefix, self.cache, [], "20.04", self.previous, repair)

    def test_repeat_setup_skips_extraction_but_repair_reextracts(self):
        self.assertEqual(self.setup_graphics(), self.records)
        self.assertEqual(len(self.extracted), len(self.records))
        self.extracted.clear()
        self.assertEqual(self.setup_graphics(), self.records)
        self.assertEqual(self.extracted, [])
        self.setup_graphics(repair=True)
        self.assertEqual(len(self.extracted), len(self.records))

    def test_interrupted_setup_resumes_only_unfinished_packages(self):
        def interrupted(args, **kwargs):
            if len(self.extracted) == 2:
                raise subprocess.CalledProcessError(1, args)
            return self.extract(args, **kwargs)

        with redirect_stdout(io.StringIO()), patch("install.run", side_effect=interrupted):
            with self.assertRaises(subprocess.CalledProcessError):
                install_debs(self.prefix, self.cache, [], "20.04", self.previous)
        completed = set(self.extracted)
        self.extracted.clear()
        self.setup_graphics()
        self.assertFalse(completed & set(self.extracted))
        self.assertEqual(len(completed) + len(self.extracted), len(self.records))

    def test_deleted_graphics_root_is_not_mistaken_for_an_installed_tree(self):
        self.setup_graphics()
        shutil.rmtree(self.prefix / "root")
        self.extracted.clear()
        self.setup_graphics()
        self.assertEqual(len(self.extracted), len(self.records))

    def test_legacy_completed_install_migrates_without_reextracting(self):
        root = self.prefix / "root"
        root.mkdir()
        for record in self.records:
            (root / record["package"]).touch()
        self.setup_graphics()
        self.assertEqual(self.extracted, [])
        self.assertTrue((self.prefix / "extracted.json").is_file())


class ProotBuildTests(unittest.TestCase):
    def test_cached_build_restores_private_executable_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            graphics = Path(directory)
            private = graphics / "proot"
            private.mkdir()
            built = private / "proot"
            built.write_bytes(b"verified cached build")
            built.chmod(0o755)
            state = {"sourceSha256": SOURCE_SHA256, "recipeVersion": BUILD_RECIPE_VERSION, "binarySha256": sha256(built)}
            (private / "build.json").write_text(json.dumps(state))
            target = graphics / "root" / "usr" / "bin" / "proot"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"original Ubuntu executable")
            with redirect_stdout(io.StringIO()):
                install_proot(graphics, graphics, graphics)
            self.assertEqual(target.read_bytes(), built.read_bytes())
            self.assertEqual((private / "proot.ubuntu").read_bytes(), b"original Ubuntu executable")
            target.write_bytes(b"re-extracted Ubuntu executable")
            with redirect_stdout(io.StringIO()):
                install_proot(graphics, graphics, graphics)
            self.assertEqual(target.read_bytes(), built.read_bytes())
            self.assertEqual((private / "proot.ubuntu").read_bytes(), b"original Ubuntu executable")

    def test_unverified_source_is_never_executed_or_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            graphics = Path(directory)
            target = graphics / "root" / "usr" / "bin" / "proot"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"original")
            with redirect_stdout(io.StringIO()), patch("proot_build.urllib.request.urlopen", return_value=io.BytesIO(b"bad archive")):
                with self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
                    install_proot(graphics, graphics, graphics)
            self.assertEqual(target.read_bytes(), b"original")
            self.assertFalse(list(graphics.glob("*.download")))


class RuntimeTests(unittest.TestCase):
    def test_registered_pixi_environment_works_without_shell_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            conda = Path(directory) / "conda"
            graphics = conda / "share" / "igv-remote"
            state.mkdir()
            (conda / "bin").mkdir(parents=True)
            python = conda / "bin" / "python"
            python.touch()
            native_bin = graphics / "root" / "usr" / "bin"
            native_bin.mkdir(parents=True)
            (native_bin / "Xvnc").touch()
            java = conda / "lib" / "jvm" / "bin" / "java"
            (state / "runtime.json").write_text(json.dumps({"schemaVersion": 2, "manager": "pixi",
                "condaPrefix": str(conda), "graphicsPrefix": str(graphics), "python": str(python),
                "java": str(java), "javaHome": str(java.parent.parent)}))
            with patch.dict("os.environ", {"PYTHONPATH": "unrelated-modules", "PYTHONHOME": "unrelated-python"}):
                _prefix, manifest, env = environment(state)
                self.assertNotIn("PYTHONPATH", env)
                self.assertNotIn("PYTHONHOME", env)
                self.assertEqual(env["CONDA_PREFIX"], str(conda))
                self.assertEqual(manifest["java"], str(java))
                self.assertEqual(binary(state, "Xtigervnc", "Xvnc"), str(native_bin / "Xvnc"))
                graphics_env = native_environment(state, env)
                self.assertIn(str(graphics / "root" / "usr" / "lib"), graphics_env["LD_LIBRARY_PATH"])
                self.assertEqual(env["LD_LIBRARY_PATH"], str(conda / "lib"))

    def test_missing_pixi_environment_reports_how_to_recreate_it(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "runtime.json").write_text(json.dumps({"manager": "pixi", "condaPrefix": directory,
                                                          "python": str(state / "missing-python")}))
            with self.assertRaisesRegex(RuntimeError, "pixi install"):
                environment(state)

    def test_previous_runtime_still_resolves_its_private_java_and_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "runtime.json").write_text(json.dumps({"schemaVersion": 1, "python": "old-python"}))
            _prefix, manifest, env = environment(state)
            self.assertEqual(manifest["java"], str(state.resolve() / "jdk" / "bin" / "java"))
            self.assertEqual(env["PYTHONPATH"], str(state.resolve() / "python"))


@unittest.skipUnless(sys.platform == "linux", "Linux process groups")
class ProcessTests(unittest.TestCase):
    def test_display_reservation_is_exclusive_and_reusable_after_release(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session"
            session.mkdir()
            number, first = reserve_display(session)
            try:
                other_number, other = reserve_display(session)
                try:
                    self.assertNotEqual(number, other_number)
                finally:
                    other.close()
            finally:
                first.close()
            reused_number, reused = reserve_display(session)
            try:
                self.assertEqual(number, reused_number)
            finally:
                reused.close()

    def test_cleanup_terminates_only_the_owned_process_group(self):
        owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        try:
            stop_process(owned)
            self.assertIsNotNone(owned.poll())
            self.assertIsNone(other.poll())
        finally:
            stop_process(owned)
            stop_process(other)


if __name__ == "__main__":
    unittest.main()
