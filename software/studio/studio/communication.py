"""Bounded, receipt-aware views of cumulative communication evidence.

These helpers never read hardware. The first counter observation is a baseline,
not sixty seconds of history; cached IPC replies and browser repaints add none.
"""
from collections import deque
import math
import time

WINDOW_S = 60.0
MAX_EVENTS = 4096


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def counter(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def empty_health():
    return {"asked": None, "ok": None, "missed": None, "rate": None, "observed_s": 0.0, "window_s": WINDOW_S}


def accepted_snapshot_age_ms(bus):
    """Commissioning timestamps share native state.started's monotonic origin."""
    if bus.get("mode") != "commissioning":
        return None
    sample = bus.get("last_sample") if isinstance(bus.get("last_sample"), dict) else {}
    updated, accepted = bus.get("updated_at_us"), sample.get("at_us")
    return (updated-accepted)/1000. if counter(updated) and counter(accepted) and updated >= accepted else None


class CounterWindow:
    def __init__(self):
        self.previous = None
        self.started = None
        self.previous_at = None
        self.events = deque(maxlen=MAX_EVENTS)

    @staticmethod
    def counts(value):
        if not isinstance(value, dict):
            return None
        values = tuple(value.get(k) for k in ("asked", "ok", "missed"))
        return values if all(counter(v) for v in values) and values[1] + values[2] == values[0] else None

    def update(self, counts, now):
        if counts is None:
            self.previous, self.started = None, None
            self.previous_at = None
            self.events.clear()
            return
        if self.previous is None or self.previous_at is not None and now-self.previous_at > WINDOW_S and counts != self.previous:
            self.events.clear()
            self.started = now
        else:
            delta = tuple(a-b for a, b in zip(counts, self.previous))
            if any(v < 0 for v in delta):
                self.events.clear()
                self.started = now
            elif delta[0] > 0:
                # At rates beyond the supported owner sampling rate, retain a
                # conservative observation start instead of an unbounded history.
                if len(self.events) == MAX_EVENTS:
                    self.started = self.events[0][0]
                self.events.append((now, *delta))
        if self.previous != counts:
            self.previous_at = now
        self.previous = counts

    def view(self, now):
        while self.events and self.events[0][0] <= now-WINDOW_S:
            self.events.popleft()
        if self.previous is None:
            return empty_health()
        asked, ok, missed = (sum(item[i] for item in self.events) for i in (1, 2, 3))
        return {"asked": asked, "ok": ok, "missed": missed,
                "rate": missed/asked if asked else None,
                "observed_s": round(min(WINDOW_S, max(0., now-self.started)), 3), "window_s": WINDOW_S}


class EvidenceAge:
    def __init__(self):
        self.token, self.at, self.age_ms = None, None, None

    def observe(self, token, receipt, age_ms, now):
        if token is not None and token != self.token and receipt is not None:
            self.token, self.at = token, receipt
            self.age_ms = max(0., age_ms) if number(age_ms) else None
        if self.at is None or self.age_ms is None:
            return None
        elapsed = max(0., now-self.at)*1000
        candidate = max(0., age_ms) + max(0., now-receipt)*1000 if number(age_ms) and receipt is not None else 0.
        return round(max(self.age_ms+elapsed, candidate), 1)


class Frequency:
    """Rate from unique accepted counter/timestamp observations, never repaint Hz."""
    def __init__(self):
        self.previous = None
        self.at = None
        self.events = deque(maxlen=MAX_EVENTS)

    def update(self, sequence, at, now, cumulative=False):
        if not counter(sequence) or at is None:
            return
        if self.previous is not None and sequence < self.previous:
            self.events.clear()
            self.previous, self.at = None, None
        if sequence != self.previous:
            if self.at is not None and at-self.at > WINDOW_S:
                self.events.clear()
                self.previous, self.at = None, None
            if self.at is not None and at > self.at:
                amount = sequence-self.previous if cumulative else 1
                self.events.append((at, amount, at-self.at))
            self.previous, self.at = sequence, at

    def view(self, now, fresh=True):
        while self.events and self.events[0][0] <= now-WINDOW_S:
            self.events.popleft()
        if not fresh or not self.events:
            return None
        elapsed = sum(item[2] for item in self.events)
        return round(sum(item[1] for item in self.events)/elapsed, 2) if elapsed else None


class ServoHealth:
    def __init__(self):
        self.identity = None
        self.windows, self.ages = {}, {}
        self.frequency = Frequency()
        self.source_age = EvidenceAge()

    @property
    def stored_events(self):
        return sum(len(w.events) for w in self.windows.values())

    def apply(self, snapshot, *, identity, receipt, token, counters, read_order, guard,
              row_tokens=None, source_age_ms=0., available=True, sequence=None, now=None, require_age=True):
        now = time.monotonic() if now is None else now
        valid = {r["id"]: CounterWindow.counts(r.get("comm")) for r in snapshot["rows"]}
        rollback = any(counts is not None and sid in self.windows and self.windows[sid].previous is not None
                       and any(a < b for a, b in zip(counts, self.windows[sid].previous))
                       for sid, counts in valid.items())
        if identity != self.identity or rollback:
            self.identity = identity
            self.windows, self.ages = {}, {}
            self.frequency, self.source_age = Frequency(), EvidenceAge()
        for row in snapshot["rows"]:
            sid = row["id"]
            window = self.windows.setdefault(sid, CounterWindow())
            window.update(valid[sid], receipt if receipt is not None else now)
            row["health"] = window.view(now)
            age = self.ages.setdefault(sid, EvidenceAge())
            row_token = (row_tokens or {}).get(sid, valid[sid][1] if valid[sid] is not None else token)
            row["age_ms"] = age.observe(row_token, receipt, row.get("age_ms"), now)
            limit = 3000. if snapshot.get("diagnostic") else 500.
            row["fresh"] = bool(available and row.get("fresh") and
                                (row["age_ms"] is not None and row["age_ms"] < limit or not require_age and row["age_ms"] is None))
        age_ms = self.source_age.observe(token, receipt, source_age_ms, now)
        self.frequency.update(sequence, receipt, now, cumulative=True)
        snapshot["communication"] = {"source": snapshot["source"], "session": identity[1],
            "age_ms": age_ms, "sample_hz": self.frequency.view(now, available and age_ms is not None and age_ms < 1500.),
            "window_s": WINDOW_S, "counters": counters, "read_order": read_order, "read_order_guard": guard}
        if snapshot.get("online") is not None:
            snapshot["online"] = sum(1 for row in snapshot["rows"] if row["fresh"])
        return snapshot


class ImuHealth:
    def __init__(self):
        self.identity = None
        self.frequency = Frequency()
        self.age = EvidenceAge()
        self.started = None

    def view(self, rd, *, source, info, sample):
        now = time.monotonic()
        bus = rd.bus.value if isinstance(rd.bus.value, dict) else {}
        native = bus.get("native") or {}
        last_sample = bus.get("last_sample") if isinstance(bus.get("last_sample"), dict) else {}
        if not sample:
            sample = last_sample.get("imu") if isinstance(last_sample.get("imu"), dict) else {}
        reader = native.get("imu_reader") or {}
        observation = native.get("imu_observation") or {}
        session = (native.get("telemetry") or {}).get("session", bus.get("session"))
        source = native.get("imu_source", sample.get("source", source))
        if source != "bus" or observation.get("source") not in (None, source):
            observation = {}
        if sample.get("source") not in (None, source):
            sample = {}
        raw = native.get("imu_raw", native.get("imu_full_raw")) if source == "bus" else None
        raw = raw if isinstance(raw, dict) else {}
        accepted_read_index = raw.get("read_index")
        if bus.get("mode") == "motion" and (counter(accepted_read_index) or number(native.get("sample_age_ms"))):
            observation = {}  # Startup diagnostics cannot override a current accepted control sample.
        info = info or (observation.get("info") if source == "bus" else None) or {}
        identity = (source, session, getattr(getattr(rd, "hello", None), "at", None), reader.get("epoch", sample.get("epoch")),
                    (native.get("read_order") or {}).get("generation"),
                    info.get("protocol"), info.get("board"), tuple(info.get("identity") or []))
        sequence = reader.get("samples", native.get("imu_sequence", sample.get("sequence", observation.get("sequence"))))
        # The freshness tracker's sequence can advance on a rejected packet.
        # Raw provenance is committed only with the accepted control sample.
        if source == "bus" and raw:
            sequence = raw.get("sequence")
        rate_basis = "accepted_sensor_counter" if counter(sequence) else None
        if source == "bus" and bus.get("mode") == "motion" and not raw and not number(native.get("imu_age_ms")):
            rate_basis = None  # A legacy freshness-tracker counter can include rejected packets.
        accepted_at_us = last_sample.get("at_us")
        if not counter(sequence) and counter(accepted_at_us) and bus.get("mode") == "commissioning":
            sequence, rate_basis = accepted_at_us, "accepted_snapshot_timestamp"
        if not counter(sequence) and counter(accepted_read_index):
            sequence, rate_basis = accepted_read_index, "accepted_host_read"
        identity += (rate_basis,)
        rollback = counter(sequence) and self.frequency.previous is not None and sequence < self.frequency.previous
        if identity != self.identity or rollback:
            self.identity, self.started = identity, now
            self.frequency, self.age = Frequency(), EvidenceAge()
        ready = native.get("imu_ready", sample.get("ready", observation.get("sensor_ready"))) is True
        valid = sample.get("valid", observation.get("sample_valid"))
        age_value = native.get("imu_age_ms", sample.get("age_ms", observation.get("age_ms")))
        reported_age = age_value if number(age_value) else None
        accepted_age = accepted_snapshot_age_ms(bus)
        age_basis = "sensor_reported" if number(age_value) else None
        if accepted_age is not None and not number(native.get("imu_age_ms")):
            age_value = accepted_age + (sample["age_ms"] if number(sample.get("age_ms")) else 0.)
            age_basis = "accepted_snapshot"
        elif bus.get("mode") == "motion" and number(native.get("sample_age_ms")):
            age_value = native["sample_age_ms"] + (reported_age if reported_age is not None else 0.)
            age_basis = "sensor_reported_plus_host_age" if reported_age is not None else "host_accepted_sample"
        token = ("accepted_read", accepted_read_index) if counter(accepted_read_index) else (
            sequence if counter(sequence) else (tuple(sample.get("quaternion_wxyz") or []), ready, valid))
        age_ms = self.age.observe(token, rd.bus.at, age_value, now)
        sensor_bound = native.get("imu_age_upper_bound_ms", raw.get("age_upper_bound_ms"))
        sensor_upper_ms = (round(age_ms+max(0., sensor_bound-(reported_age or 0.)), 1)
                           if age_ms is not None and number(sensor_bound) else None)
        connected = getattr(rd, "present", True) is not False and not rd.bus.error
        hello_at = getattr(getattr(rd, "hello", None), "at", None)
        connected = connected and (hello_at is None or rd.bus.at is not None and rd.bus.at >= hello_at)
        accepted = ready and valid is not False and observation.get("sample_fresh") is not False
        if connected and accepted and rate_basis is not None:
            self.frequency.update(sequence, rd.bus.at, now, cumulative=rate_basis == "accepted_sensor_counter")
        fresh = bool(connected and accepted and age_ms is not None and age_ms < 500.
                     and (sensor_upper_ms is None or sensor_upper_ms < 500.))
        reason = ("主程序连接已断开" if not connected else
                  "IMU 数据无效" if valid is False else
                  "IMU 尚未就绪" if not ready else
                  "IMU 数据未更新或已过期" if not fresh else None)
        counters = {k: reader.get(k) for k in ("samples", "errors", "dropped", "invalid", "stale")}
        health = {"source": source, "session": session, "age_ms": age_ms,
                  "update_hz": self.frequency.view(now, fresh), "observed_s": round(min(WINDOW_S, now-self.started), 3),
                  "rate_basis": rate_basis, "age_basis": age_basis,
                  "sensor_age_upper_bound_ms": sensor_upper_ms,
                  "ready": bool(connected and ready), "fresh": fresh, "valid": valid,
                  "reason": reason, "last_error": reader.get("last_error", observation.get("error")), "counters": counters}
        metadata = {"chip": info.get("chip", source if source not in (None, "bus") else None),
                    "board": info.get("board"), "protocol": info.get("protocol"),
                    "firmware": info.get("firmware", native.get("imu_firmware"))}
        return health, metadata
