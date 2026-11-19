import base64
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import main
from app.catalog import ValidationError, load_catalog
from app.generator import generate

OSES = list(load_catalog().oses)
SHELLCHECK = shutil.which("shellcheck")


def full_config(os_id):
    fam = load_catalog().oses[os_id]["family"]
    cfg = {
        "accounts": {
            "new_admin_user": "opsadmin",
            "new_admin_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKey me@laptop"],
            "admin_users": ["alice", "bob"],
        },
        "ssh": {"install_server": True, "tcp_forwarding": True},
        "firewall": {
            "default_outgoing": "deny",
            "ports": {"ssh": 1, "http": 3, "https": 2, "dns": 2, "rdp": 4},
            "custom_ports": ["6000-6010/tcp:1", "51820/udp:4"],
        },
        "packages": {"enabled": True, "remove_orphaned": True, "remove_fuse2": True, "remove_flatpak": True},
    }
    if fam == "debian":
        cfg["packages"].update(apt_disable_sources=["docker.list", "debian.sources"],
                               apt_disable_components=load_catalog().oses[os_id]["optional_components"][:2])
        cfg["apparmor"] = {"install_extra": True, "enforce_profiles": ["usr.sbin.cupsd"], "complain_profiles": ["usr.bin.man"]}
    else:
        cfg["packages"].update(dnf_disable_repos=["updates-testing", "fedora-cisco-openh264"])
        cfg["selinux"] = {"mode": "permissive"}
    return cfg


@pytest.mark.parametrize("os_id", OSES)
@pytest.mark.parametrize("variant", ["defaults", "full"])
def test_script_is_valid_bash(os_id, variant, tmp_path):
    r = generate(os_id, None if variant == "defaults" else full_config(os_id))
    path = tmp_path / r["filename"]
    path.write_text(r["script"])
    subprocess.run(["bash", "-n", str(path)], check=True)
    if SHELLCHECK:
        res = subprocess.run([SHELLCHECK, "-S", "warning", str(path)], capture_output=True, text=True)
        assert res.returncode == 0, res.stdout


def test_family_specific_choices():
    deb = generate("debian-13", full_config("debian-13"))["script"]
    fed = generate("fedora-45", full_config("fedora-45"))["script"]
    assert "ufw default deny incoming" in deb and "firewall-cmd" not in deb
    assert "firewall-cmd" in fed and "ufw default" not in fed
    assert "SVC=ssh" in deb and "SVC=sshd" in fed
    assert "HC_ADMIN_GROUP=sudo" in deb and "HC_ADMIN_GROUP=wheel" in fed
    assert "pam-auth-update" in deb and "authselect enable-feature with-faillock" in fed
    assert "aa-enforce" in deb and "setenforce" in fed
    assert "6000:6010/tcp" in deb and "6000-6010/tcp" in fed


def test_profile_round_trip():
    cfg = full_config("rocky-10")
    r = generate("rocky-10", cfg)
    line = next(l for l in r["script"].splitlines() if l.startswith("# HC-PROFILE: "))
    profile = json.loads(base64.b64decode(line.split(": ", 1)[1]))
    assert profile["os"] == "rocky-10"
    again = generate("rocky-10", profile["config"])
    strip = lambda s: "\n".join(l for l in s.splitlines() if not l.startswith("#  Generated"))
    assert strip(again["script"]) == strip(r["script"])


@pytest.mark.parametrize("bad", [
    {"accounts": {"admin_users": ["root; rm -rf /"]}},
    {"accounts": {"new_admin_user": "$(id)"}},
    {"accounts": {"new_admin_keys": ["ssh-ed25519 AAAA' ; reboot ; '"]}},
    {"ssh": {"max_startups": "10;reboot"}},
    {"ssh": {"max_auth_tries": 999}},
    {"ssh": {"nonexistent": True}},
    {"firewall": {"ports": {"ssh": 9}}},
    {"firewall": {"custom_ports": ["22/tcp:1;reboot"]}},
    {"packages": {"apt_disable_sources": ["../../etc/passwd"]}},
    {"selinux": {"mode": "enforcing"}},  # not applicable to Debian
])
def test_rejects_bad_input(bad):
    with pytest.raises(ValidationError):
        generate("debian-13", bad)


def test_banner_is_quoted():
    evil = "Hi $(touch /tmp/pwned) `id` 'q' \"dq\""
    script = generate("ubuntu-26.04", {"ssh": {"banner_text": evil}})["script"]
    line = next(l for l in script.splitlines() if "/etc/issue.net 0644" in l)
    out = subprocess.run(["bash", "-c", line.split(" | ")[0]], capture_output=True, text=True).stdout
    assert out.strip() == evil


def test_api_generate_and_share():
    client = TestClient(main.app)
    cat = client.get("/api/catalog").json()
    assert {o["id"] for o in cat["oses"]} == set(OSES)
    res = client.post("/api/generate", json={"os": "kali-rolling", "config": {}})
    assert res.status_code == 200
    data = res.json()
    url = data["share"]["url"]
    token = url.rsplit("/", 1)[1]
    got = client.get(f"/s/{token}")
    assert got.status_code == 200 and got.text == data["script"]
    assert got.headers["cache-control"] == "no-store"
    assert client.get(f"/s/{token}.sha256").text.startswith(data["sha256"])
    assert client.get("/s/doesnotexistdoesnotexist").status_code == 404


def test_api_validation_errors():
    client = TestClient(main.app)
    res = client.post("/api/generate", json={"os": "fedora-45", "config": {"ssh": {"max_sessions": 0}}})
    assert res.status_code == 422 and res.json()["errors"]
    assert client.post("/api/generate", json={"os": "windows", "config": {}}).status_code == 422


def test_share_expiry(monkeypatch):
    from app import store as store_mod
    s = store_mod.ScriptStore(ttl=10, max_entries=2)
    t0 = 1000.0
    monkeypatch.setattr(store_mod.time, "time", lambda: t0)
    a, _ = s.put("a", "x", "a.sh")
    b, _ = s.put("b", "x", "b.sh")
    c, _ = s.put("c", "x", "c.sh")
    assert s.get(a) is None and s.get(c).script == "c"   # cap evicts oldest
    monkeypatch.setattr(store_mod.time, "time", lambda: t0 + 11)
    assert s.get(b) is None and s.get(c) is None          # TTL
