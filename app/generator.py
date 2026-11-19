"""Render a hardening script from a validated selection."""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .catalog import CATALOG_VERSION, Catalog, ValidationError, load_catalog

SCRIPT_DIR = Path(__file__).resolve().parent / "script"

_env = Environment(
    loader=FileSystemLoader(SCRIPT_DIR),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
    # Bash uses "{#" in ${#array[@]}, so move Jinja comments out of the way.
    comment_start_string="<#--",
    comment_end_string="--#>",
    autoescape=False,
)
_env.filters["q"] = lambda v: shlex.quote(str(v))
_env.filters["qlist"] = lambda vs: " ".join(shlex.quote(str(v)) for v in vs)


def _firewall_rules(cat: Catalog, os_id: str, fw: dict) -> list[dict]:
    """Flatten the port map and custom ports into [{spec, action, label}]."""
    sec = next(s for s in cat.sections_for(os_id) if s["id"] == "firewall")
    portopt = next(o for o in sec["options"] if o["id"] == "ports")
    entries = []
    for p in portopt["ports"]:
        action = fw["ports"].get(p["id"], 0)
        if action:
            entries += [(spec, action, p["label"]) for spec in p["spec"]]
    for entry in fw["custom_ports"]:
        spec, action = entry.rsplit(":", 1)
        port = spec.split("/")[0]
        lo, _, hi = port.partition("-")
        if not (1 <= int(lo) <= 65535 and (not hi or int(lo) <= int(hi) <= 65535)):
            raise ValidationError([f"firewall.custom_ports: invalid port range '{entry}'"])
        if int(action):
            entries.append((spec, int(action), "custom"))
    rules = []
    for spec, action, label in entries:
        port, proto = spec.split("/")
        rules.append({
            "port": port,                       # 22 or 6000-6010 (firewalld form)
            "proto": proto,
            "ufw": f"{port.replace('-', ':')}/{proto}",
            "action": action,
            "label": label,
        })
    return rules


def generate(os_id: str, config: dict | None) -> dict:
    """Return {'script', 'filename', 'sha256', 'config', 'warnings'}."""
    cat = load_catalog()
    osd = cat.os(os_id)
    cfg = cat.normalize(os_id, config)
    warnings: list[str] = []

    if "accounts" in cfg:
        acc = cfg["accounts"]
        admins = list(acc["admin_users"])
        if acc["new_admin_user"] and acc["new_admin_user"] not in admins:
            admins.insert(0, acc["new_admin_user"])
        acc["all_admins"] = admins
        if acc["new_admin_keys"] and not acc["new_admin_user"]:
            warnings.append("SSH keys were given without a new admin account name; they will be ignored.")

    if "packages" in cfg and cfg["packages"]["enabled"]:
        pk = cfg["packages"]
        hit = set(pk.get("apt_disable_sources", []) + pk.get("dnf_disable_repos", [])) & set(osd.get("primary_sources", []))
        if hit:
            warnings.append(f"You are disabling the main {osd['name']} package source ({', '.join(sorted(hit))}). "
                            "The machine will stop receiving security updates and most packages will have no source.")

    fw_rules: list[dict] = []
    if "firewall" in cfg and cfg["firewall"]["enabled"]:
        fw = cfg["firewall"]
        fw_rules = _firewall_rules(cat, os_id, fw)
        if fw["ports"].get("ssh") not in (1, 3):
            warnings.append("SSH is not allowed in. The script refuses to apply this over an SSH session unless run with --force.")
        if fw["default_outgoing"] == "deny" and fw["ports"].get("dns") not in (2, 3):
            warnings.append("Outgoing traffic is denied but DNS is not allowed out, so name resolution will fail.")

    now = datetime.now(timezone.utc)
    profile = {"catalog_version": CATALOG_VERSION, "os": os_id, "config": _profile_config(cfg)}
    profile_b64 = base64.b64encode(json.dumps(profile, separators=(",", ":")).encode()).decode()

    script = _env.get_template("main.sh.j2").render(
        os=osd,
        cfg=cfg,
        fw_rules=fw_rules,
        version=CATALOG_VERSION,
        generated=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        profile_b64=profile_b64,
        hc_logs=(SCRIPT_DIR / "hc-logs.sh").read_text(),
        hc_inactive=_env.get_template("hc-inactive.sh.j2").render(os=osd, cfg=cfg),
    )
    return {
        "script": script,
        "filename": f"hardeningchef-{os_id}-{now.strftime('%Y%m%d-%H%M%S')}.sh",
        "sha256": hashlib.sha256(script.encode()).hexdigest(),
        "profile": profile,
        "warnings": warnings,
    }


def _profile_config(cfg: dict) -> dict:
    out = json.loads(json.dumps(cfg))
    out.get("accounts", {}).pop("all_admins", None)
    return out
