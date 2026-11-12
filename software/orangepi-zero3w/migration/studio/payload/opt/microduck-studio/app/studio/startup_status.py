"""Read-only startup diagnostics. These observations never establish motion readiness."""
import math
import re

from .estop import STARTING_PHASES

OBSERVATION_MAX_AGE_MS = 3000
TELEMETRY = ("position_raw", "speed_raw", "load_raw", "current_raw", "volts", "temperature_c")
_CALIBRATION_ERROR = re.compile(r"calibrat|motion_enabled|IMU mounting", re.I)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _age(value):
    return value if _number(value) and value >= 0 else None


def imu_context_current(rd, now=None):
    """Only the current daemon's successful, recent bus reply can bind metadata."""
    age = _age(rd.bus.age(now))
    hello_at = getattr(getattr(rd, "hello", None), "at", None)
    return (getattr(rd, "present", None) is True and getattr(rd.bus, "error", None) is None
            and age is not None and age * 1000 < OBSERVATION_MAX_AGE_MS
            and (hello_at is None or (rd.bus.at is not None and rd.bus.at >= hello_at)))


def imu_diagnostic(rd, now=None):
    """Read-only ID 200 evidence, never an input to control or calibration.

    The native age survives repeated RPC replies; the receipt age covers a stalled
    Studio connection. A new hello makes the previous daemon's snapshot unusable.
    """
    bus = rd.bus.value if isinstance(rd.bus.value, dict) else {}
    native = bus.get("native") if isinstance(bus.get("native"), dict) else {}
    obs = native.get("imu_observation")
    obs = obs if isinstance(obs, dict) else {}
    out = {"status": "unknown", "responding": None, "info": None, "age_ms": None,
           "sample_valid": None, "sensor_ready": None, "sample_fresh": None,
           "sequence": None, "error": None}
    receipt_s, wire_ms = _age(rd.bus.age(now)), _age(obs.get("age_ms"))
    hello_at = getattr(getattr(rd, "hello", None), "at", None)
    if (getattr(rd, "present", None) is not True or getattr(rd.bus, "error", None) is not None
            or (hello_at is not None and (rd.bus.at is None or rd.bus.at < hello_at))
            or native.get("imu_source") != "bus" or obs.get("source") != "bus"
            or obs.get("attempted") is not True or receipt_s is None or wire_ms is None):
        return out
    out["age_ms"] = round(receipt_s * 1000 + wire_ms, 3)
    if out["age_ms"] >= OBSERVATION_MAX_AGE_MS:
        out["status"] = "stale"
        return out
    responding = obs.get("responding")
    if not isinstance(responding, bool):
        return out
    configured = native.get("configured_imu_profile", bus.get("configured_imu_profile"))
    if (configured not in ("legacy", "qinmo")
            or ("configured_imu_profile" in bus and bus["configured_imu_profile"] != configured)):
        return out
    info = obs.get("info")
    # Only known protocol/board pairs and the selected profile identify a board.
    pairs = {("feetech", "ft6"), ("feetech", "imu_to_dxl"),
             ("dxl2", "sflp_124_12"), ("feetech", "qinmo")}
    known = (isinstance(info, dict) and (info.get("protocol"), info.get("board")) in pairs
             and info.get("profile") == configured
             and (info.get("board") == "qinmo") == (configured == "qinmo")
             and isinstance(info.get("identity"), list)
             and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 255
                     for x in info["identity"]))
    if info is not None and not known:
        return out
    out["responding"] = responding
    out["error"] = obs.get("error") if isinstance(obs.get("error"), str) else None
    if not responding:
        out["status"] = "no_response"
        return out
    if not known:
        return out
    out["info"] = {key: info[key] for key in ("protocol", "board", "identity", "profile")}
    valid, ready, fresh = (obs.get(key) for key in ("sample_valid", "sensor_ready", "sample_fresh"))
    if any(value is not None and not isinstance(value, bool) for value in (valid, ready, fresh)):
        return out
    out.update(sample_valid=valid, sensor_ready=ready, sample_fresh=fresh,
               sequence=obs.get("sequence") if isinstance(obs.get("sequence"), int)
               and not isinstance(obs.get("sequence"), bool) and obs["sequence"] >= 0 else None)
    out["status"] = "stale" if fresh is False else "identified" if valid is True and ready is not False else "data_wait"
    return out


def startup_wait(bus):
    """Describe the actual startup wait, keeping unknown bus failures generic."""
    if not isinstance(bus, dict) or bus.get("phase") not in STARTING_PHASES:
        return None
    error = str(bus.get("error") or "")
    if _CALIBRATION_ERROR.search(error):
        return None
    native = bus.get("native") if isinstance(bus.get("native"), dict) else {}
    if re.search(r"\bimu(?:\s*200)?\b", error, re.I):
        source = native.get("imu_source")
        source = source if isinstance(source, str) and source else None
        return {"kind": "imu", "text": "等待 IMU 数据" + (f"（来源：{source}）" if source else ""),
                "source": source}
    if bus.get("phase") == "waiting_for_bus":
        return {"kind": "bus", "text": "等待总线设备响应", "source": None}
    return None


def servo_observations(bus, *, present, bus_age_s, bus_error=None):
    """Normalize native startup evidence; add all three ages before trusting a row.

    Returning None outside startup preserves the existing motion/commissioning path.
    Within startup an empty/expired result is unknown, never zero responding servos.
    """
    if not isinstance(bus, dict) or bus.get("phase") not in STARTING_PHASES:
        return None
    native = bus.get("native") if isinstance(bus.get("native"), dict) else {}
    obs = native.get("servo_observations")
    obs = obs if isinstance(obs, dict) else {}
    receipt_ms = None if _age(bus_age_s) is None else bus_age_s * 1000
    snapshot_ms = _age(obs.get("age_ms"))
    usable = (present is True and bus_error is None and receipt_ms is not None
              and snapshot_ms is not None and obs.get("attempted") is True)
    base_age = receipt_ms + snapshot_ms if usable else None
    rows = []
    for row in obs.get("rows") if isinstance(obs.get("rows"), list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int) or isinstance(row.get("id"), bool):
            continue
        ages = {key: None if base_age is None or _age(row.get(key)) is None else base_age + row[key]
                for key in ("check_age_ms", "reply_age_ms", "feedback_age_ms")}
        checked = ages["check_age_ms"] is not None and ages["check_age_ms"] < OBSERVATION_MAX_AGE_MS
        replied = ages["reply_age_ms"] is not None and ages["reply_age_ms"] < OBSERVATION_MAX_AGE_MS
        responding = row.get("responding") if checked and isinstance(row.get("responding"), bool) else None
        if responding is True and not replied:
            responding = None
        feedback = (responding is True and ages["feedback_age_ms"] is not None
                    and ages["feedback_age_ms"] < OBSERVATION_MAX_AGE_MS)
        rows.append({"id": row["id"], "name": row.get("name"), "responding": responding,
                     "feedback_fresh": feedback, **ages,
                     **{key: row.get(key) if feedback and _number(row.get(key)) else None for key in TELEMETRY}})
    known = any(row["responding"] is not None for row in rows)
    return {"rows": rows, "online": sum(row["responding"] is True for row in rows) if known else None}


def sampled_joint_feedback(bus, *, receipt_age_s=None, bus_error=None):
    """Recent commissioning/read-only feedback for display, never motion readiness.

    One failed cycle does not erase fifteen replies from 20 ms earlier. Conversely,
    repeated cached replies must expire using their native sample timestamp.
    """
    if not isinstance(bus, dict):
        return None
    if bus.get("mode") == "motion":
        native = bus.get("native") or {}
        rows = native.get("joints")
        if not isinstance(rows, list):
            return None
        receipt = _age(receipt_age_s)
        if receipt is None:
            return None  # Legacy dictionary callers retain native freshness semantics.
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            native_age = _age(row.get("sample_age_ms"))
            age = native_age + receipt * 1000 if native_age is not None and receipt is not None else None
            recent = (bus_error is None and receipt is not None and receipt < .5
                      and (age < 500 if age is not None else row.get("feedback_fresh") is True))
            out.append(dict(row, feedback_fresh=recent, sample_age_ms=age))
        return out
    if bus.get("mode") not in ("read_only", "commissioning"):
        return None
    sample = bus.get("last_sample")
    sample = sample if isinstance(sample, dict) else {}
    published, captured = _age(bus.get("updated_at_us")), _age(sample.get("at_us"))
    receipt = _age(receipt_age_s)
    age_ms = (receipt * 1000 + (published - captured) / 1000
              if receipt is not None and published is not None and captured is not None and published >= captured else None)
    fresh = bus_error is None and age_ms is not None and age_ms < 500
    return [dict(row, name=row.get("joint"), volts=row.get("voltage_v"),
                 feedback_fresh=fresh, sample_age_ms=age_ms)
            for row in sample.get("servos", []) if isinstance(row, dict)
            and isinstance(row.get("id"), int) and row["id"] in (10,11,12,13,14,20,21,22,23,24,30,31,32,33,34)]
