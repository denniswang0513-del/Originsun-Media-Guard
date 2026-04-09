"""DJI Protobuf metadata parser for Mavic 3 / Mini 4 Pro / Air 3.

Extracts flight data (GPS, camera params, attitude) from the binary
Protobuf track embedded in DJI MOV files (handler_name containing "DJI").
"""

import os
import struct
import math
import tempfile
import subprocess
import json

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HNO = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _quat_to_euler(w, x, y, z):
    """Quaternion to (pitch, roll, yaw) in degrees."""
    pitch = math.degrees(math.asin(max(-1, min(1, 2 * (w * y - z * x)))))
    roll = math.degrees(math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
    yaw = math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    return (round(pitch, 1), round(roll, 1), round(yaw, 1))


# ── Low-level Protobuf decoding ──────────────────────────────────

def _decode_varint(buf, pos):
    """Decode a varint at position, return (value, new_pos)."""
    result = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if (b & 0x80) == 0:
            return result, pos
        shift += 7
    return result, pos


def _decode_field(buf, pos):
    """Decode one protobuf field. Returns (field_number, wire_type, value, new_pos).

    wire_type 0 = varint, 1 = 64-bit, 2 = length-delimited, 5 = 32-bit.
    """
    if pos >= len(buf):
        return None
    tag, pos = _decode_varint(buf, pos)
    field_number = tag >> 3
    wire_type = tag & 0x07

    if wire_type == 0:  # varint
        value, pos = _decode_varint(buf, pos)
        return field_number, wire_type, value, pos
    elif wire_type == 1:  # 64-bit
        if pos + 8 > len(buf):
            return None
        value = buf[pos:pos + 8]
        return field_number, wire_type, value, pos + 8
    elif wire_type == 2:  # length-delimited
        length, pos = _decode_varint(buf, pos)
        if pos + length > len(buf):
            return None
        value = buf[pos:pos + length]
        return field_number, wire_type, value, pos + length
    elif wire_type == 5:  # 32-bit
        if pos + 4 > len(buf):
            return None
        value = buf[pos:pos + 4]
        return field_number, wire_type, value, pos + 4
    else:
        # Unknown wire type — skip
        return None


def _decode_all_fields(buf):
    """Decode all top-level fields in a protobuf buffer."""
    fields = []
    pos = 0
    while pos < len(buf):
        result = _decode_field(buf, pos)
        if result is None:
            break
        fn, wt, val, pos = result
        fields.append((fn, wt, val))
    return fields


def _find_fields(fields, field_number):
    """Return all values for a given field number."""
    return [val for fn, wt, val in fields if fn == field_number]


def _find_field(fields, field_number, default=None):
    """Return first value for a given field number."""
    for fn, wt, val in fields:
        if fn == field_number:
            return val
    return default


def _bytes_to_double(b):
    """Convert 8-byte little-endian to double."""
    try:
        return struct.unpack("<d", b)[0]
    except Exception:
        return 0.0


def _bytes_to_float(b):
    """Convert 4-byte little-endian to float."""
    try:
        return struct.unpack("<f", b)[0]
    except Exception:
        return 0.0


def _try_decode_string(b):
    """Try to decode bytes as UTF-8 string."""
    try:
        return b.decode("utf-8", errors="ignore")
    except Exception:
        return ""


# ── Header parsing ───────────────────────────────────────────────

def _parse_header(header_buf):
    """Parse the header message (field 1) for device info."""
    fields = _decode_all_fields(header_buf)
    info = {}

    # Field 1 = device sub-message
    device_buf = _find_field(fields, 1)
    if device_buf and isinstance(device_buf, (bytes, bytearray)):
        device_fields = _decode_all_fields(device_buf)
        # 1.1 = protocol name
        proto = _find_field(device_fields, 1)
        if proto and isinstance(proto, (bytes, bytearray)):
            info["protocol"] = _try_decode_string(proto)
        # 1.5 = serial number
        serial = _find_field(device_fields, 5)
        if serial and isinstance(serial, (bytes, bytearray)):
            info["serial"] = _try_decode_string(serial)
        # 1.10 = device name
        device_name = _find_field(device_fields, 10)
        if device_name and isinstance(device_name, (bytes, bytearray)):
            info["device"] = _try_decode_string(device_name)

    # Field 2 = camera/lens info
    camera_buf = _find_field(fields, 2)
    if camera_buf and isinstance(camera_buf, (bytes, bytearray)):
        text = _try_decode_string(camera_buf)
        if text:
            info["lens"] = text

    return info


# ── Frame parsing ────────────────────────────────────────────────

def _parse_frame(frame_buf):
    """Parse a single per-second frame (field 3 in the root)."""
    fields = _decode_all_fields(frame_buf)
    frame = {
        "lat": 0.0, "lon": 0.0, "alt": 0.0,
        "iso": 100, "shutter": 0, "ev": 0.0, "fnum": 0.0,
        "f_pry": (0.0, 0.0, 0.0), "g_pry": (0.0, 0.0, 0.0),
    }

    # Field 4 = GPS sub-message
    gps_buf = _find_field(fields, 4)
    if gps_buf and isinstance(gps_buf, (bytes, bytearray)):
        gps_fields = _decode_all_fields(gps_buf)
        # Sub-field 1 = lat/lon container
        coord_buf = _find_field(gps_fields, 1)
        if coord_buf and isinstance(coord_buf, (bytes, bytearray)):
            coord_fields = _decode_all_fields(coord_buf)
            # 2 = latitude (double, radians)
            lat_raw = _find_field(coord_fields, 2)
            if lat_raw and isinstance(lat_raw, (bytes, bytearray)) and len(lat_raw) == 8:
                frame["lat"] = round(math.degrees(_bytes_to_double(lat_raw)), 6)
            # 3 = longitude (double, radians)
            lon_raw = _find_field(coord_fields, 3)
            if lon_raw and isinstance(lon_raw, (bytes, bytearray)) and len(lon_raw) == 8:
                frame["lon"] = round(math.degrees(_bytes_to_double(lon_raw)), 6)

    # Field 5 = altitude sub-message
    alt_buf = _find_field(fields, 5)
    if alt_buf and isinstance(alt_buf, (bytes, bytearray)):
        alt_fields = _decode_all_fields(alt_buf)
        alt_raw = _find_field(alt_fields, 1)
        if alt_raw and isinstance(alt_raw, (bytes, bytearray)) and len(alt_raw) == 4:
            alt_val = _bytes_to_float(alt_raw)
            # If value seems too large, divide by 1000
            if abs(alt_val) > 10000:
                alt_val /= 1000.0
            frame["alt"] = round(alt_val, 2)

    # Field 2 = camera params sub-message
    cam_buf = _find_field(fields, 2)
    if cam_buf and isinstance(cam_buf, (bytes, bytearray)):
        cam_fields = _decode_all_fields(cam_buf)

        # 7 = ISO sub-message → 7.1 = ISO value (float 32-bit)
        iso_buf = _find_field(cam_fields, 7)
        if iso_buf and isinstance(iso_buf, (bytes, bytearray)):
            iso_fields = _decode_all_fields(iso_buf)
            iso_raw = _find_field(iso_fields, 1)
            if iso_raw and isinstance(iso_raw, (bytes, bytearray)) and len(iso_raw) == 4:
                frame["iso"] = round(_bytes_to_float(iso_raw))

        # 37 = shutter sub-message → 37.1 = shutter denominator (float)
        shut_buf = _find_field(cam_fields, 37)
        if shut_buf and isinstance(shut_buf, (bytes, bytearray)):
            shut_fields = _decode_all_fields(shut_buf)
            shut_raw = _find_field(shut_fields, 1)
            if shut_raw and isinstance(shut_raw, (bytes, bytearray)) and len(shut_raw) == 4:
                frame["shutter"] = round(_bytes_to_float(shut_raw))

        # 24 = aperture sub-message → 24.1 = aperture × 2000 (varint)
        ap_buf = _find_field(cam_fields, 24)
        if ap_buf and isinstance(ap_buf, (bytes, bytearray)):
            ap_fields = _decode_all_fields(ap_buf)
            ap_raw = _find_field(ap_fields, 1)
            if ap_raw is not None and isinstance(ap_raw, int):
                frame["fnum"] = round(ap_raw / 2000.0, 1)

    # Field 4, sub-field 4 = quaternion attitude
    if gps_buf and isinstance(gps_buf, (bytes, bytearray)):
        gps_fields_for_quat = _decode_all_fields(gps_buf)
        quat_bufs = _find_fields(gps_fields_for_quat, 4)
        # Typically two quaternion groups: flight (f_pry) and gimbal (g_pry)
        quats = []
        for qb in quat_bufs:
            if isinstance(qb, (bytes, bytearray)):
                qf = _decode_all_fields(qb)
                vals = []
                for i in range(1, 5):
                    v = _find_field(qf, i)
                    if v and isinstance(v, (bytes, bytearray)) and len(v) == 4:
                        vals.append(_bytes_to_float(v))
                    else:
                        vals.append(0.0)
                if len(vals) == 4:
                    quats.append(_quat_to_euler(*vals))
        if len(quats) >= 1:
            frame["f_pry"] = quats[0]
        if len(quats) >= 2:
            frame["g_pry"] = quats[1]

    return frame


# ── ffprobe DJI detection ────────────────────────────────────────

def _is_dji_stream(video_path, ffprobe_path=None):
    """Check if the file has a DJI metadata stream. Returns stream index or -1."""
    if ffprobe_path is None:
        ffprobe_path = os.path.join(_PROJECT_ROOT, "ffprobe.exe")

    cmd = [
        ffprobe_path, "-v", "error",
        "-show_entries", "stream=index:stream_tags=handler_name",
        "-of", "json", video_path,
    ]
    try:
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=15,
            creationflags=_HNO,
        )
        data = json.loads(res.stdout)
        for stream in data.get("streams", []):
            tags = stream.get("tags", {})
            handler = tags.get("handler_name", "")
            if "DJI" in handler.upper():
                return stream.get("index", 1)
    except Exception:
        pass
    return -1


# ── Main entry point ─────────────────────────────────────────────

def parse_dji_meta(video_path: str, ffmpeg_path: str = None) -> dict:
    """Extract flight data from DJI MOV file.

    Returns: {
        'is_dji': True,
        'device': 'DJIMavic3',
        'serial': '1581F45T...',
        'lens': 'Hasselblad L2D-20c',
        'home_lat': 25.036, 'home_lon': 121.064,
        'frames': [
            {'lat': 25.036, 'lon': 121.064, 'alt': 63.0,
             'iso': 100, 'shutter': 29, 'ev': 0.0, 'fnum': 2.8,
             'f_pry': (-3.9, -5.5, -134.7), 'g_pry': (2.5, 0.0, -134.6)},
            ...
        ]
    }
    """
    if ffmpeg_path is None:
        ffmpeg_path = os.path.join(_PROJECT_ROOT, "ffmpeg.exe")
    ffprobe_path = ffmpeg_path.replace("ffmpeg", "ffprobe")

    # Step 1: Detect DJI stream
    dji_stream = _is_dji_stream(video_path, ffprobe_path)
    if dji_stream < 0:
        return {"is_dji": False}

    # Step 2: Extract the DJI meta stream to a temp binary file
    tmp_fd, tmp_bin = tempfile.mkstemp(suffix=".bin", prefix="dji_meta_")
    os.close(tmp_fd)

    try:
        cmd = [
            ffmpeg_path, "-y", "-nostdin",
            "-i", video_path,
            "-map", f"0:{dji_stream}",
            "-f", "data",
            tmp_bin,
        ]
        res = subprocess.run(
            cmd, capture_output=True, timeout=30,
            creationflags=_HNO,
        )
        if res.returncode != 0 or not os.path.exists(tmp_bin):
            return {"is_dji": False}

        with open(tmp_bin, "rb") as f:
            raw = f.read()

        if len(raw) < 20:
            return {"is_dji": False}

    except Exception:
        return {"is_dji": False}
    finally:
        try:
            os.remove(tmp_bin)
        except OSError:
            pass

    # Step 3: Parse the binary Protobuf data
    try:
        root_fields = _decode_all_fields(raw)
    except Exception:
        return {"is_dji": False}

    result = {"is_dji": True, "device": "", "serial": "", "lens": "", "frames": []}

    # Parse header (field 1)
    header_buf = _find_field(root_fields, 1)
    if header_buf and isinstance(header_buf, (bytes, bytearray)):
        header_info = _parse_header(header_buf)
        result["device"] = header_info.get("device", "")
        result["serial"] = header_info.get("serial", "")
        result["lens"] = header_info.get("lens", "")

    # Parse frames (field 3, repeated)
    frame_bufs = _find_fields(root_fields, 3)
    for fb in frame_bufs:
        if isinstance(fb, (bytes, bytearray)):
            frame = _parse_frame(fb)
            result["frames"].append(frame)

    # Set home position from first frame
    if result["frames"]:
        first = result["frames"][0]
        result["home_lat"] = first["lat"]
        result["home_lon"] = first["lon"]
    else:
        result["home_lat"] = 0.0
        result["home_lon"] = 0.0

    return result
