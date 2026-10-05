"""Cold-import regression for offline Linux-compatible report exports."""

import json
import subprocess
import sys
from pathlib import Path


def run_script(code, *arguments):
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", code, str(project / "scripts"), *map(str, arguments)],
                            cwd=project, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result


def test_network_guard_preserves_socket_class_and_restores_operations():
    run_script('''import socket, sys
sys.path.insert(0, sys.argv[1])
from local_smoke import blocked_network
original_class = socket.socket
original_connect = socket.socket.connect
original_lookup = socket.getaddrinfo
sys.modules.pop("ssl", None)
with blocked_network():
    import ssl
    assert socket.socket is original_class
    assert issubclass(ssl.SSLSocket, original_class)
    for operation in [lambda: socket.create_connection(("127.0.0.1", 9)),
                      lambda: socket.getaddrinfo("example.invalid", 443)]:
        try:
            operation()
        except AssertionError as error:
            assert "Unexpected network access" in str(error)
        else:
            raise AssertionError("The network guard did not block access")
    with socket.socket() as endpoint:
        operations = [lambda: endpoint.connect(("127.0.0.1", 9)),
                          lambda: endpoint.connect_ex(("127.0.0.1", 9)),
                          lambda: endpoint.send(b"synthetic"),
                          lambda: endpoint.sendall(b"synthetic"),
                          lambda: endpoint.sendto(b"synthetic", ("127.0.0.1", 9))]
        if hasattr(endpoint, "sendmsg"):
            operations.append(lambda: endpoint.sendmsg([b"synthetic"]))
        for operation in operations:
            try:
                operation()
            except AssertionError:
                pass
            else:
                raise AssertionError("A socket operation bypassed the guard")
assert socket.socket.connect is original_connect
assert socket.getaddrinfo is original_lookup
try:
    with blocked_network():
        raise ValueError("Synthetic exception")
except ValueError:
    pass
assert socket.socket.connect is original_connect
assert socket.getaddrinfo is original_lookup
''')


def test_cold_ssl_and_parquet_smoke_exports_preserve_scientific_values(tmp_path):
    result = run_script('''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from local_smoke import smoke
sys.modules.pop("ssl", None)
output = Path(sys.argv[2])
report = smoke(output)
import pyarrow.parquet as pq
rows = pq.read_table(output / "dataset.parquet").to_pylist()
assert len(rows) == 1
assert rows[0]["value"] == 7 and rows[0]["uncertainty"] == 1
assert rows[0]["n_independent"] == 3 and rows[0]["status"] == "accepted"
assert all((output / name).is_file() for name in ("report.html", "dataset.json", "dataset.csv", "dataset.parquet"))
print(json.dumps(report))
''', tmp_path / "exports")
    summary = json.loads(result.stdout)
    assert summary["status"] == "passed" and summary["model_calls"] == 0
    assert summary["network_access"] == "blocked"
    assert summary["phases"] == ["screening", "text_table", "verification"]
