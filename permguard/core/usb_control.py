"""
usb_control.py — USB port enable / disable via Linux sysfs.

Each USB device exposes /sys/bus/usb/devices/<id>/authorized
  1 = enabled (default)
  0 = disabled (device is cut off, OS ignores it)

Requires root (pkexec) to write authorization.
"""
import re
from pathlib import Path
from .system import run, run_privileged


SYS_USB = Path("/sys/bus/usb/devices")


def _read(path: Path, default="") -> str:
    try:
        return path.read_text().strip()
    except Exception:
        return default


def get_usb_ports() -> list[dict]:
    """
    Return list of dicts for every physical USB port (not hubs, not interfaces).
    Each dict: id, manufacturer, product, authorized, speed, bus, devnum
    """
    ports = []
    if not SYS_USB.exists():
        return ports

    for dev_path in sorted(SYS_USB.iterdir()):
        name = dev_path.name
        # Skip interfaces (contain colon), keep bus devices
        if ":" in name:
            continue
        auth_file = dev_path / "authorized"
        if not auth_file.exists():
            continue

        manufacturer = _read(dev_path / "manufacturer") or "Unknown"
        product      = _read(dev_path / "product")      or "USB Device"
        authorized   = _read(auth_file, "1") == "1"
        speed        = _read(dev_path / "speed")        or "?"
        bus          = _read(dev_path / "busnum")       or "?"
        devnum       = _read(dev_path / "devnum")       or "?"
        id_vendor    = _read(dev_path / "idVendor")     or "????"
        id_product   = _read(dev_path / "idProduct")   or "????"

        ports.append({
            "id":           name,
            "bus":          bus,
            "devnum":       devnum,
            "vendor_id":    id_vendor,
            "product_id":   id_product,
            "manufacturer": manufacturer,
            "product":      product,
            "speed":        f"{speed} Mbps" if speed != "?" else "?",
            "authorized":   authorized,
            "auth_path":    str(auth_file),
        })

    return ports


def set_authorized(device_id: str, authorized: bool) -> tuple[bool, str]:
    """
    Enable or disable a USB device by writing to its authorized file.
    Requires root via pkexec.
    """
    auth_path = SYS_USB / device_id / "authorized"
    if not auth_path.exists():
        return False, f"Device {device_id} not found"

    val = "1" if authorized else "0"
    # Use tee instead of sh -c to avoid shell injection via device_id
    return run_privileged(["tee", str(auth_path)], stdin_data=val)


def disable_all_usb() -> tuple[bool, str]:
    """Disable every non-hub USB device (emergency lockdown)."""
    errors = []
    for port in get_usb_ports():
        if port["authorized"] and port["id"] not in ("usb1", "usb2", "usb3", "usb4"):
            ok, err = set_authorized(port["id"], False)
            if not ok:
                errors.append(f"{port['id']}: {err}")
    return len(errors) == 0, "\n".join(errors)
