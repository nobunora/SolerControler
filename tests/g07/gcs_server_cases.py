"""Actual generation preconditions within one approved operation prefix."""
from __future__ import annotations

import hashlib
import time
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor


def run(fixture: dict) -> list[dict]:
    from google.cloud.storage import Client
    from google.api_core.exceptions import NotFound, PreconditionFailed
    names = fixture.get("owned_object_names", [])
    if not names or fixture["kind"] == "emulator":
        return [{"name": "gcs_native", "status": "INCONCLUSIVE", "reason_code": "real_gcs_owned_object_required"}]
    client = Client(project=fixture["project_id"])
    blob = client.bucket(fixture["bucket"]).blob(names[0])
    generation = None
    checkpoint = None
    checkpoint_generation = None
    cases = []
    step = "initial_generation"
    try:
        credentials_valid_before = bool(client._credentials.valid)
        auth_started = time.monotonic()
        try:
            blob.reload(timeout=4, retry=None)
        except NotFound:
            pass
        else:
            raise ValueError("synthetic object already exists")
        credentials_valid_after = bool(client._credentials.valid)
        refresh_observed = not credentials_valid_before and credentials_valid_after
        cases.append({"name": "gcs_actual_adc_initial_refresh", "status": "PASS" if refresh_observed else "INCONCLUSIVE",
                      "credentials_valid_before": credentials_valid_before,
                      "credentials_valid_after": credentials_valid_after,
                      "elapsed_ms": round((time.monotonic() - auth_started) * 1000),
                      "reason_code": "initial_refresh_observed" if refresh_observed else "initial_refresh_unobserved"})
        raw = fixture["operation_id"].encode()
        blob.upload_from_string(raw, if_generation_match=0, timeout=4, retry=None)
        blob.reload(timeout=4, retry=None)
        generation = int(blob.generation)
        assert blob.download_as_bytes(if_generation_match=generation, timeout=4, retry=None) == raw
        try:
            blob.upload_from_string(b"stale", if_generation_match=0, timeout=4, retry=None)
        except PreconditionFailed:
            pass
        else:
            raise AssertionError("server accepted stale generation")
        cases.append({"name": "gcs_native_generation_pinned", "status": "PASS",
                      "raw_sha256": hashlib.sha256(raw).hexdigest(), "generation": generation})
        step = "two_clients_cas"
        barrier = Barrier(2)
        started = time.monotonic()
        def compete(candidate):
            other = Client(project=fixture["project_id"])
            target = other.bucket(fixture["bucket"]).blob(names[0])
            try:
                barrier.wait(timeout=10)
                try:
                    target.upload_from_string(candidate, if_generation_match=generation, timeout=4, retry=None)
                    return candidate
                except PreconditionFailed:
                    return None
            finally:
                other.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(compete, candidate) for candidate in (raw + b"-a", raw + b"-b")]
            winners = [result for future in futures if (result := future.result(timeout=15)) is not None]
        assert len(winners) == 1
        blob.reload(timeout=4, retry=None)
        generation = int(blob.generation)
        assert blob.download_as_bytes(if_generation_match=generation, timeout=4, retry=None) == winners[0]
        cases.append({"name": "gcs_two_actual_clients_cas", "status": "PASS"})
        step = "selected_generation_restore"
        if len(names) < 2:
            return cases + [{"name": "selected_generation_restore", "status": "INCONCLUSIVE",
                             "reason_code": "second_owned_checkpoint_object_required"}]
        checkpoint = client.bucket(fixture["bucket"]).blob(names[1])
        checkpoint.upload_from_string(raw, if_generation_match=0, timeout=4, retry=None)
        checkpoint.reload(timeout=4, retry=None)
        checkpoint_generation = int(checkpoint.generation)
        selected = checkpoint.download_as_bytes(if_generation_match=checkpoint_generation, timeout=4, retry=None)
        assert selected == raw
        blob.upload_from_string(selected, if_generation_match=generation, timeout=4, retry=None)
        blob.reload(timeout=4, retry=None)
        generation = int(blob.generation)
        restored = blob.download_as_bytes(if_generation_match=generation, timeout=4, retry=None)
        assert restored == raw
        cases.append({"name": "selected_native_generation_restore_and_read_performance", "status": "PASS",
                      "selected_generation": checkpoint_generation, "restored_generation": generation,
                      "read_bytes": len(winners[0]) + len(selected) + len(restored),
                      "elapsed_ms": round((time.monotonic() - started) * 1000),
                      "measurement_scope": "owned GCS objects; production dashboard browser is separate"})
        return cases
    except Exception as error:
        error.add_note(f"native proof step=gcs.{step}")
        raise
    finally:
        if generation is not None:
            blob.delete(if_generation_match=generation, timeout=4, retry=None)
        if checkpoint_generation is not None:
            checkpoint.delete(if_generation_match=checkpoint_generation, timeout=4, retry=None)
        for name, selected_generation in ((names[0], generation), (names[1], checkpoint_generation)):
            if selected_generation is not None:
                fresh = client.bucket(fixture["bucket"]).blob(name)
                try:
                    fresh.reload(timeout=4, retry=None)
                except NotFound:
                    pass
                else:
                    raise AssertionError("operation-owned object cleanup is unconfirmed")
        if generation is not None and checkpoint_generation is not None:
            cases.append({"name": "gcs_owned_cleanup_native_absence", "status": "PASS", "objects": 2})
        client.close()
