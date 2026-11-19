"""Load the YAML control catalog and validate user selections against it."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CATALOG_DIR = Path(__file__).resolve().parent.parent / "catalog"
CATALOG_VERSION = "1.0.0"

# Characters allowed in free-form text (banner). Quoting makes anything safe for
# the shell; this just keeps control characters out of /etc/issue.net.
_TEXT_OK = re.compile(r"[^\x00-\x08\x0b-\x1f\x7f]*")


class ValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Catalog:
    oses: dict[str, dict]
    sections: list[dict]

    def os(self, os_id: str) -> dict:
        if os_id not in self.oses:
            raise ValidationError([f"unknown OS '{os_id}'"])
        return {"id": os_id, **self.oses[os_id]}

    def sections_for(self, os_id: str) -> list[dict]:
        """Sections and options applicable to one OS, with OS-specific values resolved."""
        osd = self.os(os_id)
        fam = osd["family"]
        out = []
        for sec in self.sections:
            if fam not in sec.get("families", [fam]):
                continue
            sec = copy.deepcopy(sec)
            opts = []
            for opt in sec.get("options", []):
                if fam not in opt.get("families", [fam]):
                    continue
                if "choices_from" in opt:
                    opt["choices"] = [{"value": c, "label": c} for c in osd.get(opt.pop("choices_from"), [])]
                    if not opt["choices"]:
                        continue
                if "placeholder_from" in opt:
                    opt["placeholder"] = osd.get(opt.pop("placeholder_from"), "")
                opts.append(opt)
            sec["options"] = opts
            out.append(sec)
        return out

    def public(self) -> dict:
        return {
            "version": CATALOG_VERSION,
            "oses": [
                {"id": k, "name": v["name"], "family": v["family"], "firewall": v["firewall"], "mac": v["mac"]}
                for k, v in self.oses.items()
            ],
            "sections": {os_id: self.sections_for(os_id) for os_id in self.oses},
        }

    # ------------------------------------------------------------------ validation

    def normalize(self, os_id: str, config: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        """Return a complete config (defaults filled in) or raise ValidationError."""
        config = config or {}
        if not isinstance(config, dict):
            raise ValidationError(["config must be an object"])
        sections = self.sections_for(os_id)
        known = {s["id"] for s in sections}
        errors: list[str] = []
        for key in config:
            if key not in known:
                errors.append(f"section '{key}' does not apply to {os_id}")

        result: dict[str, dict[str, Any]] = {}
        for sec in sections:
            given = config.get(sec["id"], {}) or {}
            if not isinstance(given, dict):
                errors.append(f"{sec['id']}: must be an object")
                given = {}
            opt_ids = {o["id"] for o in sec["options"]}
            for k in given:
                if k not in opt_ids:
                    errors.append(f"{sec['id']}.{k}: unknown option")
            values: dict[str, Any] = {}
            for opt in sec["options"]:
                raw = given.get(opt["id"], copy.deepcopy(opt.get("default")))
                try:
                    values[opt["id"]] = _coerce(opt, raw)
                except ValueError as e:
                    errors.append(f"{sec['id']}.{opt['id']}: {e}")
            result[sec["id"]] = values
        if errors:
            raise ValidationError(errors)
        return result


def _full(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value) is not None


def _coerce(opt: dict, raw: Any) -> Any:
    t = opt["type"]
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        raise ValueError("expected true/false")
    if t == "int":
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            raise ValueError("expected an integer")
        try:
            v = int(raw)
        except ValueError:
            raise ValueError("expected an integer") from None
        if v < opt.get("min", v) or v > opt.get("max", v):
            raise ValueError(f"must be between {opt['min']} and {opt['max']}")
        return v
    if t in ("text", "textarea"):
        if not isinstance(raw, str):
            raise ValueError("expected text")
        v = raw.replace("\r\n", "\n").strip() if t == "textarea" else raw.strip()
        if len(v) > opt.get("maxlength", 200):
            raise ValueError("too long")
        if t == "textarea" and not _TEXT_OK.fullmatch(v):
            raise ValueError("contains control characters")
        if v and "pattern" in opt and not _full(opt["pattern"], v):
            raise ValueError(f"invalid value '{v}'")
        return v
    if t == "choice":
        allowed = [c["value"] for c in opt["choices"]]
        if raw not in allowed:
            raise ValueError(f"must be one of {allowed}")
        return raw
    if t in ("list", "multichoice"):
        if isinstance(raw, str):
            raw = re.split(r"[,\n]", raw)
        if not isinstance(raw, list):
            raise ValueError("expected a list")
        items = []
        for item in raw:
            if not isinstance(item, str):
                raise ValueError("list items must be text")
            item = item.strip()
            if not item:
                continue
            if t == "multichoice":
                if item not in [c["value"] for c in opt["choices"]]:
                    raise ValueError(f"'{item}' is not a valid choice")
            elif "pattern" in opt and not _full(opt["pattern"], item):
                raise ValueError(f"invalid entry '{item[:60]}'")
            if item not in items:
                items.append(item)
        if len(items) > 100:
            raise ValueError("too many entries")
        return items
    if t == "portmap":
        if not isinstance(raw, dict):
            raise ValueError("expected an object of port -> action")
        valid_ports = {p["id"] for p in opt["ports"]}
        valid_actions = {a["value"] for a in opt["actions"]}
        out = {p: 0 for p in valid_ports}
        for k, v in raw.items():
            if k not in valid_ports:
                raise ValueError(f"unknown port '{k}'")
            if isinstance(v, bool) or not isinstance(v, int) or v not in valid_actions:
                raise ValueError(f"{k}: action must be 0-4")
            out[k] = v
        return out
    raise ValueError(f"unsupported option type {t}")


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    oses = yaml.safe_load((CATALOG_DIR / "os.yaml").read_text())
    sections = yaml.safe_load((CATALOG_DIR / "sections.yaml").read_text())
    return Catalog(oses=oses, sections=sections)
