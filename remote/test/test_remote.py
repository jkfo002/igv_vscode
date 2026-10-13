import hashlib
import http.client
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from install import download, extract_jdk
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
    def test_download_verifies_bytes_and_reuses_valid_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.write_bytes(b"archive bytes")
            dest = Path(directory) / "cached"
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            download(source.as_uri(), sha, dest)
            source.unlink()
            download(source.as_uri(), sha, dest)
            self.assertEqual(dest.read_bytes(), b"archive bytes")

    def test_corrupt_download_is_not_promoted_to_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.write_bytes(b"corrupt")
            dest = Path(directory) / "cached"
            with self.assertRaisesRegex(RuntimeError, "SHA256"):
                download(source.as_uri(), "0" * 64, dest)
            self.assertFalse(dest.exists())
            self.assertFalse((Path(directory) / "cached.partial").exists())

    def test_archive_cannot_escape_installation_directory(self):
        for name, link in [("../escaped", None), ("jdk/link", "../../escaped")]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w") as tar:
                    member = tarfile.TarInfo(name)
                    if link:
                        member.type = tarfile.SYMTYPE
                        member.linkname = link
                    tar.addfile(member)
                buffer.seek(0)
                with tarfile.open(fileobj=buffer) as tar:
                    with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                        extract_jdk(tar, Path(directory))

    def test_stopped_descriptor_invalidates_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "session.json"
            private_json(target, {"state": "running", "token": "secret"})
            private_json(target, {"state": "stopped"})
            self.assertNotIn("token", json.loads(target.read_text()))
            self.assertFalse(target.with_suffix(".tmp").exists())


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
