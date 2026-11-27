"""Reference Jaeger contract plus opt-in real, pinned-binary restart proof."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from ops import verify_jaeger_persistence as proof


class JaegerReference(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load(proof.DEFAULT_CONFIG.read_text(encoding="utf-8"))

    def test_persistent_store_has_explicit_sync_and_finite_retention(self):
        backends = self.config["extensions"]["jaeger_storage"]["backends"]
        self.assertEqual(set(backends), {"incident_traces"})
        store = backends["incident_traces"]["badger"]
        self.assertIs(store["ephemeral"], False)
        self.assertIs(store["consistency"], True)
        self.assertEqual(store["ttl"]["spans"], "72h")
        self.assertEqual(store["directories"], {"keys": "/var/lib/jaeger/keys", "values": "/var/lib/jaeger/values"})
        self.assertEqual(self.config["extensions"]["jaeger_query"]["storage"]["traces"], "incident_traces")

    def test_acknowledgement_is_not_buffered_in_a_second_volatile_queue(self):
        self.assertEqual(self.config["service"]["pipelines"]["traces"]["processors"], ["memory_limiter"])
        exporter = self.config["exporters"]["jaeger_storage_exporter"]
        self.assertEqual(exporter["trace_storage"], "incident_traces")
        self.assertIs(exporter["queue"]["enabled"], False)
        self.assertIs(exporter["retry_on_failure"]["enabled"], False)

    def test_only_needed_ingest_and_query_surfaces_are_enabled(self):
        self.assertEqual(set(self.config["receivers"]), {"otlp"})
        http = self.config["receivers"]["otlp"]["protocols"]
        self.assertEqual(set(http), {"http"})
        self.assertEqual(http["http"]["max_request_body_size"], 4 * 1024 * 1024)
        extensions = self.config["extensions"]
        self.assertEqual(set(extensions), {"jaeger_storage", "jaeger_query", "healthcheckv2"})
        self.assertNotIn("ai", extensions["jaeger_query"])
        self.assertTrue(extensions["jaeger_query"]["grpc"]["endpoint"].startswith("127.0.0.1:"))
        self.assertEqual(self.config["service"]["telemetry"]["logs"]["level"], "warn")

    def test_memory_budget_leaves_process_headroom(self):
        limiter = self.config["processors"]["memory_limiter"]
        self.assertEqual(limiter["limit_mib"], 384)
        self.assertLess(limiter["spike_limit_mib"], limiter["limit_mib"])

    def test_local_proof_changes_io_only_and_keeps_durability_pipeline(self):
        config = proof.local_config(self.config, Path("synthetic"))
        self.assertEqual(config["service"]["pipelines"], self.config["service"]["pipelines"])
        self.assertEqual(config["exporters"], self.config["exporters"])
        self.assertEqual(config["processors"], self.config["processors"])
        self.assertEqual(config["extensions"]["jaeger_storage"]["backends"]["incident_traces"]["badger"]["ttl"], {"spans": "72h"})
        for surface in ("ingest", "query", "health"):
            self.assertTrue(proof._url(config, surface, "/").startswith("http://127.0.0.1:"))
        # The source configuration remains suitable for the container mount.
        self.assertEqual(self.config["receivers"]["otlp"]["protocols"]["http"]["endpoint"], "0.0.0.0:4318")


@unittest.skipUnless(os.getenv("JAEGER_TEST_BINARY"), "set JAEGER_TEST_BINARY to the checksum-verified pinned release")
class JaegerNativeProof(unittest.TestCase):
    def test_acknowledged_trace_survives_forced_restart_and_ttl_expires(self):
        result = proof.prove(Path(os.environ["JAEGER_TEST_BINARY"]))
        self.assertTrue(result["forced_restart_retained_trace"])
        self.assertTrue(result["continued_ingestion"])
        self.assertTrue(result["short_ttl_read_expiry"])
        self.assertTrue(result["native_service_discovery_and_search"])
        self.assertFalse(result["grafana_legacy_search_supported"])
        self.assertGreater(result["logical_store_bytes_before_cleanup"], 0)

    def test_native_schema_rejects_unknown_storage_keys(self):
        source = yaml.safe_load(proof.DEFAULT_CONFIG.read_text(encoding="utf-8"))
        source["extensions"]["jaeger_storage"]["backends"]["incident_traces"]["badger"]["typo_ttl"] = "72h"
        with tempfile.TemporaryDirectory(prefix="testlookup-jaeger-invalid-") as directory:
            path = Path(directory) / "invalid.yml"
            path.write_text(yaml.safe_dump(source), encoding="utf-8")
            result = subprocess.run([os.environ["JAEGER_TEST_BINARY"], "validate", f"--config={path}"],
                                    capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
