import base64
import io
import json
from email.message import Message

import pytest

import printer.creality_probe_backend as backend
from printer.creality_probe_backend import ProbeHandler


@pytest.fixture(autouse=True)
def isolate_identity_state(tmp_path, monkeypatch):
    state_path = tmp_path / "identity_state.json"
    monkeypatch.setattr(backend.ProbeHandler, "_identity_state_path", lambda self: str(state_path))
    yield state_path


def test_build_info_payload_marks_lan_printer_fields(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert payload["connectType"] == 1001
    assert payload["deviceType"] == 0
    assert payload["type"] == 0
    assert payload["isLanPrinter"] is True
    assert payload["lanCompatible"] is True
    assert payload["oldPrinter"] is False
    assert payload["state"] == 1
    assert payload["deviceState"] == 1
    assert payload["localOnline"] is True
    assert payload["cloudOnline"] is False
    assert payload["cxyOnline"] is False
    assert payload["isExistInLocal"] is True
    assert payload["isExistInCxy"] is False


def test_build_info_payload_ignores_stale_persisted_runtime_state_for_lan(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})

    handler._save_persisted_identity({
        "deviceName": "Old Name",
        "aliasName": "Old Name",
        "name": "Old Name",
        "machine_name": "Old Name",
        "identity": "192.168.1.100",
        "status": {"state": "printing"},
        "temperature": {"bed": {"value": 999.0}, "nozzle": {"value": 999.0}},
        "cameraState": {"state": "streaming"},
        "recordState": {"recording": True},
        "streamState": {"source": "webrtc"},
        "ctrol": {"fan": 100},
        "data": {"bedTemp0": 55.0},
    })

    payload = handler._build_info_payload()

    assert payload["identity"] is None
    assert payload["status"]["state"] == "standby"
    assert payload["temperature"]["bed"]["value"] == 0.0
    assert payload["temperature"]["nozzle"]["value"] == 0.0
    assert payload["cameraState"]["state"] == "ready"
    assert payload["recordState"]["recording"] is False
    assert payload["streamState"]["source"] == "webcam"


def test_lan_device_entry_uses_null_identity_to_avoid_cloud_badge(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()
    compat_entry = handler._build_compat_device_entry(payload)

    assert compat_entry["identity"] is None
    assert compat_entry["cloudOnline"] is False


def test_lan_device_entry_uses_address_for_matching_and_display_name_for_ui(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()
    payload["deviceName"] = "Lan Test Rename"
    payload["aliasName"] = "Lan Test Rename"
    payload["name"] = "Lan Test Rename"

    compat_entry = handler._build_compat_device_entry(payload)

    assert compat_entry["deviceName"] == "192.168.1.100"
    assert compat_entry["aliasName"] == "Lan Test Rename"
    assert compat_entry["name"] == "Lan Test Rename"
    assert compat_entry["device"]["deviceName"] == "192.168.1.100"
    assert compat_entry["device"]["aliasName"] == "Lan Test Rename"


def test_poll_state_keeps_lan_identity_null(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_poll_state()

    assert captured["payload"]["result"][0]["identity"] is None


def test_detail_response_keeps_lan_identity_null(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_print_cluster_device_detail({})

    response = captured["payload"]
    assert response["result"]["identity"] is None
    assert response["result"]["device"]["identity"] is None


def test_detail_response_uses_address_for_device_matching_and_display_name_for_ui(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_print_cluster_device_detail({"deviceName": "Lan Test Rename", "aliasName": "Lan Test Rename", "address": "192.168.1.100", "dn": "192.168.1.100"})

    response = captured["payload"]["result"]
    assert response["deviceName"] == "192.168.1.100"
    assert response["aliasName"] == "Lan Test Rename"
    assert response["name"] == "Lan Test Rename"
    assert response["device"]["deviceName"] == "192.168.1.100"
    assert response["device"]["aliasName"] == "Lan Test Rename"


def test_poll_state_uses_request_context_for_names_and_address(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_poll_state({"deviceName": "Lan Test Rename", "aliasName": "Lan Test Rename", "address": "192.168.1.100", "dn": "192.168.1.100"})

    response = captured["payload"]["result"][0]
    assert response["deviceName"] == "192.168.1.100"
    assert response["aliasName"] == "Lan Test Rename"
    assert response["name"] == "Lan Test Rename"
    assert response["address"] == "192.168.1.100"
    assert response["device"]["deviceName"] == "192.168.1.100"
    assert response["device"]["aliasName"] == "Lan Test Rename"
    assert response["device"]["address"] == "192.168.1.100"


def test_detail_response_keeps_machine_name_model_based_after_display_name_update(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_print_cluster_device_detail({"deviceName": "Lan Test Rename", "aliasName": "Lan Test Rename", "address": "192.168.1.100", "dn": "192.168.1.100"})

    response = captured["payload"]["result"]
    assert response["deviceName"] == "192.168.1.100"
    assert response["aliasName"] == "Lan Test Rename"
    assert response["name"] == "Lan Test Rename"
    assert response["machine_name"] == "K2 Plus"
    assert response["machine_type"] == "K2 Plus"
    assert response["model"] == "K2 Plus"
    assert response["modelName"] == "K2 Plus"
    assert response["device"]["machine_name"] == "K2 Plus"
    assert response["device"]["machine_type"] == "K2 Plus"


def test_poll_state_preserves_model_fields_while_using_request_name_for_display(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_poll_state({"deviceName": "Lan Test Rename", "aliasName": "Lan Test Rename", "address": "192.168.1.100", "dn": "192.168.1.100"})

    response = captured["payload"]["result"][0]
    assert response["model"] == "K2 Plus"
    assert response["modelName"] == "K2 Plus"
    assert response["machine_name"] == "K2 Plus"
    assert response["machine_type"] == "K2 Plus"
    assert response["device"]["model"] == "K2 Plus"
    assert response["device"]["modelName"] == "K2 Plus"
    assert response["device"]["machine_name"] == "K2 Plus"
    assert response["device"]["machine_type"] == "K2 Plus"


def test_detail_response_exposes_lan_type_for_client_normalization(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_print_cluster_device_detail({})

    response = captured["payload"]
    assert response["result"]["type"] == 0
    assert response["result"]["device"]["type"] == 0
    assert response["result"]["connectType"] == 1001
    assert response["result"]["isLanPrinter"] is True
    assert response["result"]["lanCompatible"] is True
    assert response["result"]["device"]["connectType"] == 1001
    assert response["result"]["device"]["isLanPrinter"] is True
    assert response["result"]["device"]["lanCompatible"] is True


def test_lan_detail_request_does_not_persist_non_null_identity(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {}})
    monkeypatch.setattr(handler, "_send_json", lambda payload: None)
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {"deviceName": "My LAN Printer", "aliasName": "My LAN Printer", "modelName": "K2 Plus", "address": "192.168.1.100", "dn": "192.168.1.100"})

    handler.serve_print_cluster_device_detail({"deviceName": "My LAN Printer", "aliasName": "My LAN Printer", "modelName": "K2 Plus", "address": "192.168.1.100", "dn": "192.168.1.100"})

    info_payload = handler._build_info_payload()
    assert info_payload["isLanPrinter"] is True
    assert info_payload["identity"] is None


def test_fetch_moonraker_info_uses_port_from_moonraker_url(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(backend, "MOONRAKER_URL", "http://127.0.0.1:7126")
    monkeypatch.setattr(handler, "_fetch_json", lambda path, timeout=0: {"result": {"name": "K2 Plus", "model": "K2 Plus"}})

    info = handler._fetch_moonraker_info(timeout=0)

    assert info["moonraker_port"] == 7126


def test_default_info_payload_reports_active_state_for_stock_client(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert payload["state"] == 1
    assert payload["deviceState"] == 1
    assert payload["status"]["state"] == "standby"
    assert payload["status"]["display_status"]["progress"] == 0.0
    assert payload["model_name"] == "K2 Plus"
    assert payload["boxsInfo"]["cfsName"] == "Lan Compat CFS"
    assert payload["boxsInfo"]["materialBoxs"][0]["materials"][0]["name"] == "Material"
    assert payload["boxConfig"]["cAutoFeed"] == 1


def test_detail_payload_exposes_lan_state_flags(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})

    payload = handler._build_detail_payload()
    device = payload["result"]["device"]

    assert device["identity"] is None
    assert device["localOnline"] is True
    assert device["cloudOnline"] is False
    assert device["cxyOnline"] is False
    assert device["isExistInLocal"] is True
    assert device["isExistInCxy"] is False


def test_state_transition_trace_records_identity_changes():
    handler = ProbeHandler.__new__(ProbeHandler)
    handler.server = type("Server", (), {"_compat_state_history": []})()

    handler._record_state_transition(
        "info-build",
        {"identity": None, "deviceName": "Lan Test", "address": "192.168.1.100", "state": 0, "deviceState": 0},
        persisted_identity={"deviceName": "Lan Test", "identity": None},
    )

    assert handler.server._compat_state_history[0]["step"] == "info-build"
    assert handler.server._compat_state_history[0]["identity"] is None
    assert handler.server._compat_state_history[0]["deviceName"] == "Lan Test"


def test_status_page_renders_contract_trace_section(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    html = handler._build_status_page_html(
        [{"name": "Probe", "ok": True, "detail": "ok"}],
        trace_lines=["[TRACE] /api/cxy/v3/print/record/list historyList=3", "[TRACE] /api/rest/iotrouter/rpc/twoway/abc pFileList=2"],
    )

    assert "Compatibility trace" in html
    assert "historyList=3" in html
    assert "pFileList=2" in html


def test_preset_compatibility_evaluation_uses_normalized_profiles():
    handler = ProbeHandler.__new__(ProbeHandler)

    device = {
        "name": "K2 Plus",
        "family": "k2",
        "nozzleSize": 0.4,
        "connected": True,
        "traits": ["lan", "video", "heated-bed"],
        "tags": ["creality"],
    }
    preset = {
        "family": "K2",
        "nozzleSize": "0.4",
        "requiredTraits": ["lan"],
        "requiredTags": ["creality"],
    }

    result = handler._evaluate_preset_compatibility(device, preset)

    assert result["compatible"] is True
    assert result["reasons"] == []
    assert result["profile"]["family"] == "k2"
    assert result["profile"]["traits"] == {"lan", "video", "heated-bed"}


def test_status_page_serves_from_unique_collision_resistant_path(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    handler.path = "/debug/creality-probe-status"
    handler.headers = Message()
    handler.headers["User-Agent"] = "pytest"
    handler.address_string = lambda: "127.0.0.1"
    handler.server = type("Server", (), {"_compat_audit": [], "_compat_payload_snapshots": {}})()
    handler.send_error = lambda *args, **kwargs: None

    served = {}

    def fake_serve_status_page():
        served["ok"] = True

    monkeypatch.setattr(handler, "serve_status_page", fake_serve_status_page)
    monkeypatch.setattr(handler, "_record_request_audit", lambda *args, **kwargs: None)

    handler.do_GET()

    assert served.get("ok") is True


def test_post_print_start_reuses_body_without_re_reading(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    handler.path = "/printer/print/start"
    handler.headers = Message()
    handler.headers["Content-Length"] = "18"
    handler.headers["User-Agent"] = "pytest"
    handler.address_string = lambda: "127.0.0.1"
    handler.rfile = io.BytesIO(b'{"filename":"job"}')
    handler.server = type("Server", (), {"_compat_audit": [], "_compat_payload_snapshots": {}})()
    handler.send_error = lambda *args, **kwargs: None

    captured = {}

    def fake_read_json_body(content_length, body_bytes=None):
        assert content_length == 18
        return {"filename": "job.gcode"}

    def fake_serve_print_start(body=None):
        captured["body"] = body

    monkeypatch.setattr(handler, "_read_json_body", fake_read_json_body)
    monkeypatch.setattr(handler, "serve_print_start", fake_serve_print_start)
    monkeypatch.setattr(handler, "_record_request_audit", lambda *args, **kwargs: None)

    handler.do_POST()

    assert captured["body"] == {"filename": "job.gcode"}


def test_post_detail_route_forwards_body_to_detail_handler(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    handler.path = "/api/rest/print/cluster/devices/getDeviceDetail"
    handler.headers = Message()
    handler.headers["Content-Length"] = "34"
    handler.headers["User-Agent"] = "pytest"
    handler.address_string = lambda: "127.0.0.1"
    handler.rfile = io.BytesIO(b'{"deviceName":"Lan Test"}')
    handler.server = type("Server", (), {"_compat_audit": [], "_compat_payload_snapshots": {}})()
    handler.send_error = lambda *args, **kwargs: None

    captured = {}

    def fake_serve_print_cluster_device_detail(body=None):
        captured["body"] = body

    monkeypatch.setattr(handler, "serve_print_cluster_device_detail", fake_serve_print_cluster_device_detail)
    monkeypatch.setattr(handler, "_record_request_audit", lambda *args, **kwargs: None)

    handler.do_POST()

    assert captured["body"] == {"deviceName": "Lan Test"}


def test_post_add_single_task_route_forwards_body_to_add_handler(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    handler.path = "/api/rest/print/cluster/addSingleTask"
    handler.headers = Message()
    handler.headers["Content-Length"] = "34"
    handler.headers["User-Agent"] = "pytest"
    handler.address_string = lambda: "127.0.0.1"
    handler.rfile = io.BytesIO(b'{"deviceName":"Lan Test"}')
    handler.server = type("Server", (), {"_compat_audit": [], "_compat_payload_snapshots": {}})()
    handler.send_error = lambda *args, **kwargs: None

    captured = {}

    def fake_serve_add_single_task(body=None):
        captured["body"] = body

    monkeypatch.setattr(handler, "serve_add_single_task", fake_serve_add_single_task)
    monkeypatch.setattr(handler, "_record_request_audit", lambda *args, **kwargs: None)

    handler.do_POST()

    assert captured["body"] == {"deviceName": "Lan Test"}


def test_add_single_task_returns_print_info_contract(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_add_single_task({"deviceName": "Lan Test Rename", "aliasName": "Lan Test Rename", "address": "192.168.1.100", "dn": "192.168.1.100"})

    payload = captured["payload"]
    assert payload["code"] == 0
    assert payload["result"]["printInfo"]["deviceName"] == "192.168.1.100"
    assert payload["result"]["printInfo"]["aliasName"] == "Lan Test Rename"
    assert payload["result"]["printInfo"]["connectType"] == 1001
    assert payload["result"]["printInfo"]["isLanPrinter"] is True
    assert payload["result"]["printInfo"]["model"] == "K2 Plus"


def test_add_single_task_falls_back_to_lightweight_payload_when_detail_building_fails(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: (_ for _ in ()).throw(RuntimeError("detail unavailable")))
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_add_single_task({"deviceName": "Lan Test Rename", "aliasName": "Lan Test Rename", "address": "192.168.1.100", "dn": "192.168.1.100"})

    payload = captured["payload"]
    assert payload["code"] == 0
    assert payload["result"]["printInfo"]["deviceName"] == "192.168.1.100"
    assert payload["result"]["printInfo"]["aliasName"] == "Lan Test Rename"
    assert payload["result"]["printInfo"]["address"] == "192.168.1.100"
    assert payload["result"]["printInfo"]["identity"] is None


def test_upload_routes_receive_raw_bytes(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    handler.path = "/server/files/upload"
    payload = b"--foo\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\nabc\r\n--foo--"
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(payload))
    handler.headers["Content-Type"] = "multipart/form-data; boundary=foo"
    handler.headers["User-Agent"] = "pytest"
    handler.address_string = lambda: "127.0.0.1"
    handler.rfile = io.BytesIO(payload)
    handler.server = type("Server", (), {"_compat_audit": [], "_compat_payload_snapshots": {}})()
    handler.send_error = lambda *args, **kwargs: None

    captured = {}

    def fake_serve_upload_compat(path, body=None):
        captured["path"] = path
        captured["body"] = body

    monkeypatch.setattr(handler, "serve_upload_compat", fake_serve_upload_compat)
    monkeypatch.setattr(handler, "_record_request_audit", lambda *args, **kwargs: None)

    handler.do_POST()

    assert captured["path"] == "/server/files/upload"
    assert captured["body"] == b"--foo\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\nabc\r\n--foo--"


def test_status_page_renders_request_audit_and_payload_snapshot(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    html = handler._build_status_page_html(
        [{"name": "Probe", "ok": True, "detail": "ok"}],
        audit_entries=[{"route": "/info", "method": "GET", "query": "foo=bar", "timestamp": "2026-08-01T00:00:00Z", "duration_ms": 12}],
        payload_summaries={"/info": {"identity": "192.168.1.100", "name": "K2 Plus", "model": "K2 Plus"}},
    )

    assert "Compatibility audit" in html
    assert "/info" in html
    assert "GET" in html
    assert "192.168.1.100" in html
    assert "ms=12" in html


def test_info_payload_uses_live_print_state_for_idle_printer(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    def fake_fetch_json(path, timeout=0):
        if path.startswith("/printer/objects/query"):
            return {"result": {"status": {"print_stats": {"state": "standby", "filename": "", "print_duration": 0}, "display_status": {"progress": 0.0}, "heater_bed": {"temperature": 28.0, "target": 0.0}, "extruder": {"temperature": 30.0, "target": 0.0}, "gcode_move": {"speed_factor": 1.0}}}}
        return {"result": {"system_info": {"network": {}}}}

    monkeypatch.setattr(handler, "_fetch_json", fake_fetch_json)
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert payload["state"] == 0
    assert payload["deviceState"] == 0
    assert payload["status"]["state"] == "standby"
    assert payload["status"]["display_status"]["progress"] == 0.0


def test_info_payload_keeps_lan_identity_null_when_public_address_is_ip(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_public_address", lambda: "192.168.1.100")

    payload = handler._build_info_payload()

    assert payload["isLanPrinter"] is True
    assert payload["identity"] is None


def test_info_payload_keeps_lan_identity_null(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_public_address", lambda: "3d.nrvous.io")

    payload = handler._build_info_payload()

    assert payload["isLanPrinter"] is True
    assert payload["identity"] is None


def test_multi_machine_uses_null_identity_for_lan_printer(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"print_stats": {"state": "standby"}, "display_status": {"progress": 0.0}})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_public_address", lambda: "3d.nrvous.io")
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_multi_machine()

    assert captured["payload"]["result"]["multi_printer_info"][0]["identity"] is None


def test_info_payload_keeps_stable_machine_name_when_cloud_printer_name_is_present(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus", "printer_name": "K2Plus-ABCD"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert payload["name"] == "K2 Plus"
    assert payload["machine_name"] == "K2 Plus"
    assert payload["model"] == "K2 Plus"
    assert payload["modelName"] == "K2 Plus"


def test_edit_endpoint_persists_custom_device_name_for_followup_requests(monkeypatch, tmp_path):
    handler = ProbeHandler.__new__(ProbeHandler)
    identity_state_path = tmp_path / "identity_state.json"

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus", "printer_name": "K2Plus-ABCD"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_identity_state_path", lambda: str(identity_state_path))

    handler.serve_print_cluster_device_edit({"deviceName": "K2Plus-ABCD", "aliasName": "K2Plus-ABCD"})
    payload = handler._build_info_payload()

    assert payload["name"] == "K2Plus-ABCD"
    assert payload["machine_name"] == "K2 Plus"
    assert payload["model"] == "K2 Plus"
    assert payload["modelName"] == "K2 Plus"
    assert payload["deviceName"] == "192.168.1.100"
    assert payload["aliasName"] == "K2Plus-ABCD"


def test_lan_info_payload_ignores_stale_persisted_machine_name(monkeypatch, tmp_path):
    handler = ProbeHandler.__new__(ProbeHandler)
    identity_state_path = tmp_path / "identity_state.json"

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_identity_state_path", lambda: str(identity_state_path))

    identity_state_path.write_text(json.dumps({
        "deviceName": "Lan Test Rename",
        "aliasName": "Lan Test Rename",
        "name": "Lan Test Rename",
        "machine_name": "Lan Test Rename",
        "machine_type": "Lan Test Rename",
        "model": "Lan Test Rename",
        "modelName": "Lan Test Rename",
    }), encoding="utf-8")

    payload = handler._build_info_payload()

    assert payload["name"] == "Lan Test Rename"
    assert payload["deviceName"] == "192.168.1.100"
    assert payload["aliasName"] == "Lan Test Rename"
    assert payload["machine_name"] == "K2 Plus"
    assert payload["machine_type"] == "K2 Plus"
    assert payload["model"] == "K2 Plus"
    assert payload["modelName"] == "K2 Plus"


def test_edit_endpoint_persists_media_and_record_state_for_followup_requests(monkeypatch, tmp_path):
    handler = ProbeHandler.__new__(ProbeHandler)
    identity_state_path = tmp_path / "identity_state.json"

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_identity_state_path", lambda: str(identity_state_path))

    handler.serve_print_cluster_device_edit({
        "deviceName": "K2 Plus",
        "aliasName": "K2 Plus",
        "cameraState": {"enabled": True, "state": "ready"},
        "recordState": {"recording": True, "timelapse": True},
        "streamState": {"active": True, "source": "webcam"},
        "record": {"timelapse": True, "video": True, "camera": True},
        "filamentsList": [{"cId": 1, "id": 1, "name": "Material"}],
        "boxsInfo": {"cfsName": "Lan Compat CFS"},
        "boxConfig": {"cAutoFeed": 1},
    })
    payload = handler._build_info_payload()

    assert payload["cameraState"]["state"] == "ready"
    assert payload["recordState"]["timelapse"] is True
    assert payload["streamState"]["source"] == "webcam"
    assert payload["record"]["timelapse"] is True
    assert payload["filamentsList"][0]["name"] == "Material"
    assert payload["boxsInfo"]["cfsName"] == "Lan Compat CFS"


def test_legacy_protocal_probe_returns_result_wrapper_for_stock_client(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_protocal_csp()

    payload = captured["payload"]
    assert payload["model"] == "K2 Plus"
    assert payload["address"] == "192.168.1.100"
    assert payload["connectType"] == 1001


def test_stream_probe_payload_uses_public_host_for_stock_client(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    headers = Message()
    headers["Host"] = "3d.nrvous.io"
    headers["X-Forwarded-Proto"] = "https"
    handler.headers = headers

    handler.serve_stream_probe("/api/v1/streams")

    payload = captured["payload"]
    assert payload["webcam"]["producers"][0]["url"].startswith("webrtc:https://3d.nrvous.io/call/webrtc_local")


def test_stream_probe_payload_uses_public_host_env_when_headers_are_local(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setenv("PUBLIC_HOST", "3d.nrvous.io")
    monkeypatch.setenv("PUBLIC_SCHEME", "https")
    headers = Message()
    headers["Host"] = "127.0.0.1"
    handler.headers = headers

    handler.serve_stream_probe("/api/v1/streams")

    payload = captured["payload"]
    assert payload["webcam"]["producers"][0]["url"].startswith("webrtc:https://3d.nrvous.io/call/webrtc_local")


def test_info_payload_prefers_lan_ip_for_printer_identity_when_public_host_is_configured(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setenv("PUBLIC_HOST", "3d.nrvous.io")
    monkeypatch.setenv("PUBLIC_SCHEME", "https")
    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert payload["address"] == "192.168.1.100"
    assert payload["identity"] is None
    assert payload["linuxVideoUrl"] == "https://3d.nrvous.io/api/v1/streams"


def test_info_payload_uses_runtime_ports_and_env_box_defaults(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setenv("MOONRAKER_URL", "http://127.0.0.1:7126")
    monkeypatch.setenv("LAN_CFS_NAME", "Dynamic CFS")
    monkeypatch.setenv("LAN_MATERIAL_NAME", "PETG")
    monkeypatch.setenv("LAN_MATERIAL_COLOR", "#12AB34")
    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert payload["moonrakerPort"] == 7126
    assert payload["fluiddPort"] == 80
    assert payload["mainsailPort"] == 80
    assert payload["boxsInfo"]["cfsName"] == "Dynamic CFS"
    assert payload["boxsInfo"]["materialBoxs"][0]["materials"][0]["name"] == "PETG"
    assert payload["boxsInfo"]["materialBoxs"][0]["materials"][0]["color"] == "#12AB34"


def test_info_payload_uses_live_printer_temperature_when_available(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {"case_fan_speed": 1200, "led_state": 1})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"heater_bed": {"temperature": 66.5, "target": 70.0}, "extruder": {"temperature": 210.0, "target": 220.0}, "print_stats": {"state": "printing"}})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert payload["temperature"]["nozzle"]["value"] == 210.0
    assert payload["temperature"]["nozzle"]["target"] == 220.0
    assert payload["temperature"]["bed"]["value"] == 66.5
    assert payload["temperature"]["bed"]["target"] == 70.0
    assert payload["ctrol"]["caseFan"] == 1200
    assert payload["ctrol"]["sideFan"] == 0
    assert payload["ctrol"]["ledSw"] == 1
    assert payload["ctrol"]["curFeedratePct"] == 100
    assert payload["data"]["nozzleTemp"] == 210.0
    assert payload["data"]["targetNozzleTemp"] == 220.0
    assert payload["data"]["bedTemp0"] == 66.5
    assert payload["data"]["targetBedTemp0"] == 70.0


def test_info_payload_uses_stream_base_for_preview_image(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setenv("PUBLIC_HOST", "3d.nrvous.io")
    monkeypatch.setenv("PUBLIC_SCHEME", "https")
    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert payload["previewimg"].startswith("data:image/svg+xml")


def test_detail_payload_uses_live_temperature_and_control_values(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {"case_fan_speed": 1200, "led_state": 1})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"heater_bed": {"temperature": 66.5, "target": 70.0}, "extruder": {"temperature": 210.0, "target": 220.0}, "print_stats": {"state": "printing"}})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_detail_payload()

    assert payload["result"]["temperature"]["nozzle"]["value"] == 210.0
    assert payload["result"]["temperature"]["bed"]["value"] == 66.5
    assert payload["result"]["data"]["nozzleTemp"] == 210.0
    assert payload["result"]["data"]["targetNozzleTemp"] == 220.0
    assert payload["result"]["data"]["bedTemp0"] == 66.5
    assert payload["result"]["data"]["targetBedTemp0"] == 70.0
    assert payload["result"]["ctrol"]["caseFan"] == 1200
    assert payload["result"]["ctrol"]["ledSw"] == 1


def test_upload_videos_payload_matches_client_media_expectations(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_public_address", lambda: "192.168.1.100")
    headers = Message()
    headers["Content-Length"] = "0"
    handler.headers = headers

    handler.serve_device_upload_videos("/api/cxy/v2/device/uploadVideos")

    payload = captured["payload"]
    videos = payload["result"]["list"]
    first_video = videos[0]
    assert first_video["deviceName"] == "192.168.1.100"
    assert first_video["media"]["url"].startswith("data:image")
    assert first_video["media"]["video"]["size"] >= 0
    assert first_video["media"]["video"]["duration"] == 0


def test_record_list_payload_exposes_list_and_history_list(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_public_address", lambda: "192.168.1.100")
    headers = Message()
    headers["Content-Length"] = "0"
    handler.headers = headers

    handler.serve_print_record_list("/api/cxy/v3/print/record/list")

    payload = captured["payload"]
    assert isinstance(payload["result"]["historyList"], list)
    assert isinstance(payload["result"]["list"], list)
    assert isinstance(payload["data"]["historyList"], list)
    assert isinstance(payload["data"]["list"], list)
    assert payload["result"]["historyList"][0]["deviceName"] == "192.168.1.100"


def test_info_payload_exposes_detail_screen_flags_and_filament_contract(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()
    compat_device = handler._build_compat_device_entry(payload, payload.get("state"), payload.get("deviceState"))

    assert payload["supportMultiple"] is True
    assert payload["machinePlatformMotionEnable"] == 1
    assert payload["materialDetector1"] == 1
    assert payload["filamentsList"][0]["cId"] == 1
    assert compat_device["supportMultiple"] is True
    assert compat_device["machinePlatformMotionEnable"] == 1
    assert compat_device["materialDetector1"] == 1
    assert compat_device["filamentsList"][0]["cId"] == 1


def test_boxs_info_payload_exposes_client_compatible_filament_shapes(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    boxs_info = handler._boxs_info_payload({"cfsName": "Lan Compat CFS", "material_name": "Material", "material_color": "#FF0000"})

    assert boxs_info["boxColorInfo"][0]["boxId"] == 1
    assert boxs_info["boxColorInfo"][0]["materialId"] == 1
    assert boxs_info["boxColorInfo"][0]["filamentName"] == "Material"
    assert boxs_info["same_material"][0][2][0]["boxId"] == 1
    assert boxs_info["same_material"][0][2][0]["materialId"] == 1


def test_compatibility_payloads_prefer_lan_address_over_local_fallback(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_public_address", lambda: "192.168.2.131")
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.2.131")
    monkeypatch.setattr(handler, "_preferred_printer_identity_address", lambda network=None: "192.168.1.100")
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "name": "K2 Plus",
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "K2 Plus",
        "machine_type": "K2 Plus",
        "address": None,
        "identity": None,
        "features": [],
        "status": {},
        "temperature": {},
        "boxsInfo": {},
        "boxConfig": {},
    })

    compat_device = handler._build_compat_device_entry()
    assert compat_device["address"] == "192.168.1.100"
    assert compat_device["identity"] == "192.168.1.100"

    protocal_payload = handler._build_protocal_payload()
    assert protocal_payload["address"] == "192.168.1.100"


def test_device_detail_uses_address_as_internal_device_name(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "mac": "aa:bb:cc:dd:ee:ff",
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "K2 Plus",
        "machine_type": "K2 Plus",
        "name": "K2 Plus",
        "address": "192.168.1.100",
        "identity": "192.168.1.100",
        "deviceType": 0,
        "video": True,
        "tbId": "tb-id",
        "keyFileToken": "token",
        "previewimg": "preview",
        "deviceImg": "img",
        "defaultDeviceImg": "./img/printerImgDefault.svg",
        "printerImagePath": "",
        "features": [],
        "linuxVideoUrl": "https://example/api/v1/streams",
        "webrtcSupport": True,
        "state": 0,
        "deviceState": 0,
        "uploadState": 0,
        "temperature": {},
        "status": {},
        "boxsInfo": {},
        "boxConfig": {},
        "supportMultiple": True,
        "machinePlatformMotionEnable": 1,
        "materialDetector1": 1,
        "filamentsList": [],
    })
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {"address": "192.168.1.100", "device": {}, "boxsInfo": {}, "boxConfig": {}, "status": {}, "temperature": {}}})
    monkeypatch.setattr(handler, "_public_address", lambda: "3d.nrvous.io")

    handler.serve_print_cluster_device_detail({
        "address": "192.168.1.100",
        "deviceName": "Friendly Name",
        "aliasName": "Friendly Name",
        "modelName": "K2 Plus",
    })

    payload = captured["payload"]
    assert payload["result"]["deviceName"] == "Friendly Name"
    assert payload["result"]["aliasName"] == "Friendly Name"
    assert payload["result"]["device"]["deviceName"] == "Friendly Name"


def test_device_detail_uses_dn_as_identity_fallback_when_address_is_missing(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "mac": "aa:bb:cc:dd:ee:ff",
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "Friendly Printer Name",
        "machine_type": "K2 Plus",
        "name": "Friendly Printer Name",
        "address": "3d.nrvous.io",
        "identity": "3d.nrvous.io",
        "deviceType": 0,
        "video": True,
        "tbId": "tb-id",
        "keyFileToken": "token",
        "previewimg": "preview",
        "deviceImg": "img",
        "defaultDeviceImg": "./img/printerImgDefault.svg",
        "printerImagePath": "",
        "features": [],
        "linuxVideoUrl": "https://example/api/v1/streams",
        "webrtcSupport": True,
        "state": 0,
        "deviceState": 0,
        "uploadState": 0,
        "temperature": {},
        "status": {},
        "boxsInfo": {},
        "boxConfig": {},
        "supportMultiple": True,
        "machinePlatformMotionEnable": 1,
        "materialDetector1": 1,
        "filamentsList": [],
    })
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {"address": "3d.nrvous.io", "device": {}, "boxsInfo": {}, "boxConfig": {}, "status": {}, "temperature": {}}})
    monkeypatch.setattr(handler, "_public_address", lambda: "3d.nrvous.io")

    handler.serve_print_cluster_device_detail({"dn": "192.168.1.100", "deviceName": "Friendly Printer Name", "aliasName": "Friendly Printer Name", "modelName": "K2 Plus"})

    payload = captured["payload"]
    assert payload["result"]["deviceName"] == "Friendly Printer Name"
    assert payload["result"]["address"] == "192.168.1.100"
    assert payload["result"]["identity"] == "192.168.1.100"
    assert payload["result"]["device"]["address"] == "192.168.1.100"


def test_iotrouter_rpc_accepts_control_params_for_detail_screen_controls(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    handler.serve_iotrouter_rpc("/api/rest/iotrouter/rpc/twoway/abc", {"params": {"lightSw": 1, "ledSw": 1, "setPosition": "X10 F3000"}})

    payload = captured["payload"]
    assert payload["code"] == 0
    assert payload["result"]["lightSw"] == 1
    assert payload["result"]["ledSw"] == 1
    assert payload["result"]["setPosition"] == "X10 F3000"


def test_machine_info_route_returns_compatibility_payload(monkeypatch, tmp_path):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}
    identity_state_path = tmp_path / "identity_state.json"

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_identity_state_path", lambda: str(identity_state_path))

    handler.serve_machine_info()

    payload = captured["payload"]
    assert payload["model"] == "K2 Plus"
    assert payload["machine_name"] == "K2 Plus"
    assert payload["address"] == "192.168.1.100"


def test_iotrouter_file_list_response_returns_array(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_iotrouter_rpc("/api/rest/iotrouter/rpc/oneway/test", {"method": "get", "params": {"pFileList": 1, "onePageNum": 2}})

    payload = captured["payload"]
    assert isinstance(payload["result"]["pFileList"], list)
    assert payload["result"]["pFileList"][0]["filename"].startswith("lan-compat")
    assert payload["result"]["fileList"][0]["filename"].startswith("lan-compat")


def test_iotrouter_rpc_returns_cfs_status_payload(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    handler.serve_iotrouter_rpc("/api/rest/iotrouter/rpc/twoway/abc", {"params": {"cfsStatus": 1}})

    payload = captured["payload"]
    assert payload["result"]["cfsName"] == "Lan Compat CFS"
    assert payload["result"]["cfsStatus"]["online"] is True
    assert payload["result"]["cfsStatus"]["state"] == 0
    assert payload["result"]["cfsStatus"]["boxsInfo"]["cfsName"] == "Lan Compat CFS"


def test_iotrouter_rpc_cfs_list_payloads_expose_material_names_and_colors(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "boxsInfo": {
            "same_material": [],
            "color_same_material": [],
            "boxColorInfo": [],
            "materialBoxs": [{"id": 1, "materials": [{"id": 1, "cId": 1, "name": "PLA", "color": "#FF0000", "type": "PLA", "selected": True, "percent": 100, "remaining_length": 1000000, "state": 1}]}],
            "cfsName": "Lan Compat CFS",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })

    handler.serve_iotrouter_rpc("/api/rest/iotrouter/rpc/twoway/abc", {"params": {"cfsList": 1}})

    payload = captured["payload"]
    material = payload["result"]["cfsList"][0]["portList"][0]
    assert material["name"] == "PLA"
    assert material["color"] == "#FF0000"
    assert material["filamentType"] == "PLA"


def test_iotrouter_rpc_returns_filament_info_for_material_lookup(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "boxsInfo": {
            "same_material": [],
            "color_same_material": [],
            "boxColorInfo": [{"boxId": 1, "materialId": 1, "color": "#FF0000", "filamentName": "Material", "filamentType": "PLA"}],
            "materialBoxs": [{"id": 1, "materials": [{"id": 1, "cId": 1, "name": "PLA", "color": "#FF0000", "type": "PLA", "selected": True, "percent": 100, "remaining_length": 1000000, "state": 1}]}],
            "cfsName": "Lan Compat CFS",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })

    handler.serve_iotrouter_rpc("/api/rest/iotrouter/rpc/twoway/abc", {"params": {"cId": 1}})

    payload = captured["payload"]
    assert payload["code"] == 0
    assert payload["result"]["cId"] == 1
    assert payload["result"]["name"] == "PLA"
    assert payload["result"]["filamentType"] == "PLA"
    assert payload["result"]["nozzleTempMax"] == 220
    assert payload["result"]["nozzleTempMin"] == 0


def test_get_device_count_route_returns_single_device_count(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_get_device_count()

    payload = captured["payload"]
    assert payload["result"] == 1
    assert payload["count"] == 1


def test_poll_state_route_preserves_model_and_identity_fields(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "name": "Friendly Printer Name",
        "machine_name": "Friendly Printer Name",
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_type": "K2 Plus",
        "address": "192.168.1.100",
        "identity": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "features": ["videoInfo.video", "printControl.xyzControl001005010"],
        "tbId": "lan-compat-tb-id",
        "videoToken": "lan-compat-video-token",
        "boxsInfo": {"cfsName": "MF049"},
        "boxConfig": {"cAutoFeed": 1},
        "temperature": {},
        "status": {},
        "streamState": {"active": True, "source": "webcam"},
        "cameraState": {"enabled": True, "state": "ready"},
        "recordState": {"recording": False, "timelapse": False},
        "state": 0,
        "deviceState": 0,
        "uploadState": 0,
    })

    handler.serve_poll_state()

    payload = captured["payload"]
    printer = payload["result"][0]
    assert printer["deviceName"] == "192.168.1.100"
    assert printer["aliasName"] == "Friendly Printer Name"
    assert printer["name"] == "Friendly Printer Name"
    assert printer["model"] == "K2 Plus"
    assert printer["modelName"] == "K2 Plus"
    assert printer["machine_name"] == "Friendly Printer Name"
    assert printer["machine_type"] == "K2 Plus"
    assert "videoInfo.video" in printer["features"]
    assert "printControl.xyzControl001005010" in printer["features"]


def test_compat_device_entry_keeps_display_name_separate_from_model(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    compat_device = handler._build_compat_device_entry({
        "name": "Friendly Printer Name",
        "machine_name": "Friendly Printer Name",
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "address": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
    }, 0, 0)

    assert compat_device["deviceName"] == "192.168.1.100"
    assert compat_device["aliasName"] == "Friendly Printer Name"
    assert compat_device["name"] == "Friendly Printer Name"
    assert compat_device["model"] == "K2 Plus"
    assert compat_device["modelName"] == "K2 Plus"


def test_get_devices_route_returns_single_payload_with_result_and_webview_printer_list(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = []

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.append(payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "name": "Friendly Printer Name",
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "Friendly Printer Name",
        "machine_type": "K2 Plus",
        "address": "192.168.1.100",
        "identity": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "tbId": "lan-compat-tb-id",
        "videoToken": "lan-compat-video-token",
        "previewimg": "https://example.test/current.png",
        "deviceImg": "./img/machine/K2 Plus.png",
        "defaultDeviceImg": "./img/printerImgDefault.svg",
        "printerImagePath": "/tmp/k2_plus.png",
        "features": ["videoInfo.video"],
        "linuxVideoUrl": "https://example.test/api/v1/streams",
        "webrtcSupport": True,
        "connectType": 1001,
        "isLanPrinter": True,
        "lanCompatible": True,
        "oldPrinter": False,
        "boxsInfo": {"cfsName": "MF049"},
        "boxConfig": {"cAutoFeed": 1},
        "temperature": {},
        "status": {},
        "streamState": {"active": True, "source": "webcam"},
        "cameraState": {"enabled": True, "state": "ready"},
        "recordState": {"recording": False, "timelapse": False},
        "state": 0,
        "deviceState": 0,
        "uploadState": 0,
    })

    handler.serve_get_devices()

    assert len(captured) == 1
    payload = captured[0]
    assert payload["result"]["multi_printer_info"][0]["address"] == "192.168.1.100"
    printer = payload["data"]["printerList"][0]["list"][0]
    assert printer["name"] == "192.168.1.100"
    assert printer["deviceName"] == "192.168.1.100"
    assert printer["model"] == "K2 Plus"
    assert printer["modelName"] == "K2 Plus"
    assert printer["machine_name"] == "Friendly Printer Name"
    assert printer["machine_type"] == "K2 Plus"
    assert printer["model_name"] == "K2 Plus"
    assert payload["data"]["currentActivePrinterMac"] == "00:11:22:33:44:55"


def test_build_device_identity_fields_keeps_model_from_info_payload_when_display_name_is_present(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    identity_fields = handler._build_device_identity_fields(
        {"name": "Friendly Printer Name", "machine_name": "Friendly Printer Name", "model": "K2 Plus", "modelName": "K2 Plus", "machine_type": "K2 Plus"},
        {"deviceName": "Friendly Printer Name"},
    )

    assert identity_fields["display_name"] == "Friendly Printer Name"
    assert identity_fields["model"] == "K2 Plus"
    assert identity_fields["machine_name"] == "Friendly Printer Name"
    assert identity_fields["machine_type"] == "K2 Plus"


def test_direct_ip_requests_prefer_request_host_over_public_host(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setenv("PUBLIC_HOST", "3d.nrvous.io")
    monkeypatch.setenv("PUBLIC_SCHEME", "https")
    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    headers = Message()
    headers["Host"] = "192.168.1.100"
    headers["X-Forwarded-Proto"] = "http"
    handler.headers = headers

    payload = handler._build_info_payload()
    assert payload["address"] == "192.168.1.100"
    assert payload["linuxVideoUrl"] == "http://192.168.1.100/api/v1/streams"


def test_loopback_requests_fallback_to_lan_ip_when_public_host_is_configured(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setenv("PUBLIC_HOST", "3d.nrvous.io")
    monkeypatch.setenv("PUBLIC_SCHEME", "https")
    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {"eth0": {"ip_addresses": [{"address": "192.168.1.100", "family": 4}]}}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    headers = Message()
    headers["Host"] = "127.0.0.1"
    headers["X-Forwarded-Proto"] = "http"
    handler.headers = headers

    payload = handler._build_info_payload()
    assert payload["address"] == "192.168.1.100"
    assert payload["identity"] is None


def test_multi_machine_uses_public_host_for_stream_urls(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setenv("PUBLIC_HOST", "3d.nrvous.io")
    monkeypatch.setenv("PUBLIC_SCHEME", "https")
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_fetch_json", lambda path, timeout=0: {"result": {"system_info": {"network": {"eth0": {"ip_addresses": [{"address": "192.168.1.100", "family": 4}]}}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    handler.serve_multi_machine()

    printer = captured["payload"]["result"]["multi_printer_info"][0]
    assert printer["linuxVideoUrl"] == "https://3d.nrvous.io/api/v1/streams"


def test_multi_machine_payload_keeps_image_and_stream_metadata(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_fetch_json", lambda path, timeout=0: {"result": {"system_info": {"network": {"eth0": {"ip_addresses": [{"address": "192.168.1.100", "family": 4}]}}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus", "printer_image_path": "/tmp/k2_plus.png"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "K2 Plus",
        "machine_type": "K2 Plus",
        "name": "K2 Plus",
        "address": "192.168.1.100",
        "identity": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "previewimg": "https://example.test/current.png",
        "deviceImg": "./img/machine/K2 Plus.png",
        "defaultDeviceImg": "./img/printerImgDefault.svg",
        "printerImagePath": "/tmp/k2_plus.png",
        "features": ["videoInfo.video", "printControl.xyzControl001005010"],
        "linuxVideoUrl": "https://example.test/api/v1/streams",
        "webrtcSupport": True,
        "state": 0,
        "deviceState": 0,
        "uploadState": 0,
        "temperature": {"nozzle": {"value": 0.0, "target": 0.0}, "bed": {"value": 0.0, "target": 0.0}},
        "status": {"state": "standby", "display_status": {"progress": 0.0}},
    })

    handler.serve_multi_machine()

    printer = captured["payload"]["result"]["multi_printer_info"][0]
    assert printer["previewimg"] == "https://example.test/current.png"
    assert printer["deviceImg"] == "./img/machine/K2 Plus.png"
    assert printer["defaultDeviceImg"] == "./img/printerImgDefault.svg"
    assert printer["printerImagePath"] == "/tmp/k2_plus.png"
    assert printer["features"] == ["videoInfo.videoEncryption", "videoInfo.video", "printControl.xyzControl001005010"]
    assert printer["linuxVideoUrl"] == "https://example.test/api/v1/streams"
    assert printer["webrtcSupport"] is True


def test_print_cluster_device_detail_preserves_model_from_info_payload_during_add_flow(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "K2 Plus",
        "machine_type": "K2 Plus",
        "name": "K2 Plus",
        "address": "192.168.1.100",
        "identity": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "boxsInfo": {
            "same_material": [],
            "boxColorInfo": [{"boxType": 0, "color": "#000000", "material": "PLA", "id": 1, "name": "MF049"}],
            "materialBoxs": [{"id": 1, "name": "MF049", "type": 0, "materials": []}],
            "cfsName": "MF049",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {}})
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {"deviceName": "My LAN Printer"})

    handler.serve_print_cluster_device_detail({})

    payload = captured["payload"]
    assert payload["result"]["deviceName"] == "My LAN Printer"
    assert payload["result"]["model"] == "K2 Plus"
    assert payload["result"]["modelName"] == "K2 Plus"
    assert payload["result"]["machine_type"] == "K2 Plus"
    assert payload["result"]["device"]["modelName"] == "K2 Plus"


def test_print_cluster_device_detail_payload_preserves_machine_identity_fields(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "Friendly Printer Name",
        "machine_type": "K2 Plus",
        "name": "Friendly Printer Name",
        "address": "192.168.1.100",
        "identity": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "boxsInfo": {
            "same_material": [],
            "boxColorInfo": [{"boxType": 0, "color": "#000000", "material": "PLA", "id": 1, "name": "MF049"}],
            "materialBoxs": [{"id": 1, "name": "MF049", "type": 0, "materials": []}],
            "cfsName": "MF049",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_print_cluster_device_detail({"deviceName": "Friendly Printer Name", "modelName": "K2 Plus"})

    payload = captured["payload"]
    assert payload["result"]["deviceName"] == "Friendly Printer Name"
    assert payload["result"]["machine_name"] == "Friendly Printer Name"
    assert payload["result"]["machine_type"] == "K2 Plus"
    assert payload["result"]["model_name"] == "K2 Plus"


def test_print_cluster_device_detail_payload_includes_print_info_and_box_metadata(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "K2 Plus",
        "machine_type": "K2 Plus",
        "name": "K2 Plus",
        "address": "192.168.1.100",
        "identity": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "boxsInfo": {
            "same_material": [],
            "boxColorInfo": [{"boxType": 0, "color": "#000000", "material": "PLA", "id": 1, "name": "MF049"}],
            "materialBoxs": [{"id": 1, "name": "MF049", "type": 0, "materials": []}],
            "cfsName": "MF049",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_print_cluster_device_detail({"dn": "192.168.1.100"})

    payload = captured["payload"]
    assert payload["code"] == 0
    assert payload["result"]["deviceName"] == "K2 Plus"
    assert payload["result"]["aliasName"] == "K2 Plus"
    assert payload["result"]["state"] == 0
    assert payload["result"]["deviceState"] == 0
    assert payload["result"]["ctrol"]["autohome"] == "X:0 Y:0 Z:0"
    assert payload["result"]["boxsInfo"]["cfsName"] == "MF049"
    assert payload["result"]["boxConfig"]["cAutoFeed"] == 1
    assert payload["result"]["printInfo"]["model"] == "K2 Plus"
    assert payload["result"]["printInfo"]["deviceName"] == "K2 Plus"


def test_print_cluster_device_detail_persists_request_state_for_followup_payloads(monkeypatch, tmp_path):
    handler = ProbeHandler.__new__(ProbeHandler)
    identity_state_path = tmp_path / "identity_state.json"

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_identity_state_path", lambda: str(identity_state_path))
    monkeypatch.setattr(handler, "_send_json", lambda payload: None)

    handler.serve_print_cluster_device_detail({
        "deviceName": "My LAN Printer",
        "aliasName": "My LAN Printer",
        "modelName": "K2 Plus",
        "cameraState": {"enabled": True, "state": "ready"},
        "recordState": {"recording": True, "timelapse": True},
        "streamState": {"active": True, "source": "webcam"},
        "record": {"timelapse": True, "video": True, "camera": True, "recording": False, "state": "done"},
    })

    payload = handler._build_info_payload()
    assert payload["deviceName"] == "192.168.1.100"
    assert payload["name"] == "My LAN Printer"
    assert payload["aliasName"] == "My LAN Printer"
    assert payload["cameraState"]["state"] == "ready"
    assert payload["recordState"]["timelapse"] is True
    assert payload["streamState"]["source"] == "webcam"


def test_detail_payload_forces_lan_shape_even_when_info_looks_cloud_like(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "K2 Plus",
        "machine_type": "K2 Plus",
        "name": "K2 Plus",
        "address": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 1,
        "type": 1,
        "video": True,
        "features": ["videoInfo.videoEncryption", "videoInfo.video"],
        "linuxVideoUrl": "http://192.168.1.100:8000/api/v1/streams",
        "webrtcSupport": True,
        "connectType": 1,
        "identity": "cloud-identity",
        "isLanPrinter": True,
        "lanCompatible": True,
        "oldPrinter": False,
        "state": 1,
        "deviceState": 1,
        "uploadState": 0,
        "localOnline": True,
        "cloudOnline": False,
        "temperature": {"nozzle": {"value": 0.0, "target": 0.0}, "bed": {"value": 0.0, "target": 0.0}},
        "status": {"state": "standby", "display_status": {"progress": 0.0}},
    })

    payload = handler._build_detail_payload()

    result = payload["result"]
    assert result["deviceType"] == 0
    assert result["type"] == 0
    assert result["connectType"] == 1001
    assert result["identity"] is None
    assert result["isLanPrinter"] is True
    assert result["lanCompatible"] is True
    assert result["oldPrinter"] is False
    assert result["device"]["deviceType"] == 0
    assert result["device"]["type"] == 0
    assert result["device"]["connectType"] == 1001
    assert result["device"]["identity"] is None


def test_creality_status_payload_matches_active_lan_shape(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "K2 Plus",
        "machine_type": "K2 Plus",
        "name": "K2 Plus",
        "address": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "features": ["videoInfo.videoEncryption", "videoInfo.video"],
        "linuxVideoUrl": "http://192.168.1.100:8000/api/v1/streams",
        "webrtcSupport": True,
        "connectType": 1001,
        "state": 1,
        "deviceState": 1,
        "uploadState": 0,
        "localOnline": True,
        "cloudOnline": False,
        "temperature": {"nozzle": {"value": 0.0, "target": 0.0}, "bed": {"value": 0.0, "target": 0.0}},
        "status": {"state": "standby", "display_status": {"progress": 0.0}},
    })

    payload = handler._build_detail_payload()

    result = payload["result"]
    assert result["state"] == 1
    assert result["deviceState"] == 1
    assert result["uploadState"] == 0
    assert result["temperature"]["nozzle"]["value"] == 0.0
    assert result["status"]["state"] == "standby"
    assert result["device"]["state"] == 1
    assert result["device"]["deviceState"] == 1


def test_detail_payload_keeps_image_and_stream_metadata_on_result_shape(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "K2 Plus",
        "machine_type": "K2 Plus",
        "name": "K2 Plus",
        "address": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "previewimg": "https://example.test/current.png",
        "deviceImg": "./img/machine/K2 Plus.png",
        "defaultDeviceImg": "./img/printerImgDefault.svg",
        "printerImagePath": "/tmp/k2_plus.png",
        "features": ["videoInfo.video", "printControl.xyzControl001005010"],
        "linuxVideoUrl": "https://example.test/api/v1/streams",
        "webrtcSupport": True,
        "identity": "192.168.1.100",
        "state": 1,
        "deviceState": 1,
        "uploadState": 0,
        "temperature": {"nozzle": {"value": 0.0, "target": 0.0}, "bed": {"value": 0.0, "target": 0.0}},
        "status": {"state": "standby", "display_status": {"progress": 0.0}},
    })

    payload = handler._build_detail_payload()
    result = payload["result"]

    assert result["previewimg"] == "https://example.test/current.png"
    assert result["deviceImg"] == "./img/machine/K2 Plus.png"
    assert result["defaultDeviceImg"] == "./img/printerImgDefault.svg"
    assert result["printerImagePath"] == "/tmp/k2_plus.png"
    assert result["features"] == ["videoInfo.videoEncryption", "videoInfo.video", "printControl.xyzControl001005010"]
    assert result["linuxVideoUrl"] == "https://example.test/api/v1/streams"
    assert result["webrtcSupport"] is True
    assert result["device"]["previewimg"] == "https://example.test/current.png"


def test_detail_payload_exposes_boxsinfo_on_top_level_result(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "K2 Plus",
        "machine_type": "K2 Plus",
        "name": "K2 Plus",
        "address": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "previewimg": "https://example.test/current.png",
        "deviceImg": "./img/machine/K2 Plus.png",
        "defaultDeviceImg": "./img/printerImgDefault.svg",
        "printerImagePath": "/tmp/k2_plus.png",
        "features": ["videoInfo.video", "printControl.xyzControl001005010"],
        "linuxVideoUrl": "https://example.test/api/v1/streams",
        "webrtcSupport": True,
        "identity": "192.168.1.100",
        "state": 1,
        "deviceState": 1,
        "uploadState": 0,
        "temperature": {"nozzle": {"value": 0.0, "target": 0.0}, "bed": {"value": 0.0, "target": 0.0}},
        "status": {"state": "standby", "display_status": {"progress": 0.0}},
        "boxsInfo": {
            "same_material": [],
            "color_same_material": [],
            "boxColorInfo": [{"boxType": 0, "color": "#000000", "material": "PLA", "id": 1, "name": "MF049"}],
            "materialBoxs": [{"id": 1, "name": "MF049", "type": 0, "materials": []}],
            "cfsName": "MF049",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })

    payload = handler._build_detail_payload()
    result = payload["result"]

    assert result["boxsInfo"]["cfsName"] == "MF049"
    assert result["boxConfig"]["autoRefill"] == 1
    assert result["device"]["boxsInfo"]["cfsName"] == "MF049"


def test_detail_payload_exposes_media_hydration_lists_for_detail_screen(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "Friendly Printer Name",
        "machine_type": "K2 Plus",
        "name": "Friendly Printer Name",
        "address": "192.168.1.100",
        "identity": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "features": [],
        "linuxVideoUrl": "https://example/api/v1/streams",
        "webrtcSupport": True,
        "state": 0,
        "deviceState": 0,
        "uploadState": 0,
        "temperature": {},
        "status": {},
        "boxsInfo": {},
        "boxConfig": {},
        "cameraState": {"enabled": True, "state": "ready"},
        "recordState": {"recording": False, "timelapse": False},
        "streamState": {"active": True, "source": "webcam"},
        "record": {"timelapse": True, "video": True, "camera": True, "recording": False, "state": "done"},
    })
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {}})
    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_print_cluster_device_detail({"dn": "192.168.1.100"})

    payload = captured["payload"]["result"]
    assert payload["record"]["timelapse"] is True
    assert payload["record"]["cameraState"]["state"] == "ready"
    assert payload["historyList"][0]["recordId"].startswith("record-")
    assert payload["pFileList"][0]["filename"].startswith("lan-compat")


def test_multi_machine_payload_exposes_boxsinfo_and_boxconfig(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_fetch_json", lambda path, timeout=0: {"result": {"system_info": {"network": {"eth0": {"ip_addresses": [{"address": "192.168.1.100", "family": 4}]}}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "K2 Plus",
        "machine_type": "K2 Plus",
        "name": "K2 Plus",
        "address": "192.168.1.100",
        "identity": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "previewimg": "https://example.test/current.png",
        "deviceImg": "./img/machine/K2 Plus.png",
        "defaultDeviceImg": "./img/printerImgDefault.svg",
        "printerImagePath": "/tmp/k2_plus.png",
        "features": ["videoInfo.video", "printControl.xyzControl001005010"],
        "linuxVideoUrl": "https://example.test/api/v1/streams",
        "webrtcSupport": True,
        "state": 0,
        "deviceState": 0,
        "uploadState": 0,
        "temperature": {"nozzle": {"value": 0.0, "target": 0.0}, "bed": {"value": 0.0, "target": 0.0}},
        "status": {"state": "standby", "display_status": {"progress": 0.0}},
        "boxsInfo": {
            "same_material": [],
            "color_same_material": [],
            "boxColorInfo": [{"boxType": 0, "color": "#000000", "material": "PLA", "id": 1, "name": "MF049"}],
            "materialBoxs": [{"id": 1, "name": "MF049", "type": 0, "materials": []}],
            "cfsName": "MF049",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })

    handler.serve_multi_machine()

    printer = captured["payload"]["result"]["multi_printer_info"][0]
    assert printer["boxsInfo"]["cfsName"] == "MF049"
    assert printer["boxConfig"]["autoRefill"] == 1


def test_multi_machine_payload_exposes_control_state_shape(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_fetch_json", lambda path, timeout=0: {"result": {"system_info": {"network": {"eth0": {"ip_addresses": [{"address": "192.168.1.100", "family": 4}]}}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    handler.serve_multi_machine()

    printer = captured["payload"]["result"]["multi_printer_info"][0]
    assert printer["ctrol"]["autohome"] == "X:0 Y:0 Z:0"
    assert printer["ctrol"]["curPosition"] == "X:1 Y:1 Z:1"
    assert printer["ctrol"]["curFeedratePct"] == 100


def test_multi_machine_payload_exposes_nested_device_shape_for_box_metadata(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_fetch_json", lambda path, timeout=0: {"result": {"system_info": {"network": {"eth0": {"ip_addresses": [{"address": "192.168.1.100", "family": 4}]}}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "model": "K2 Plus",
        "modelName": "K2 Plus",
        "machine_name": "K2 Plus",
        "machine_type": "K2 Plus",
        "name": "K2 Plus",
        "address": "192.168.1.100",
        "identity": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "deviceType": 0,
        "video": True,
        "previewimg": "https://example.test/current.png",
        "deviceImg": "./img/machine/K2 Plus.png",
        "defaultDeviceImg": "./img/printerImgDefault.svg",
        "printerImagePath": "/tmp/k2_plus.png",
        "features": ["videoInfo.video", "printControl.xyzControl001005010"],
        "linuxVideoUrl": "https://example.test/api/v1/streams",
        "webrtcSupport": True,
        "state": 0,
        "deviceState": 0,
        "uploadState": 0,
        "temperature": {"nozzle": {"value": 0.0, "target": 0.0}, "bed": {"value": 0.0, "target": 0.0}},
        "status": {"state": "standby", "display_status": {"progress": 0.0}},
        "boxsInfo": {
            "same_material": [],
            "color_same_material": [],
            "boxColorInfo": [{"boxType": 0, "color": "#000000", "material": "PLA", "id": 1, "name": "MF049"}],
            "materialBoxs": [{"id": 1, "name": "MF049", "type": 0, "materials": []}],
            "cfsName": "MF049",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })

    handler.serve_multi_machine()

    printer = captured["payload"]["result"]["multi_printer_info"][0]
    assert printer["boxsInfo"]["cfsName"] == "MF049"
    assert printer["boxConfig"]["autoRefill"] == 1
    assert printer["device"]["boxsInfo"]["cfsName"] == "MF049"
    assert printer["device"]["boxConfig"]["autoRefill"] == 1
    assert printer["device"]["previewimg"] == "https://example.test/current.png"


def test_multi_machine_payload_uses_live_control_state(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_fetch_json", lambda path, timeout=0: {"result": {"system_info": {"network": {"eth0": {"ip_addresses": [{"address": "192.168.1.100", "family": 4}]}}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {"case_fan_speed": 42, "side_fan_speed": 58, "chamber_temp": 31.5, "chamber_temp_target": 37.0, "led_state": 1})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    handler.serve_multi_machine()

    printer = captured["payload"]["result"]["multi_printer_info"][0]
    assert printer["caseFan"] == 42
    assert printer["caseFanPct"] == 42
    assert printer["sideFan"] == 58
    assert printer["sideFanPct"] == 58
    assert printer["chamberTemp"] == 31.5
    assert printer["chamberTempTarget"] == 37.0
    assert printer["ledSw"] == 1
    assert printer["ctrol"]["caseFan"] == 42
    assert printer["ctrol"]["sideFanPct"] == 58
    assert printer["ctrol"]["chamberTempTarget"] == 37.0
    assert printer["ctrol"]["ledSw"] == 1


def test_get_devices_endpoint_returns_identity_and_state_shape(monkeypatch, tmp_path):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}
    identity_state_path = tmp_path / "identity_state.json"

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_fetch_json", lambda path, timeout=0: {"result": {"system_info": {"network": {"eth0": {"ip_addresses": [{"address": "192.168.1.100", "family": 4}]}}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus", "printer_id": 7, "moonraker_port": 7125, "fluidd_port": 80, "mainsail_port": 80, "printer_image_path": "/tmp/k2_plus.png"})
    monkeypatch.setattr(handler, "_fetch_live_state", lambda timeout=0: {"case_fan_speed": 11, "side_fan_speed": 22, "chamber_temp": 31.5, "chamber_temp_target": 35.0, "led_state": 1})
    monkeypatch.setattr(handler, "_fetch_printer_status", lambda timeout=0: {"state": "standby", "display_status": {"progress": 0.0}, "print_stats": {"state": "standby"}, "heater_bed": {"temperature": 0.0, "target": 0.0}, "extruder": {"temperature": 0.0, "target": 0.0}, "gcode_move": {"speed_factor": 1.0}, "_status_available": True})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")
    monkeypatch.setattr(handler, "_public_address", lambda: "3d.nrvous.io")
    monkeypatch.setattr(handler, "_stream_base_url", lambda: "https://3d.nrvous.io")
    monkeypatch.setattr(handler, "_identity_state_path", lambda: str(identity_state_path))

    handler.serve_get_devices()

    printer = captured["payload"]["result"]["multi_printer_info"][0]
    assert printer["deviceName"] == "192.168.1.100"
    assert printer["aliasName"] == "K2 Plus"
    assert printer["tbId"] == "lan-compat-tb-id"
    assert printer["keyFileToken"] == "lan-compat-key-token"
    assert printer["videoToken"] == "lan-compat-video-token"
    assert printer["streamState"]["active"] is True
    assert printer["cameraState"]["enabled"] is True
    assert printer["recordState"]["recording"] is False
    assert printer["localOnline"] is True
    assert printer["cloudOnline"] is False
    assert printer["cxyOnline"] is False
    assert printer["isExistInLocal"] is True
    assert printer["isExistInCxy"] is False
    assert printer["device"]["deviceName"] == "192.168.1.100"
    assert printer["device"]["aliasName"] == "K2 Plus"
    assert printer["device"]["tbId"] == "lan-compat-tb-id"


def test_lan_printer_payload_avoids_encrypted_video_feature(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert "videoInfo.videoEncryption" in payload["features"]
    assert "videoInfo.video" in payload["features"]
    assert "printControl.xyzControl001005010" in payload["features"]


def test_info_payload_exposes_control_state_shape_for_detail_screen(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert payload["ctrol"]["autohome"] == "X:0 Y:0 Z:0"
    assert payload["ctrol"]["curPosition"] == "X:1 Y:1 Z:1"
    assert payload["ctrol"]["curFeedratePct"] == 100


def test_info_payload_exposes_full_lan_control_fields(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert payload["sideFanPct"] == 0
    assert payload["chamberTemp"] == 0.0
    assert payload["chamberTempTarget"] == 0.0
    assert payload["ledSw"] == 0
    assert payload["lightSw"] == 0
    assert payload["ctrol"]["sideFanPct"] == 0
    assert payload["ctrol"]["chamberTemp"] == 0.0
    assert payload["ctrol"]["chamberTempTarget"] == 0.0
    assert payload["ctrol"]["ledSw"] == 0
    assert payload["ctrol"]["lightSw"] == 0


def test_info_payload_exposes_detail_page_metadata_fields(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus", "printer_image_path": "/tmp/k2_plus.png"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_info_payload()

    assert payload["printerImagePath"] == "/tmp/k2_plus.png"
    assert payload["deviceImg"].endswith("/K2 Plus.png")
    assert payload["defaultDeviceImg"] == "./img/printerImgDefault.svg"
    assert payload["identity"] is None
    assert payload["boxsInfo"]["materialBoxs"][0]["materials"][0]["color"] == "#FF0000"
    assert payload["boxConfig"]["autoRefill"] == 1
    assert payload["boxConfig"]["ignoreColorAutoFeed"] == 0


def test_detail_payload_exposes_full_lan_control_fields_on_device_shape(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_detail_payload()

    assert payload["result"]["device"]["sideFanPct"] == 0
    assert payload["result"]["device"]["chamberTemp"] == 0.0
    assert payload["result"]["device"]["chamberTempTarget"] == 0.0
    assert payload["result"]["device"]["ledSw"] == 0
    assert payload["result"]["device"]["ctrol"]["sideFanPct"] == 0
    assert payload["result"]["device"]["ctrol"]["ledSw"] == 0
    assert payload["result"]["autohome"] == "X:0 Y:0 Z:0"
    assert payload["result"]["curPosition"] == "X:1 Y:1 Z:1"
    assert payload["result"]["curFeedratePct"] == 100


def test_protocal_payload_includes_old_printer_state_fields(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)

    monkeypatch.setattr(handler, "_fetch_json", lambda *args, **kwargs: {"result": {"system_info": {"network": {}}}})
    monkeypatch.setattr(handler, "_fetch_moonraker_info", lambda timeout=0: {"machine_name": "K2 Plus", "machine_type": "K2 Plus"})
    monkeypatch.setattr(handler, "_guess_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(handler, "_guess_mac", lambda: "00:11:22:33:44:55")

    payload = handler._build_protocal_payload()

    assert payload["ssid"].endswith("00:11:22:33:44:55")
    assert payload["connect"] == 1
    assert payload["nozzleTemp"] == 0.0
    assert payload["bedTemp"] == 0.0
    assert payload["nozzleTemp2"] == 0.0
    assert payload["bedTemp2"] == 0.0
    assert payload["printProgress"] == 0.0
    assert payload["curFeedratePct"] == 100
    assert payload["video"] is True
    assert payload["linuxVideoUrl"].startswith(("http://", "https://"))


def test_print_cancel_routes_return_safe_success_without_forwarding(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    handler.wfile = io.BytesIO()
    handler.send_response = lambda status: captured.setdefault("status", status)
    handler.send_header = lambda name, value: captured.setdefault(name, value)
    handler.end_headers = lambda: None
    monkeypatch.setattr(handler, "_forward_print_cancel_to_moonraker", lambda path: (b'{"error": {"code": 400, "message": "shutdown"}}', 400))

    handler.serve_print_cancel("/printer/print/cancel")

    body = handler.wfile.getvalue().decode("utf-8")
    assert captured["status"] == 200
    assert json.loads(body)["result"] == "ok"


def test_cxy_iotrouter_rpc_route_returns_stock_client_shape(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "boxsInfo": {
            "same_material": [],
            "color_same_material": [],
            "boxColorInfo": [{"boxType": 0, "color": "#FF0000", "id": 1, "name": "MF049"}],
            "materialBoxs": [{"id": 1, "name": "MF049", "type": 0, "materials": [{"id": 1, "name": "PLA", "type": 0, "color": "#FF0000", "state": 1, "percent": 100, "remaining_length": 1000000}]}],
            "cfsName": "MF049",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })

    handler.serve_iotrouter_rpc("/api/cxy/v2/iotrouter/rpc", {"method": "get", "params": {"cfsList": 1}})

    payload = captured["payload"]
    assert payload["code"] == 0
    assert payload["result"]["cfsName"] == "MF049"


def test_rpc_twoway_cfs_route_returns_stock_client_shape(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "boxsInfo": {
            "same_material": [],
            "color_same_material": [],
            "boxColorInfo": [{"boxType": 0, "color": "#FF0000", "id": 1, "name": "MF049"}],
            "materialBoxs": [{"id": 1, "name": "MF049", "type": 0, "materials": [{"id": 1, "name": "PLA", "type": 0, "color": "#FF0000", "state": 1, "percent": 100, "remaining_length": 1000000}]}],
            "cfsName": "MF049",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })

    handler.serve_iotrouter_rpc("/api/rest/iotrouter/rpc/twoway/abc123", {"method": "get", "params": {"cfsList": 1}})

    payload = captured["payload"]
    assert payload["code"] == 0
    assert payload["result"]["cfsName"] == "MF049"
    assert payload["result"]["cfsList"][0]["portList"][0]["filamentsColor"] == "#FF0000"


def test_rpc_twoway_box_config_route_returns_box_config_shape(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "boxsInfo": {
            "same_material": [],
            "color_same_material": [],
            "boxColorInfo": [{"boxType": 0, "color": "#FF0000", "id": 1, "name": "MF049"}],
            "materialBoxs": [{"id": 1, "name": "MF049", "type": 0, "materials": [{"id": 1, "name": "PLA", "type": 0, "color": "#FF0000", "state": 1, "percent": 100, "remaining_length": 1000000}]}],
            "cfsName": "MF049",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })

    handler.serve_iotrouter_rpc("/api/rest/iotrouter/rpc/twoway/abc123", {"method": "get", "params": {"cfsInfo": 1}})

    payload = captured["payload"]
    assert payload["code"] == 0
    assert payload["result"]["cAutoFeed"] == 1
    assert payload["result"]["autoRefill"] == 1
    assert payload["result"]["ignoreColorAutoFeed"] == 0


def test_rpc_twoway_initial_state_route_exposes_material_and_token_payloads(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {
        "boxsInfo": {
            "same_material": [],
            "color_same_material": [],
            "boxColorInfo": [{"boxType": 0, "color": "#FF0000", "id": 1, "name": "MF049"}],
            "materialBoxs": [{"id": 1, "name": "MF049", "type": 0, "materials": [{"id": 1, "name": "PLA", "type": 0, "color": "#FF0000", "state": 1, "percent": 100, "remaining_length": 1000000}]}],
            "cfsName": "MF049",
        },
        "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
    })

    handler.serve_iotrouter_rpc(
        "/api/rest/iotrouter/rpc/twoway/abc123",
        {"method": "get", "params": {"reqGcodeFile": 1, "reqGcodeList": 1, "reqMaterials": 1, "boxsInfo": 1, "boxConfig": 1, "getToken": 1}},
    )

    payload = captured["payload"]
    assert payload["result"]["boxsInfo"]["cfsName"] == "MF049"
    assert payload["result"]["boxConfig"]["cAutoFeed"] == 1
    assert payload["result"]["reqMaterials"]["cfsName"] == "MF049"
    assert payload["result"]["getToken"].startswith("lan-compat")


def test_record_detail_route_returns_lan_compat_payload(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {"id": "rec-001"})

    handler.serve_print_record_detail("/api/cxy/v3/print/record/detail")

    payload = captured["payload"]
    assert payload["code"] == 0
    assert payload["result"]["id"] == "rec-001"
    assert payload["result"]["record"]["id"] == "rec-001"
    assert payload["result"]["record"]["timelapse"] is False
    assert payload["result"]["record"]["cameraState"]["state"] == "ready"
    assert payload["result"]["record"]["recordState"]["timelapse"] is False


def test_rpc_twoway_file_list_route_exposes_compat_file_entries(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))

    handler.serve_iotrouter_rpc(
        "/api/rest/iotrouter/rpc/twoway/abc123",
        {"method": "get", "params": {"pFileList": 1, "onePageNum": 10}},
    )

    payload = captured["payload"]
    assert isinstance(payload["result"]["pFileList"], list)
    assert payload["result"]["pFileList"][0]["filename"] == "lan-compat.gcode"
    assert payload["result"]["fileList"][0]["filename"] == "lan-compat.gcode"
    assert payload["result"]["fileList"][0]["previewimg"].startswith("data:image")


def test_record_list_route_returns_history_list_payload(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {"page": 1, "pageSize": 10, "deviceName": "lan-printer"})

    handler.serve_print_record_list("/api/cxy/v3/print/record/list")

    payload = captured["payload"]
    assert payload["data"]["historyList"][0]["filename"] == "lan-compat-record.mp4"
    assert payload["data"]["historyList"][0]["previewimg"].startswith("data:image")


def test_media_entries_use_lan_printer_identity_for_matching(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.update({"payload": payload}))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {"address": "192.168.1.100", "identity": "192.168.1.100", "name": "K2 Plus"})
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {"page": 1, "pageSize": 2})

    handler.serve_print_record_list("/api/cxy/v3/print/record/list")
    record_payload = captured["payload"]
    assert record_payload["data"]["historyList"][0]["deviceName"] == "192.168.1.100"

    handler.serve_iotrouter_rpc(
        "/api/rest/iotrouter/rpc/twoway/abc123",
        {"method": "get", "params": {"pFileList": 1, "onePageNum": 2}},
    )
    file_payload = captured["payload"]
    assert file_payload["result"]["pFileList"][0]["deviceName"] == "192.168.1.100"


def test_device_detail_route_exposes_rehydration_state(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {"name": "K2 Plus", "deviceName": "K2 Plus"})
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {"deviceName": "K2 Plus", "status": {}, "temperature": {}, "device": {"name": "K2 Plus"}, "record": {"cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}}, "cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}, "streamState": {"active": True, "source": "webcam"}}})
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {})

    handler.serve_print_cluster_device_detail({})

    payload = captured["payload"]
    assert payload["result"]["videoToken"] == "lan-compat-video-token"
    assert payload["result"]["cameraState"]["state"] == "ready"
    assert payload["result"]["recordState"]["recording"] is False
    assert payload["result"]["printInfo"]["modelName"] == "K2 Plus"
    assert payload["result"]["idleState"] == 0
    assert payload["result"]["device"]["name"] == "K2 Plus"
    assert payload["result"]["record"]["cameraState"]["state"] == "ready"


def test_device_detail_route_preserves_nested_record_state_from_request_device(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {"name": "K2 Plus", "deviceName": "K2 Plus"})
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {"deviceName": "K2 Plus", "status": {}, "temperature": {}, "device": {"name": "K2 Plus"}, "record": {"cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}}, "cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}, "streamState": {"active": True, "source": "webcam"}}})
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {"device": {"record": {"cameraState": {"state": "streaming"}, "recordState": {"recording": True, "timelapse": True}, "streamState": {"active": True, "source": "webrtc"}}}})

    handler.serve_print_cluster_device_detail({})

    payload = captured["payload"]
    assert payload["result"]["cameraState"]["state"] == "streaming"
    assert payload["result"]["recordState"]["recording"] is True
    assert payload["result"]["streamState"]["source"] == "webrtc"
    assert payload["result"]["record"]["cameraState"]["state"] == "streaming"
    assert payload["result"]["device"]["record"]["recordState"]["recording"] is True


def test_device_detail_route_exposes_stream_fields_expected_by_client(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {"name": "K2 Plus", "deviceName": "K2 Plus"})
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {"deviceName": "K2 Plus", "status": {}, "temperature": {}, "device": {"name": "K2 Plus"}, "record": {"cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}}, "cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}, "streamState": {"active": True, "source": "webcam"}}})
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {})

    handler.serve_print_cluster_device_detail({})

    payload = captured["payload"]
    assert payload["result"]["video"] == 1
    assert payload["result"]["tbId"] == "lan-compat-tb-id"
    assert payload["result"]["keyFileToken"] == "lan-compat-key-token"


def test_device_detail_route_rehydrates_nested_device_state(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {"name": "K2 Plus", "deviceName": "K2 Plus"})
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {"deviceName": "K2 Plus", "status": {}, "temperature": {}, "device": {"name": "K2 Plus"}, "record": {"cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}}, "cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}, "streamState": {"active": True, "source": "webcam"}}})
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {})

    handler.serve_print_cluster_device_detail({})

    payload = captured["payload"]
    assert payload["result"]["device"]["tbId"] == "lan-compat-tb-id"
    assert payload["result"]["device"]["videoToken"] == "lan-compat-video-token"
    assert payload["result"]["device"]["streamState"]["source"] == "webcam"
    assert payload["result"]["device"]["record"]["cameraState"]["state"] == "ready"
    assert payload["result"]["device"]["video"] == 1


def test_device_detail_route_preserves_client_alias_name_from_request_payload(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {"name": "K2 Plus", "model": "K2 Plus", "modelName": "K2 Plus", "deviceName": "K2 Plus"})
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {"deviceName": "K2 Plus", "status": {}, "temperature": {}, "device": {"name": "K2 Plus"}, "record": {"cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}}, "cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}, "streamState": {"active": True, "source": "webcam"}}})
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {"deviceName": "My LAN Printer", "aliasName": "My LAN Printer", "name": "My LAN Printer", "model": "K2 Plus", "modelName": "K2 Plus"})

    handler.serve_print_cluster_device_detail({})

    payload = captured["payload"]
    assert payload["result"]["deviceName"] == "My LAN Printer"
    assert payload["result"]["aliasName"] == "My LAN Printer"
    assert payload["result"]["modelName"] == "K2 Plus"
    assert payload["result"]["device"]["name"] == "My LAN Printer"
    assert payload["result"]["printInfo"]["deviceName"] == "My LAN Printer"


def test_device_detail_route_hydrates_nested_device_identity_on_initial_load(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {"name": "K2 Plus", "model": "K2 Plus", "modelName": "K2 Plus", "deviceName": "K2 Plus"})
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {"deviceName": "K2 Plus", "status": {}, "temperature": {}, "device": {"name": "K2 Plus"}, "record": {"cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}}, "cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}, "streamState": {"active": True, "source": "webcam"}}})
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {"deviceName": "My LAN Printer", "aliasName": "My LAN Printer", "name": "My LAN Printer", "model": "K2 Plus", "modelName": "K2 Plus"})

    handler.serve_print_cluster_device_detail({})

    payload = captured["payload"]
    assert payload["result"]["device"]["deviceName"] == "My LAN Printer"
    assert payload["result"]["device"]["aliasName"] == "My LAN Printer"
    assert payload["result"]["device"]["modelName"] == "K2 Plus"


def test_device_detail_route_uses_nested_device_identity_for_display_name(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {"name": "K2 Plus", "model": "K2 Plus", "modelName": "K2 Plus", "deviceName": "K2 Plus"})
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {"deviceName": "K2 Plus", "status": {}, "temperature": {}, "device": {"name": "K2 Plus"}, "record": {"cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}}, "cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}, "streamState": {"active": True, "source": "webcam"}}})
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {"device": {"deviceName": "My LAN Printer", "aliasName": "My LAN Printer", "name": "My LAN Printer", "model": "K2 Plus", "modelName": "K2 Plus"}})

    handler.serve_print_cluster_device_detail({})

    payload = captured["payload"]
    assert payload["result"]["deviceName"] == "My LAN Printer"
    assert payload["result"]["aliasName"] == "My LAN Printer"
    assert payload["result"]["name"] == "My LAN Printer"
    assert payload["result"]["device"]["deviceName"] == "My LAN Printer"
    assert payload["result"]["device"]["name"] == "My LAN Printer"


def test_device_detail_route_keeps_nested_identity_fields_separate_from_model(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {"name": "K2 Plus", "model": "K2 Plus", "modelName": "K2 Plus", "deviceName": "K2 Plus"})
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {"deviceName": "K2 Plus", "status": {}, "temperature": {}, "device": {"name": "K2 Plus"}, "record": {"cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}}, "cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}, "streamState": {"active": True, "source": "webcam"}}})
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {"deviceName": "My LAN Printer", "aliasName": "My LAN Printer", "name": "My LAN Printer", "model": "K2 Plus", "modelName": "K2 Plus"})

    handler.serve_print_cluster_device_detail({})

    payload = captured["payload"]
    assert payload["result"]["device"]["name"] == "My LAN Printer"
    assert payload["result"]["device"]["machine_name"] == "K2 Plus"
    assert payload["result"]["device"]["machine_type"] == "K2 Plus"
    assert payload["result"]["device"]["model_name"] == "K2 Plus"
    assert payload["result"]["device"]["modelName"] == "K2 Plus"


def test_device_detail_route_preserves_rich_state_from_request_payload(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    monkeypatch.setattr(handler, "_send_json", lambda payload: captured.setdefault("payload", payload))
    monkeypatch.setattr(handler, "_build_info_payload", lambda: {"name": "K2 Plus", "model": "K2 Plus", "modelName": "K2 Plus", "deviceName": "K2 Plus"})
    monkeypatch.setattr(handler, "_build_detail_payload", lambda: {"result": {"deviceName": "K2 Plus", "status": {}, "temperature": {}, "device": {"name": "K2 Plus"}, "record": {"cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}}, "cameraState": {"state": "ready"}, "recordState": {"recording": False, "timelapse": False}, "streamState": {"active": True, "source": "webcam"}}})
    monkeypatch.setattr(handler, "_read_json_body", lambda content_length: {"deviceName": "My LAN Printer", "aliasName": "My LAN Printer", "name": "My LAN Printer", "model": "K2 Plus", "modelName": "K2 Plus", "temperature": {"nozzle": {"value": 210.0, "target": 230.0}, "bed": {"value": 65.0, "target": 80.0}}, "status": {"state": "printing", "display_status": {"progress": 0.42}}, "boxsInfo": {"cfsName": "MF049"}, "boxConfig": {"cAutoFeed": 1, "cMode": 1}, "cameraState": {"enabled": True, "state": "streaming"}, "recordState": {"recording": True, "timelapse": True}, "streamState": {"active": True, "source": "webrtc"}, "videoToken": "persisted-token", "tbId": "persisted-tb", "keyFileToken": "persisted-key", "filamentsList": [{"name": "PLA"}], "ctrol": {"fan": 100}, "data": {"bedTemp0": 55.0}})

    handler.serve_print_cluster_device_detail({})

    payload = captured["payload"]
    assert payload["result"]["temperature"]["nozzle"]["value"] == 210.0
    assert payload["result"]["status"]["state"] == "printing"
    assert payload["result"]["boxsInfo"]["cfsName"] == "MF049"
    assert payload["result"]["boxConfig"]["cMode"] == 1
    assert payload["result"]["cameraState"]["state"] == "streaming"
    assert payload["result"]["recordState"]["timelapse"] is True
    assert payload["result"]["streamState"]["source"] == "webrtc"
    assert payload["result"]["videoToken"] == "persisted-token"
    assert payload["result"]["device"]["videoToken"] == "persisted-token"
    assert payload["result"]["device"]["boxsInfo"]["cfsName"] == "MF049"
    assert payload["result"]["device"]["record"]["recordState"]["recording"] is True


def test_webrtc_local_handler_returns_base64_encoded_offer_answer(monkeypatch):
    handler = ProbeHandler.__new__(ProbeHandler)
    captured = {}

    body = b"v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\n"
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status: captured.setdefault("status", status)
    handler.send_header = lambda name, value: captured.setdefault(name, value)
    handler.end_headers = lambda: None

    handler.serve_stream_probe("/call/webrtc_local", method="POST")

    response_body = handler.wfile.getvalue().decode("utf-8")
    decoded = json.loads(base64.b64decode(response_body).decode("utf-8"))

    assert captured["status"] == 200
    assert captured["Content-Type"] == "text/plain; charset=utf-8"
    assert decoded["type"] == "answer"
    assert decoded["sdp"].startswith("v=0")
