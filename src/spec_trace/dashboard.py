from __future__ import annotations

from importlib.resources import files

_ASSET_ROOT = files("spec_trace").joinpath("web_assets")


def asset_text(name: str) -> str:
    return _ASSET_ROOT.joinpath(name).read_text(encoding="utf-8")


HTML = asset_text("dashboard.html")
