"""Publish a limited inventory snapshot; never expose the private Spoolman API."""
import logging
import math
import re
import threading
from urllib.parse import urlsplit

import requests

log = logging.getLogger("bambu.share")


def public_inventory(spools):
    """Allowlist public fields, excluding locations, notes, prices and credentials."""
    output = []
    if not isinstance(spools, list):
        raise ValueError("Expected a spool list")
    if len(spools) > 2000:
        raise ValueError("Too many spools for shared inventory")
    for spool in spools:
        if spool.get("archived"):
            continue
        filament = spool.get("filament") or {}
        vendor = filament.get("vendor") or {}
        sid = spool.get("id")
        if isinstance(sid, bool) or not isinstance(sid, int) or sid <= 0:
            continue
        color = str(filament.get("color_hex") or "").lstrip("#").upper()
        color = color[:6] if re.fullmatch(r"[0-9A-F]{6}([0-9A-F]{2})?", color) else None
        weight = spool.get("remaining_weight")
        try:
            weight = float(weight)
            if not math.isfinite(weight) or weight < 0:
                weight = None
        except (ValueError, TypeError):
            weight = None
        output.append({
            "id": sid,
            "brand": str(vendor.get("name") or "")[:80],
            "name": str(filament.get("name") or "")[:100],
            "material": str(filament.get("material") or "Unknown")[:40],
            "colorHex": color,
            "colorName": "",
            "remainingGrams": round(weight, 2) if weight is not None else None,
        })
    return {"spools": output}


def sync_once(spoolman_url, share_url, token):
    parsed = urlsplit(share_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Inventory sharing requires an HTTPS URL without credentials or query parameters")
    source = requests.get(f"{spoolman_url.rstrip('/')}/api/v1/spool", timeout=15)
    source.raise_for_status()
    snapshot = public_inventory(source.json())
    response = requests.post(
        f"{share_url.rstrip('/')}/api/sync",
        headers={"Authorization": f"Bearer {token}"},
        json=snapshot, timeout=15, allow_redirects=False,
    )
    if response.status_code != 200:
        # Do not print the request, credentials, response body or private URLs.
        raise RuntimeError(f"Shared inventory update returned HTTP {response.status_code}")
    log.info("Shared inventory updated (%s spools)", len(snapshot["spools"]))


def start_inventory_share(spoolman_url, share_url, token):
    """Opt-in, five-minute snapshots. Returns a stop event for clean shutdown."""
    stop = threading.Event()
    if not share_url or not token:
        return stop
    if not spoolman_url:
        log.error("Inventory sharing needs a Spoolman URL")
        return stop

    def publish_loop():
        while not stop.is_set():
            try:
                sync_once(spoolman_url, share_url, token)
            except Exception as error:
                log.warning("Shared inventory update failed (%s); will retry in five minutes", type(error).__name__)
            stop.wait(300)

    threading.Thread(target=publish_loop, daemon=True, name="inventory-share").start()
    return stop
