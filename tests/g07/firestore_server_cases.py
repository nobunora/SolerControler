"""Operation-owned native Firestore probes, with version-guarded cleanup."""
from __future__ import annotations

import os
import time
import json
import subprocess
import sys
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor


class RpcRecorder:
    """Instance-only recording; every RPC still reaches the actual SDK/server."""
    def __init__(self, transport):
        self.transport = transport
        self.calls = []
        self.drop_commit_ack = False

    def __getattr__(self, name):
        original = getattr(self.transport, name)
        def invoke(*args, **kwargs):
            started = time.monotonic()
            response = original(*args, **kwargs)
            if name == "batch_get_documents":
                response = iter(list(response))
            request = kwargs.get("request", {})
            self.calls.append({"method": name, "timeout": kwargs.get("timeout"),
                               "retry_disabled": kwargs.get("retry") is None,
                               "writes": len(request.get("writes", [])),
                               "elapsed_ms": round((time.monotonic() - started) * 1000)})
            if name == "commit" and self.drop_commit_ack:
                self.drop_commit_ack = False
                raise TimeoutError("injected client acknowledgement loss after actual commit")
            return response
        return invoke


def run(fixture: dict) -> list[dict]:
    from google.auth.credentials import AnonymousCredentials
    from google.cloud.firestore_v1 import Client
    from google.api_core.exceptions import AlreadyExists, FailedPrecondition, Conflict
    from app.operations.forecast_persistence import _run_bounded_projection_transaction

    paths = fixture.get("owned_document_paths", [])
    if len(paths) < 2:
        return [{"name": "firestore_native", "status": "INCONCLUSIVE", "reason_code": "two_owned_documents_required"}]
    if fixture["kind"] == "emulator":
        os.environ["FIRESTORE_EMULATOR_HOST"] = fixture["endpoint"]
        client = Client(project=fixture["project_id"], database=fixture["database_id"], credentials=AnonymousCredentials())
    else:
        client = Client(project=fixture["project_id"], database=fixture["database_id"])
    references = [client.document(path) for path in paths[:2]]
    versions = {}
    cases = []
    step = "seed"
    deadline = time.monotonic() + 60
    try:
        step = "first_owned_get_auth_observation"
        credentials_valid_before = bool(client._credentials.valid)
        auth_started = time.monotonic()
        first_snapshot = references[0].get(timeout=4, retry=None)
        credentials_valid_after = bool(client._credentials.valid)
        refresh_observed = not credentials_valid_before and credentials_valid_after
        cases.append({"name": "firestore_actual_adc_initial_refresh", "status": "PASS" if refresh_observed else "INCONCLUSIVE",
                      "credentials_valid_before": credentials_valid_before,
                      "credentials_valid_after": credentials_valid_after,
                      "elapsed_ms": round((time.monotonic() - auth_started) * 1000),
                      "reason_code": "initial_refresh_observed" if refresh_observed else "initial_refresh_unobserved"})
        step = "seed"
        if first_snapshot.exists or references[1].get(timeout=4, retry=None).exists:
            raise ValueError("synthetic test document already exists")
        for ref in references:
            value = {"operation_id": fixture["operation_id"], "count": 0, "date": fixture.get("target_date")}
            ref.create(value, timeout=4, retry=None)
            versions[ref.path] = ref.get(timeout=4, retry=None).update_time
        def increment(transaction):
            values = [ref.get(transaction=transaction, timeout=4, retry=None).to_dict() for ref in references]
            for ref, value in zip(references, values):
                transaction.update(ref, {"count": value["count"] + 1})
        _run_bounded_projection_transaction(client, increment, deadline=deadline, max_attempts=3)
        saved = [ref.get(timeout=4, retry=None) for ref in references]
        versions.update({ref.path: snap.update_time for ref, snap in zip(references, saved)})
        assert [snap.to_dict()["count"] for snap in saved] == [1, 1]
        # A server precondition conflict must atomically reject the other create.
        batch = client.batch()
        for ref in references:
            batch.create(ref, {"count": 999})
        try:
            batch.commit(timeout=4, retry=None)
        except AlreadyExists:
            pass
        else:
            raise AssertionError("server accepted existing-document create")
        assert all(ref.get(timeout=4, retry=None).to_dict()["count"] == 1 for ref in references)
        cases.append({"name": "firestore_native_atomic_and_precondition", "status": "PASS"})

        step = "two_clients_cas"
        barrier = Barrier(2)
        old = [ref.get(timeout=4, retry=None) for ref in references]
        def compete(number):
            other = Client(project=fixture["project_id"], database=fixture["database_id"],
                           **({"credentials": AnonymousCredentials()} if fixture["kind"] == "emulator" else {}))
            try:
                candidate = other.batch()
                for ref, snapshot in zip(references, old):
                    candidate.update(other.document(ref.path), {"count": number},
                                     option=other.write_option(last_update_time=snapshot.update_time))
                barrier.wait(timeout=10)
                try:
                    candidate.commit(timeout=4, retry=None)
                    return number
                except (FailedPrecondition, Conflict):
                    return None
            finally:
                other.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(compete, number) for number in (2, 3)]
            results = [future.result(timeout=15) for future in futures]
        winners = [number for number in results if number is not None]
        assert len(winners) == 1
        assert all(ref.get(timeout=4, retry=None).to_dict()["count"] == winners[0] for ref in references)
        versions.update({ref.path: ref.get(timeout=4, retry=None).update_time for ref in references})
        cases.append({"name": "firestore_two_clients_native_cas", "status": "PASS"})

        step = "commit_ack_loss"
        recorder = RpcRecorder(client._firestore_api)
        client._firestore_api_internal = recorder
        marker = fixture["operation_id"] + "-ack"
        def same_vintage(transaction):
            current = [ref.get(transaction=transaction, timeout=4, retry=None).to_dict() for ref in references]
            for ref, value in zip(references, current):
                if value.get("vintage") != marker:
                    transaction.update(ref, {"count": value["count"] + 1, "vintage": marker})
        recorder.drop_commit_ack = True
        try:
            _run_bounded_projection_transaction(client, same_vintage, deadline=time.monotonic() + 20, max_attempts=3)
        except TimeoutError:
            pass
        else:
            raise AssertionError("client acknowledgement loss was not propagated")
        assert all(ref.get(timeout=4, retry=None).to_dict()["vintage"] == marker for ref in references)
        baseline = len(recorder.calls)
        _run_bounded_projection_transaction(client, same_vintage, deadline=time.monotonic() + 20, max_attempts=3)
        assert all(call["writes"] == 0 for call in recorder.calls[baseline:] if call["method"] == "commit")
        versions.update({ref.path: ref.get(timeout=4, retry=None).update_time for ref in references})
        cases.append({"name": "actual_commit_ack_loss_native_readback_same_vintage_write0", "status": "PASS"})

        step = "worker_crash"
        # Kill an actual worker after the server acknowledged its writes. The
        # second worker reads that same marker and therefore performs no writes.
        child = '''import json,os,sys
from google.cloud.firestore_v1 import Client
from google.auth.credentials import AnonymousCredentials
d=json.loads(sys.argv[1]);kw={"credentials":AnonymousCredentials()} if d["emulator"] else {}
c=Client(project=d["project"],database=d["database"],**kw);refs=[c.document(p) for p in d["paths"]]
values=[r.get(timeout=4,retry=None).to_dict() for r in refs]
if all(v.get("vintage")==d["marker"] for v in values):
 print(json.dumps({"writes":0}),flush=True);sys.exit(0)
b=c.batch()
for r,v in zip(refs,values):b.update(r,{"vintage":d["marker"],"count":v["count"]+1})
result=b.commit(timeout=4,retry=None);print(json.dumps({"writes":len(result)}),flush=True);os._exit(23)
'''
        child_input = json.dumps({"project": fixture["project_id"], "database": fixture["database_id"],
                                 "paths": [ref.path for ref in references], "marker": marker + "-crash",
                                 "emulator": fixture["kind"] == "emulator"})
        crashed = subprocess.run([sys.executable, "-c", child, child_input], capture_output=True, timeout=30, text=True)
        assert crashed.returncode == 23 and json.loads(crashed.stdout)["writes"] == 2
        assert all(ref.get(timeout=4, retry=None).to_dict()["vintage"] == marker + "-crash" for ref in references)
        restarted = subprocess.run([sys.executable, "-c", child, child_input], capture_output=True, timeout=30, text=True)
        assert restarted.returncode == 0 and json.loads(restarted.stdout)["writes"] == 0
        versions.update({ref.path: ref.get(timeout=4, retry=None).update_time for ref in references})
        cases.append({"name": "worker_crash_after_actual_commit_restart_write0", "status": "PASS"})

        step = "deadline_and_counts"
        # Expiration must stop before begin; no remote request is delegated.
        baseline = len(recorder.calls)
        try:
            _run_bounded_projection_transaction(client, same_vintage, deadline=time.monotonic() - 1, max_attempts=3)
        except TimeoutError:
            pass
        else:
            raise AssertionError("expired transaction was started")
        assert len(recorder.calls) == baseline
        assert all(call["timeout"] <= 4 and call["retry_disabled"] for call in recorder.calls if call["method"] in {"begin_transaction", "commit", "rollback"})
        payload_bytes = sum(len(json.dumps(ref.get(timeout=4, retry=None).to_dict()).encode()) for ref in references)
        cases.append({"name": "native_transaction_deadline_and_rpc_counts", "status": "PASS",
                      "rpc_calls": recorder.calls, "rpc_count": len(recorder.calls),
                      "payload_bytes": payload_bytes})
        return cases
    except Exception as error:
        error.add_note(f"native proof step=firestore.{step}")
        raise
    finally:
        if isinstance(client._firestore_api_internal, RpcRecorder):
            client._firestore_api_internal = client._firestore_api_internal.transport
        for ref in references:
            if ref.path in versions:
                ref.delete(option=client.write_option(last_update_time=versions[ref.path]), timeout=4, retry=None)
                if ref.get(timeout=4, retry=None).exists:
                    raise AssertionError("operation-owned document cleanup is unconfirmed")
        if len(versions) == len(references):
            cases.append({"name": "firestore_owned_cleanup_native_absence", "status": "PASS", "documents": len(references)})
        client.close()
