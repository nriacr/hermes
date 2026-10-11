"""Read maintenance counts through Home Assistant authentication, never the guest link.

The existing HA helper owns credentials. Its short-lived ingress cookie stays
in memory and is never passed as a command argument, printed or saved.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

APP = "769724e3_hermes"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def open_ingress(request):
    # A redirect must never forward the HA session cookie to another destination.
    return urllib.request.build_opener(NoRedirect).open(request, timeout=25)


def rpc(helper: Path, endpoint: str, method: str = "get") -> dict:
    result = subprocess.run([str(helper), "rpc", "supervisor/api", json.dumps({"endpoint": endpoint, "method": method})],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("HA authentication failed")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("invalid HA response")
    return value


def read_runtime(workspace: Path) -> dict:
    helper = workspace / "bin/ha-ws"
    info = rpc(helper, f"/addons/{APP}/info")
    entry = str(info.get("ingress_entry") or "")
    if entry.startswith("/api/hassio_ingress/"):
        entry = entry.removeprefix("/api/hassio_ingress/").rstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", entry):
        raise ValueError("invalid ingress entry")
    session = str(rpc(helper, "/ingress/session", "post").get("session") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session):
        raise ValueError("invalid ingress session")
    base = os.environ.get("HA_URL", "http://192.168.1.143:8123").rstrip("/")
    for path in ("internal/runtime", "runtime"):
        request = urllib.request.Request(f"{base}/api/hassio_ingress/{entry}/{path}",
                                         headers={"Cookie": f"ingress_session={session}"})
        try:
            with open_ingress(request) as response:
                value = json.load(response)
                if not isinstance(value, dict) or "healthy" not in value or "version" not in value:
                    raise ValueError("invalid runtime response")
                return value
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            # Only old releases lack the HA-only maintenance route. Never retry publicly.
            if status != 404 or path != "internal/runtime":
                raise ValueError("runtime unavailable") from None
    raise ValueError("runtime unavailable")


def main():
    default = Path(__file__).resolve().parents[2] / "bin/ha-ssh"
    workspace = Path(os.environ.get("HA_SSH_HELPER", str(default))).resolve().parents[1]
    try:
        print(json.dumps(read_runtime(workspace), ensure_ascii=False))
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SystemExit("Home Assistant üzerinden Hermes ölçümleri okunamadı; gizli ayrıntılar gösterilmedi.") from None


if __name__ == "__main__":
    main()
