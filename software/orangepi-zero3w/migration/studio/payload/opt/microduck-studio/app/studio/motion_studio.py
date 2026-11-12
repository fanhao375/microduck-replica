"""One webpage-owned recorder/player, serviced by PadOps' existing safety tick.

The send lock orders state transitions with output. The state lock never spans disk
access. Invalidation from event-loop callbacks only marks the session and wakes the
tick; every later output rechecks that mark while holding the send lock.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import threading
import uuid

from .motion_store import CLEANUP, HEAD_ZERO, MAX_BYTES, MAX_EVENTS, MAX_SECONDS, MotionStore, name, validate_event
from .pad_combo import Refused
from .motion_posture import PostureTracker, evidence, phase_evidence
from .audio_interaction import MusicRhythm
from .rpc import Timeout, Unreachable

HEARTBEAT_S = .5
SKILL_UNSUPPORTED = "模型技能还没有可验证的完成状态：首版录制不支持技能、连滚或含技能的组合动作"
CONTROL_OPS = {"move", "sticks", "stop", "keys", "mode", "key", "skill", "sound", "action_start", "action_hold", "action_stop"}
PREP_OPS = {"stand", "walk", "walk_stop", "sit", "start", "reboot", "drive_mode"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def policy_identity(pad):
    p = pad._policies() or {}
    # Readiness and progress are telemetry; changing either must not change identity.
    slots = [{k: v for k, v in slot.items() if k in {"slot", "path", "sha256", "model", "duration", "config", "hash"}}
             for slot in p.get("slots") or [] if isinstance(slot, dict)]
    return {"mode": p.get("mode"), "slots": slots, "skills": p.get("skills"),
            "v": pad._limits["v"], "w": pad._limits["w"],
            "head": pad._inputs["head"], "mouth": pad._inputs["mouth"],
            "config": getattr(pad, "_motion_config_digest", None)}


def fingerprint(pad, identity=None):
    """Hash readable model contents again on each start, even at the same path."""
    if identity is None:
        with pad._lk:
            identity = copy.deepcopy(policy_identity(pad))
    files = {}
    for slot in identity["slots"]:
        path = slot.get("path")
        if not isinstance(path, str) or not path:
            continue
        h = hashlib.sha256()
        try:
            with Path(path).open("rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(block)
            files[path] = h.hexdigest()
        except OSError:
            files[path] = None
    return _digest({"control": identity, "models": files})


class Lively:
    """Small weighted gestures, adapted from servo_ops.GESTURES into policy intents."""
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.origin, self.target = [0.] * 5, [0.] * 5
        self.start, self.end, self.pause_until = None, None, 0.
        self.previous = None

    def sample(self, now, head_limit, mouth_percent):
        if self.start is None or now >= self.pause_until:
            choices = ["tilt"] * 3 + ["look"] * 3 + ["nod", "shake", "mouth", "curious", "scan"] * 2 + ["twitch"]
            kind = self.rng.choice([c for c in choices if c != self.previous])
            self.previous = kind
            sign = self.rng.choice((-1, 1))
            # neck, pitch, yaw, roll in radians, followed by normalized mouth.
            gestures = {"tilt": [0., 0., .14 * sign, .24 * sign, 0.],
                        "look": [.06, -.04, .50 * sign, -.08 * sign, 0.],
                        "nod": [.04, .21 * sign, 0., 0., 0.],
                        "shake": [0., 0., .34 * sign, 0., 0.],
                        "mouth": [0., 0., 0., 0., .70],
                        "curious": [-.04, -.13, .14 * sign, .21 * sign, .42],
                        "scan": [.04, .10, .31 * sign, 0., 0.],
                        "twitch": [0., 0., .10 * sign, -.10 * sign, 0.]}
            self.target = gestures[kind]
            self.start = now
            self.end = now + self.rng.uniform(.9, 1.9)
            self.pause_until = self.end + self.rng.uniform(.3, 1.5)
        length = self.end - self.start
        t = max(0., min(1., (now - self.start) / length))
        # A smooth out-and-back excursion; pauses keep the neutral target.
        scale = math.sin(math.pi * t) ** 2 if now < self.end else 0.
        vals = [v * scale for v in self.target]
        h = {k: max(-head_limit, min(head_limit, v)) for k, v in zip(HEAD_ZERO, vals)}
        return (("robot.head", h), ("robot.mouth", {"open": min(mouth_percent / 100., vals[4])}))


class MotionStudio:
    def __init__(self, pad):
        self.pad = pad
        self.store = MotionStore(pad.state_dir, pad.log)
        self.recordings = self.store.summaries()
        self.session, self.draft = None, None
        self.state, self.epoch = "idle", 0
        self.last_result, self.error = None, None
        self._pending_cleanup = {}
        self._outputs = []
        self._storage_busy = False
        self._skill_pending, self._skill_reset_seen = False, False
        self._skill_fence = 0
        self._request_local = threading.local()
        self.posture = PostureTracker()

    def background_music(self, conn=None):
        """Only standalone music owns a server-managed, socket-bound lifetime."""
        with self.pad._lk:
            s = self.session
            return bool(s and self.state == "idle_motion" and s.get("idle_kind") == "music"
                        and s.get("lively") and not s.get("invalid")
                        and (conn is None or s["conn"] == conn))

    def _heartbeat_expired(self, s, now):
        return not self.background_music() and now - s["heartbeat"] > HEARTBEAT_S

    def _music_ready_why(self, now):
        p = self.pad
        audio = getattr(p, "audio", None)
        source = audio.snapshot() if audio is not None else {}
        if (source.get("available") is not True or source.get("listening") is not True
                or source.get("music") is not True or source.get("error")):
            return "监听、音乐模式或语音服务已关闭；重新开启后请再点「随乐律动」"
        try:
            _, phase, sitting = phase_evidence(p._robot or {}, now)
        except Refused:
            return "随乐律动需要新鲜的策略阶段和坐姿回读"
        if phase not in ("walk", "stand") or sitting:
            return "随乐律动需要站稳并准备好走"
        status = p._status
        if (not isinstance(status, dict) or not isinstance(status.get("pads"), list)
                or p._status_at is None or not 0 <= now - p._status_at < 15
                or p._status_error or (p._robot or {}).get("pad_status_error")):
            return "实体手柄状态读不出：随乐律动已停止，请恢复状态后重新开始"
        if any(not isinstance(row, dict) or type(row.get("connected")) is not bool
               for row in status["pads"]):
            return "实体手柄状态不明确：请恢复状态后重新开始"
        if any(row["connected"] for row in status["pads"]):
            return "实体手柄连着：先关闭实体手柄"
        return None

    def posture_allowed(self):
        s = self.session
        return bool(s and s.get("posture_control") and self.state in
                    ("recording", "record_paused", "playing", "play_paused")
                    and (self.posture.pending or s.get("posture_target") == "sit"))

    def _posture_why(self, view, now):
        """A controlled posture permits its expected state, never arbitrary drift.

        Invalid evidence cancels the web session permanently. The independent
        tracker can later verify robotd completion and release its safety fence.
        Legacy sessions without posture steps keep their original telemetry path.
        """
        s = self.session
        if not s or not s.get("posture_control"):
            return None
        try:
            _, phase, sitting, _, _ = evidence(view, now)
        except Refused as e:
            why = f"坐站完成回读中断：{e}；已取消后续动作"
            if self.posture.pending:
                self.posture.error = self.posture.error or why
            return why
        target = s.get("posture_target")
        if not self.posture.pending and target is not None:
            matches = phase == "sit" and sitting if target == "sit" else phase in ("walk", "stand") and not sitting
            if not matches:
                return "姿态在坐站步骤后被改变：已取消后续动作"
        return None

    def begin_posture(self, target, manual=False):
        """Fence before a toggle can leave the process; caller owns send ordering."""
        with self.pad._lk:
            p, now = self.pad, self.pad.clock()
            view = p._robot or {}
            why = p._base_block(now) or p._relax_block(now)
            bus = view.get("bus") or {}
            if (why or view.get("present") is not True or bus.get("policy_enabled") is not True
                    or (bus.get("native") or {}).get("torque_state_confirmed") is not True
                    or (p._policies() or {}).get("enabled") is not True):
                raise Refused(why or "坐站需要实时策略启用和扭矩确认")
            if "sit_toggle" not in (p._known_skills() or []):
                raise Refused("这只鸭子没装坐站模型")
            if self.session:
                if manual:
                    raise Refused("手动坐站前必须先取消自动会话")
                s = self.session
                if (s.get("invalid") or now - s["heartbeat"] > HEARTBEAT_S or s["conn"] in p._gone
                        or s["conn"] in p._closed or p._pad_attached(now)[0] is not False):
                    raise Refused(s.get("invalid") or "页面已离开、心跳过期或实体手柄接管，坐站请求已取消")
            token = self.posture.begin(target, view, now, manual=manual)
            if token is not None and self.session:
                self.session["posture_control"] = True
                self.session.setdefault("posture_target", "sit" if (p._policies() or {}).get("sitting") else "stand")
                token["session"] = self.session
            return token

    def posture_reply(self, token, accepted, origin=None, reason=None):
        with self.pad._lk:
            if token is None or self.posture.pending is not token:
                return
            if accepted is True:
                self.posture.accepted(token, self.pad.clock())
                if self.session is not None and self.session is token.get("session"):
                    self.session["posture_target"] = token["target"]
                self.accepted_from(origin, "studio.posture", {"target": token["target"]})
            elif accepted is False:
                self.posture.rejected(token)
            else:
                self.posture.uncertain(token, reason or "主程序没有确认", self.pad.clock())
                self.invalidate(self.posture.error)
            self.pad._tick_evt.set()

    @contextmanager
    def request(self):
        previous = getattr(self._request_local, "origin", None)
        with self.pad._lk:
            self._request_local.origin = {"epoch": self.epoch, "session": self.session}
        try:
            yield
        finally:
            self._request_local.origin = previous

    def request_current(self):
        origin = getattr(self._request_local, "origin", None)
        return origin is None or (origin["epoch"] == self.epoch and origin["session"] is self.session)

    def _refresh_request(self):
        origin = getattr(self._request_local, "origin", None)
        if origin is not None:
            origin.update(epoch=self.epoch, session=self.session)

    def note_skill(self):
        with self.pad._lk:
            previous = self._skill_pending, self._skill_reset_seen
            self._skill_fence += 1
            self._skill_pending, self._skill_reset_seen = True, False
            self.invalidate("模型技能已发出，但没有可验证的完成状态")
            return self._skill_fence, previous

    def rejected_skill(self, fence):
        with self.pad._lk:
            if fence is not None and self._skill_fence == fence[0]:
                self._skill_pending, self._skill_reset_seen = fence[1]

    def recording_origin(self):
        with self.pad._lk:
            return (self.session, self.epoch) if self.session and self.state == "recording" else None

    def accepted_from(self, origin, method, params):
        with self.pad._lk:
            if origin is not None and self.session is origin[0] and self.epoch == origin[1]:
                self.accepted(method, params)

    @staticmethod
    def _channel(method, params):
        return (method, params.get("tag")) if method == "robot.sound" else method

    def _cleanup_failed(self, method, params):
        key = self._channel(method, params)
        previous = self._pending_cleanup.get(key)
        attempt = min(4, previous["attempt"] + 1 if previous else 1)
        self._pending_cleanup[key] = {"method": method, "params": dict(params), "attempt": attempt,
                                      "due": self.pad.clock() + min(8., 2. ** (attempt - 1))}

    def output_accepted(self, method, params):
        # A newer authorized intent owns this channel and its normal cleanup now.
        # Retrying an old neutral target must never overwrite that newer intent.
        with self.pad._lk:
            self._pending_cleanup.pop(self._channel(method, params), None)

    def policy_feed(self, view):
        """A freshly observed disabled-to-ready policy cycle fences prior skills.

        An RPC stop response never clears this flag, nor does a guessed duration.
        No automation sends enable/disable requests to manufacture this boundary.
        """
        self.posture.feed(view, self.pad.clock())
        why = self._posture_why(view, self.pad.clock())
        if why:
            self.invalidate(why)
        if not self._skill_pending:
            return
        p = view.get("policies") or {}
        b = view.get("bus") or {}
        if (view.get("bus_at") is None or self.pad.clock() - view["bus_at"] > 3
                or view.get("paused") or view.get("present") is not True):
            return
        # robot.policies.enabled is the startup [policy] configuration, not the
        # live enable flag. Only bus.policy_enabled observes an actual stop.
        if b.get("policy_enabled") is False:
            self._skill_reset_seen = True
        elif (self._skill_reset_seen and p.get("enabled") is True and p.get("homed") is True
                and b.get("policy_enabled") is True and b.get("homed") is True):
            self._skill_pending, self._skill_reset_seen = False, False

    @contextmanager
    def _ordered(self):
        with self.pad._send_lk:
            try:
                yield
            finally:
                # Callers have left their state-lock context before this runs.
                self._flush_outputs()

    def _flush_outputs(self):
        outputs, self._outputs = self._outputs, []
        for method, params, target, timestamp, cleanup in outputs:
            with self.pad._lk:
                s = self.session
                if method is None:
                    # Complete only after approved tail events and their cleanup have
                    # been dispatched. An invalidation may change the result, never
                    # restore the old session or suppress neutral cleanup.
                    if s is target:
                        self.session, self.state = None, "idle"
                        self.last_result = s.get("invalid") or ("页面超过 500 毫秒没有动作心跳"
                            if self._heartbeat_expired(s, self.pad.clock()) else "回放完成")
                    continue
                if not cleanup and self.pad._update_blocked():
                    self.invalidate('软件 / 主程序更新保护仍在')
                    continue
                if not cleanup and (s is not target or s.get("invalid")
                                    or self._heartbeat_expired(s, self.pad.clock())):
                    continue
                if not cleanup and policy_identity(self.pad) != target["identity"]:
                    self.invalidate("模型或控制配置已改变")
                    continue
                if not cleanup and self.background_music():
                    # The previous send can block while real evidence goes stale.
                    # Music has no page heartbeat, so fence each queued send with
                    # its actual safety evidence before it leaves the process.
                    now = self.pad.clock()
                    why = self.ready_why(s["conn"], now) or self._music_ready_why(now)
                    if why:
                        self.invalidate(why)
                        continue
            if method == "studio.posture":
                try:
                    self._execute_posture(target, params["target"])
                except Refused as e:
                    with self.pad._lk:
                        self.error = str(e)
                        self.invalidate(str(e))
                continue
            try:
                if self.pad.robot is None:
                    raise Unreachable("robotd 未配置")
                self.pad.robot.notify(method, dict(params))
            except (Unreachable, Timeout) as e:
                with self.pad._lk:
                    self.error = f"动作指令没发出去：{e}"
                    if cleanup:
                        self._cleanup_failed(method, params)
                    elif self.session is target:
                        self.invalidate(self.error)
                continue
            self.output_accepted(method, params)
            if target is not None and timestamp is not None:
                with self.pad._lk:
                    self._append(target, method, params, timestamp, force=cleanup)

    def elapsed(self, now=None):
        s = self.session
        if s is None:
            return self.draft["duration_s"] if self.draft else 0.
        return min(MAX_SECONDS, s["base"] + ((self.pad.clock() if now is None else now) - s["anchor"]
                                           if not s.get("posture_barrier") and
                                           self.state in ("recording", "playing", "idle_motion") else 0.))

    def ready_why(self, conn, now, starting=False, posture_start=False):
        p = self.pad
        if p._closing:
            return "Studio 正在关闭"
        if conn is None or conn in p._gone or (conn in p._closed and
                                               (starting or not self.background_music(conn))):
            return "页面不在手柄控制区：滚到遥控区再开始"
        attached, why = p._pad_attached(now)
        if attached is not False:
            return "实体手柄连着或状态读不出：先关闭实体手柄" + (f"（{why}）" if why else "")
        base = p._base_block(now) or p._relax_block(now)
        if base:
            return base
        allow_posture = posture_start or (not starting and self.posture_allowed())
        if self.posture.pending and (starting or not allow_posture):
            return self.posture.error or "当前坐站仍会完成：等新鲜回读确认，或手动停下策略再开始走"
        if self._skill_pending:
            if self._skill_reset_seen:
                return "已确认策略停下：点击上方「开始走」，等新鲜回读确认「准备好走」后再开始"
            return "上个模型技能没有可验证的完成状态：先手动停下策略、再重新开始走，等新鲜回读确认准备好"
        pol = p._policies() or {}
        b = (p._robot or {}).get("bus") or {}
        if (p._posture() not in (("policy", "walking", "sitting") if allow_posture else ("policy", "walking")) or pol.get("enabled") is not True
                or (not allow_posture and pol.get("sitting") is not False) or pol.get("homed") is not True
                or b.get("policy_enabled") is not True or b.get("homed") is not True
                or (b.get("native") or {}).get("torque_state_confirmed") is not True):
            return "先让鸭子站稳并进入「准备好走」（策略在跑）"
        if p._job_alive() or p._cmd_busy:
            return "还有指令或配对任务没结束，等它完成"
        held = p._lease_holder(now)
        if held is not None and held["conn"] != conn:
            return f"{held['by'] or '另一个页面'}正在遥控：先接管"
        if starting and (p._action is not None or any(not st.get("end") and st["held"] for st in p._keys.values())):
            return "先松开动作和按住的键，等动作结束"
        if starting and p._web_driving:
            return "先松开摇杆，站好后再开始"
        return None

    def snapshot(self, conn, now):
        s = self.session
        why = self.ready_why(conn, now, starting=True)
        if self.draft:
            why = why or "还有尚未保存的草稿：先保存或丢弃"
        if self._storage_busy:
            why = why or "正在保存或整理动作文件"
        record_why, idle_why = why, why
        if s:
            if not (self.state == "idle_motion" and s["conn"] == conn):
                record_why = record_why or "已有录制或回放会话"
            if not (self.state == "recording" and s["conn"] == conn and not s.get("lively")):
                idle_why = idle_why or "已有录制、回放或灵动会话"
        owner_conn = s["conn"] if s else (self.draft.get("conn") if self.draft else None)
        owner_by = s["by"] if s else (self.draft.get("by") if self.draft else None)
        draft = None if self.draft is None else {k: self.draft[k] for k in ("duration_s", "events", "reason")}
        if draft:
            draft["events"] = len(draft["events"])
        return {"state": self.state, "session_id": s["id"] if s else None,
                "owner": None if owner_conn is None else {"mine": owner_conn == conn or self._orphan_draft(conn), "by": owner_by},
                "elapsed_s": round(self.elapsed(now), 3), "duration_s": s.get("duration", 0.) if s else self.elapsed(now),
                "loop": s.get("loop", 0) if s else 0, "loops": s.get("loops", 1) if s else 1,
                "recordings": copy.deepcopy(self.recordings), "draft": draft,
                "library_warning": (f"有 {self.store.skipped} 段动作记录损坏或格式不合法，已略过；其他记录可用"
                                    if self.store.skipped else None),
                "record_available": {"ok": record_why is None, "why": record_why},
                "idle_available": {"ok": idle_why is None, "why": idle_why}, "last_result": self.last_result,
                "error": self.error, "idle_running": bool(s and s.get("lively") and not s.get("invalid")
                                                            and self.state in ("idle_motion", "recording")),
                "idle_kind": s.get("idle_kind", "lively") if s and s.get("lively") else None,
                "idle_phase": ("moving" if s["lively"].moving else "waiting")
                              if s and self.state == "idle_motion" and s.get("idle_kind") == "music"
                              and s.get("lively") else None,
                "posture_step": self.posture.snapshot()}

    def invalidate(self, why, conn=None):
        """Safe from event-loop callbacks: no network or filesystem work."""
        with self.pad._lk:
            s = self.session
            if s and (conn is None or s["conn"] == conn):
                s["invalid"] = s.get("invalid") or why
                self.pad._tick_evt.set()

    def _own(self, conn):
        owner = self.session or self.draft
        if owner and owner["conn"] != conn:
            if self._orphan_draft(conn):
                self.draft["conn"] = conn
                return
            raise Refused(f"{owner.get('by') or '另一个页面'}拥有这次会话：先接管遥控")

    def _orphan_draft(self, conn):
        if self.session is not None or not self.draft or conn is None or conn in self.pad._gone or conn in self.pad._closed:
            return False
        owner = self.draft["conn"]
        driver = self.pad._driver
        return (owner in self.pad._gone or owner in self.pad._closed
                or (driver is not None and driver["conn"] == conn and driver["conn"] != owner))

    def _new(self, conn, by, fp, identity):
        self.epoch += 1
        now = self.pad.clock()
        self.session = {"id": uuid.uuid4().hex, "conn": conn, "by": by, "heartbeat": now,
                        "anchor": now, "base": 0., "fingerprint": fp, "identity": copy.deepcopy(identity),
                        "events": [], "bytes": 0, "invalid": None, "lively": None}
        self.error, self.last_result = None, None
        self.pad._take_lease(conn, by, now)
        self.pad._tick_evt.set()
        return {"session_id": self.session["id"]}

    def _boundary(self, record=False, conn=None):
        """Called under send lock. Fence late actions/keys before neutral output."""
        p, s = self.pad, self.session
        boundary_t = round(self.elapsed(), 6)
        conn = s["conn"] if s else conn
        p._action_stop(conn, "动作录制 / 回放到达停止边界")
        if conn in p._keys:
            p._keys.pop(conn, None)
        p._x_armed.pop(conn, None)
        p._modes.pop(conn, None)
        for kind in p._latch:
            if (p._latch[kind] or {}).get("conn") == conn:
                p._latch[kind] = None
        if conn is not None:
            q = p._seq_of(conn)
            q["stop"] = max(q["stop"], q["hi"])
        p._web_driving = False
        p.virtual.update(last=[0., 0., 0.], at=p.clock())
        for method, params in CLEANUP:
            self._outputs.append((method, dict(params), s if record else None,
                                  boundary_t if record else None, True))
        self.epoch += 1
        return boundary_t

    def _finish(self, reason):
        s = self.session
        if s is None:
            return
        meaningful = any(self._meaningful(e) for e in s["events"])
        elapsed = self._boundary(record=self.state == "recording" and meaningful)
        self.session = None
        if self.posture.pending:
            reason += "；当前坐站仍会完成，后续动作已取消"
        self.last_result = reason
        if meaningful:
            self.draft = dict(s, duration_s=max(elapsed, s["events"][-1]["t"]), reason=reason)
            self.state = "draft"
        else:
            self.state = "idle"
            self.last_result = ((reason + "；") if self.posture.pending else "") + "没有有效动作，没有生成草稿"

    def _stop(self, reason):
        if self.state in ("recording", "record_paused"):
            self._finish(reason)
        elif self.session:
            self._boundary()
            self.session = None
            self.state = "idle"
            self.last_result = reason + ("；当前坐站仍会完成，后续动作已取消" if self.posture.pending else "")

    @staticmethod
    def _meaningful(event):
        method, p = event["method"], event["params"]
        if method == "studio.posture":
            return True
        if method == "robot.sound":
            return p.get("hold") is not False
        return any(abs(v) > 1e-9 for k, v in p.items() if k != "active")

    def accepted(self, method, params, force=False):
        """Only called after robotd output succeeds; refused input never gets here."""
        with self.pad._lk:
            self.output_accepted(method, params)
            s = self.session
            if not s or self.state != "recording" or (s.get("invalid") and not force):
                return
            if not force and policy_identity(self.pad) != s["identity"]:
                self.invalidate("模型或控制配置已改变")
                return
            event = {"t": round(self.elapsed(), 6), "method": method, "params": copy.deepcopy(params)}
            self._append(s, method, params, event["t"], force=force)

    def _append(self, s, method, params, timestamp, force=False):
        event = {"t": timestamp, "method": method, "params": copy.deepcopy(params)}
        try:
            validate_event(event)
        except Refused:
            return
        cost = len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if not force and (len(s["events"]) >= MAX_EVENTS - len(CLEANUP) or s["bytes"] + cost > MAX_BYTES - 4096):
            s["invalid"] = "已达到事件数量或文件大小上限"
            self.pad._tick_evt.set()
            return
        if len(s["events"]) < MAX_EVENTS:
            s["events"].append(event)
            s["bytes"] += cost
            if self.draft and self.draft["events"] is s["events"]:
                self.draft["duration_s"] = max(self.draft["duration_s"], timestamp)

    def before(self, args, conn, by):
        op = args.get("op")
        # Releases/old hold timers from an earlier action must not terminate a new automation.
        if op in ("action_hold", "action_stop"):
            return
        if op == "mode" and (args.get("hb") or args.get("mode") == self.pad._mode_of(conn)):
            return
        if op not in CONTROL_OPS | PREP_OPS:
            return
        with self._ordered():
            with self.pad._lk:
                if not self.request_current():
                    return False
                s = self.session
                if not s:
                    return
                if policy_identity(self.pad) != s["identity"]:
                    self._stop("模型或控制配置已改变")
                    self._refresh_request()
                    return
                if op in PREP_OPS:
                    if self.state == "recording" and op in ("sit", "stand"):
                        self._own(conn)
                        if self.posture.pending:
                            if self.posture.pending["target"] != op:
                                self._stop("手动坐站恢复接管，录制已结束")
                                self._refresh_request()
                                return
                            raise Refused(self.posture.error or "当前坐站仍会完成：请等待关节稳定")
                        # Only sitstand, never init/enable, belongs in a recording.
                        if op == "stand" and (self.pad._policies() or {}).get("sitting") is not True:
                            raise Refused("录制中的站起只接受坐姿起身；请先坐下")
                        if s.get("lively"):
                            s["lively"] = None
                            self._boundary(record=True)
                            self._refresh_request()
                        return
                    self._stop("准备状态被改变，录制 / 回放已结束")
                    self._refresh_request()
                    return
                self._own(conn)
                if self._storage_busy:
                    raise Refused("正在保存或整理动作文件，等它完成")
                if self.state == "record_paused":
                    raise Refused("录制已暂停：先点「继续录制」再操作")
                if self.state in ("playing", "play_paused", "idle_motion"):
                    self._stop("手动操作接管，自动动作已停止")
                    self._refresh_request()
                    return
                if self.state == "recording":
                    if self.posture.pending:
                        raise Refused(self.posture.error or "当前坐站仍会完成：请等待关节稳定")
                    combo = self.pad._find_combo(args.get("id")) if op == "action_start" else None
                    key = args.get("key")
                    skill_key = op == "key" and (key in {"a", "x", "lb", "rb"} or
                        (key == "dpad_down" and self.pad._bind_map()[key]["skill"] not in ("", "sit_toggle")))
                    if (op == "skill" or skill_key or (op == "keys" and "x" in (args.get("held") or []))
                            or (combo and any(s["do"] == "skill" for s in combo["steps"]))):
                        raise Refused(SKILL_UNSUPPORTED)
                    if s.get("lively") and op not in ("action_hold", "action_stop"):
                        s["lively"] = None
                        self._boundary(record=True)
                        self._refresh_request()

    def handle(self, args, conn, by):
        action = args.get("action")
        idle_kind = args.get("kind", "lively")
        if action == "idle_start":
            if idle_kind not in ("lively", "music"):
                raise Refused("律动类型只能选择灵动或音乐")
            if idle_kind == "music":
                audio = getattr(self.pad, "audio", None)
                status = audio.snapshot() if audio is not None else {}
                if not status.get("available") or not status.get("listening") or not status.get("music"):
                    raise Refused("先在语音面板开启「听音乐」，等待服务连接")
        supplied_token = args.get("session_id")
        with self.pad._lk:
            entry_session, entry_epoch = self.session, self.epoch
            if supplied_token is not None and action != "heartbeat":
                if self.session is None or self.session["id"] != supplied_token:
                    return {"dropped": "旧的动作会话"}
        if action == "heartbeat":
            with self.pad._lk:
                s = self.session
                if (not s or s["conn"] != conn or s["id"] != args.get("session_id") or s.get("invalid")
                        or conn in self.pad._closed or conn in self.pad._gone):
                    return {"dropped": "旧的或已关闭的动作会话"}
                if self.background_music(conn):
                    return {"session_id": s["id"]}
                if self._heartbeat_expired(s, self.pad.clock()):
                    s["invalid"] = "页面超过 500 毫秒没有动作心跳"
                    self.pad._tick_evt.set()
                    return {"dropped": s["invalid"]}
                s["heartbeat"] = self.pad.clock()
                self.pad._touch_lease(conn, s["heartbeat"])
                return {"session_id": s["id"]}
        if action in ("save", "rename", "delete"):
            return self._storage(action, args, conn)
        fp = None
        hashed_identity = None
        clip = None
        if action in ("record_start", "play", "idle_start", "record_resume", "resume"):
            # File reads never hold the state lock or delay the heartbeat fast pool.
            self.pad._refresh_files()
            with self.pad._lk:
                hashed_identity = copy.deepcopy(policy_identity(self.pad))
            fp = fingerprint(self.pad, hashed_identity)
            if action == "play":
                clip = self.store.get(args.get("id"))
                if type(args.get("loops", 1)) is not int or args.get("loops", 1) not in (1, 3, 5):
                    raise Refused("回放次数只能是 1、3 或 5")
                if clip["fingerprint"] != fp:
                    raise Refused("模型或控制配置已改变：请重新录制")
                postures = [e["params"]["target"] for e in clip["events"] if e["method"] == "studio.posture"]
                if args.get("loops", 1) > 1 and postures and postures[-1] != "stand":
                    raise Refused("多次循环需要最后录回站姿；请补录站起后再循环")
        with self._ordered():
            with self.pad._lk:
                # Starts/resumes can hash large models without locks. Their target must
                # remain the exact session/epoch that existed when the request arrived.
                if self.session is not entry_session or self.epoch != entry_epoch:
                    return {"dropped": "动作会话已经改变，旧请求已丢弃"}
                if hashed_identity is not None and policy_identity(self.pad) != hashed_identity:
                    self._stop("模型或控制配置在校验期间改变")
                    raise Refused("模型或控制配置在校验期间改变：请重新操作")
                if supplied_token is not None and (self.session is None or self.session["id"] != supplied_token):
                    return {"dropped": "旧的动作会话"}
                self._own(conn)
                if self._storage_busy:
                    raise Refused("正在保存或整理动作文件，等它完成")
                s = self.session
                if s and (s.get("invalid") or self._heartbeat_expired(s, self.pad.clock())):
                    why = s.get("invalid") or "页面超过 500 毫秒没有动作心跳，会话已经结束"
                    self._stop(why)
                    raise Refused(why)
                if action in ("record_start", "play", "idle_start"):
                    if self.draft:
                        raise Refused("还有尚未保存的草稿：先保存或丢弃")
                    combine = s and s["conn"] == conn and ((action == "record_start" and self.state == "idle_motion")
                                or (action == "idle_start" and self.state == "recording"))
                    if s and not combine:
                        raise Refused("已有录制或回放会话：先停止")
                    if action == "play" and (self.pad._policies() or {}).get("sitting") is True:
                        first = next((e for e in clip["events"] if self._meaningful(e)), None)
                        if first is None or first["method"] != "studio.posture":
                            raise Refused("这段动作在坐站步骤前有连续动作：请先站稳再回放")
                    why = self.ready_why(conn, self.pad.clock(), starting=True,
                                         posture_start=action == "play" and any(e["method"] == "studio.posture" for e in clip["events"]))
                    if why:
                        raise Refused(why)
                    if action == "idle_start" and idle_kind == "music" and not combine:
                        why = self._music_ready_why(self.pad.clock())
                        if why:
                            raise Refused(why)
                    if combine:
                        if action == "record_start":
                            self.epoch += 1
                            s.update(anchor=self.pad.clock(), base=0., events=[], bytes=0, fingerprint=fp,
                                     identity=copy.deepcopy(hashed_identity), id=uuid.uuid4().hex, heartbeat=self.pad.clock())
                            self.state = "recording"
                        else:
                            # Starting a producer is a new request generation even
                            # when the recording keeps its public session token.
                            self._boundary(record=True)
                            s["lively"] = MusicRhythm(self.pad.audio.snapshot) if idle_kind == "music" else Lively()
                            s["idle_kind"] = idle_kind
                        return {"session_id": s["id"]}
                    self._boundary(conn=conn)
                    result = self._new(conn, by, fp, hashed_identity)
                    s = self.session
                    if action == "record_start":
                        self.state = "recording"
                    elif action == "idle_start":
                        self.state = "idle_motion"
                        s["lively"] = MusicRhythm(self.pad.audio.snapshot) if idle_kind == "music" else Lively()
                        s["idle_kind"] = idle_kind
                    else:
                        self.state = "playing"
                        s.update(clip=clip, duration=clip["duration_s"], loop=1, loops=args.get("loops", 1), index=0,
                                 posture_control=any(e["method"] == "studio.posture" for e in clip["events"]),
                                 posture_target="sit" if (self.pad._policies() or {}).get("sitting") else "stand")
                    return result
                if action in ("record_pause", "pause"):
                    wanted = "recording" if action == "record_pause" else "playing"
                    if self.state != wanted:
                        raise Refused("当前会话不能暂停")
                    elapsed = self._boundary(record=wanted == "recording")
                    s["base"], s["lively"] = elapsed, None
                    self.state = "record_paused" if wanted == "recording" else "play_paused"
                    if self.posture.pending:
                        self.last_result = "已暂停后续动作；当前坐站仍会完成，完成后才能继续"
                elif action in ("record_resume", "resume"):
                    wanted = "record_paused" if action == "record_resume" else "play_paused"
                    if self.state != wanted or s is None or s.get("invalid"):
                        raise Refused("会话已经结束或状态改变，不能继续")
                    if self.posture.pending:
                        raise Refused(self.posture.error or "当前坐站仍会完成：完成并确认关节稳定后才能继续")
                    why = self.ready_why(conn, self.pad.clock())
                    if why or fp != s["fingerprint"]:
                        self._stop(why or "模型或配置改变")
                        raise Refused(why or "模型或配置改变：请重新录制")
                    s["anchor"] = self.pad.clock()
                    self.state = "recording" if wanted == "record_paused" else "playing"
                    if self.state == "playing" and not s.get("posture_barrier"):
                        self._restore_latches(s)
                elif action == "record_finish":
                    if self.state not in ("recording", "record_paused"):
                        raise Refused("现在没有正在录制的动作")
                    self._finish("录制结束，尚未保存")
                elif action == "discard":
                    if self.state != "draft":
                        raise Refused("现在没有待丢弃的草稿")
                    self.draft, self.state = None, "idle"
                elif action == "idle_stop":
                    if s and s.get("lively"):
                        if self.state == "recording":
                            s["lively"] = None
                            self._boundary(record=True)
                        else:
                            self._stop("灵动已停止")
                elif action == "stop":
                    if s is None:
                        # A foreground start may still be hashing outside locks.
                        self.epoch += 1
                    self._stop("页面停止了动作会话")
                else:
                    raise Refused(f"不认识的动作录制操作：{action}")
                return {"state": self.state, "session_id": self.session["id"] if self.session else None}

    def _storage(self, action, args, conn):
        with self.pad._lk:
            self._own(conn)
            if self._storage_busy:
                raise Refused("正在保存或整理动作文件，等它完成")
            draft = self.draft
            if self.session:
                raise Refused("录制或回放进行中：先停止再整理记录")
            if action == "save" and draft is None:
                raise Refused("现在没有待保存的草稿")
            if action == "save":
                clip = {"version": 2 if any(e["method"] == "studio.posture" for e in draft["events"]) else 1,
                        "id": uuid.uuid4().hex, "name": name(args.get("name")),
                        "created": datetime.now(timezone.utc).isoformat(), "duration_s": draft["duration_s"],
                        "fingerprint": draft["fingerprint"], "events": copy.deepcopy(draft["events"])}
            self._storage_busy = True
        try:
            if action == "save":
                self.store.save(clip)
            elif action == "rename":
                self.store.rename(args.get("id"), args.get("name"))
            else:
                self.store.delete(args.get("id"))
            rows = self.store.summaries()
        except Exception:
            with self.pad._lk:
                self._storage_busy = False
            raise
        with self.pad._lk:
            self.recordings = rows
            if action == "save" and self.draft is draft:
                self.draft, self.state = None, "idle"
            self.error = None
            self.last_result = {"save": "动作已保存", "rename": "名称已更新", "delete": "动作已删除"}[action]
            self._storage_busy = False
        return {"saved": action == "save", "recordings": rows}

    def tick(self):
        p = self.pad
        with self._ordered():
            with p._lk:
                s = self.session
                timeout = self.posture.check_timeout(p.clock())
                if timeout and s:
                    self.invalidate(timeout)
                if self._pending_cleanup and p.robot is not None and not self.posture.pending:
                    for pending in self._pending_cleanup.values():
                        if pending["due"] <= p.clock():
                            self._outputs.append((pending["method"], pending["params"], None, None, True))
                if s is None:
                    return
                now = p.clock()
                why = s.get("invalid") or self._posture_why(p._robot or {}, now) or self.ready_why(s["conn"], now)
                if self._heartbeat_expired(s, now):
                    why = why or "页面超过 500 毫秒没有动作心跳"
                if self.background_music():
                    why = why or self._music_ready_why(now)
                if policy_identity(p) != s["identity"]:
                    why = why or "模型或控制配置已改变"
                if why:
                    self._stop(why)
                    return
                p._touch_lease(s["conn"], now)
                if self.state == "recording" and self.elapsed(now) >= MAX_SECONDS:
                    self._finish("已达到 60 秒上限，尚未保存")
                    return
                if s.get("posture_barrier"):
                    if self.posture.pending or self.state != "playing":
                        return
                    s.update(posture_barrier=False, anchor=now)
                    return
                if self.posture.pending:
                    return
                if self.state in ("recording", "idle_motion") and s.get("lively"):
                    producer = s["lively"]
                    outputs = producer.sample(now, p._inputs["head"], p._inputs["mouth"])
                    if self.state == "idle_motion" and s.get("idle_kind") == "music" and not producer.source_active:
                        # Source shutdown ends this lease; rhythm uncertainty only waits.
                        # The session boundary owns neutral cleanup, exactly once.
                        self._stop("监听、音乐模式或语音服务已关闭；重新开启后请再点「随乐律动」")
                        return
                    for method, params in outputs:
                        self._send(method, params)
                elif self.state == "playing":
                    self._play_tick(s, now)

    def _send(self, method, params):
        self._outputs.append((method, params, self.session,
                              round(self.elapsed(), 6) if self.state == "recording" else None, False))

    def _restore_latches(self, s):
        latest = {}
        for index, event in enumerate(s["clip"]["events"]):
            if event["t"] > s["base"]:
                break
            s["index"] = index + 1
            if event["method"] in ("robot.head", "robot.pose", "robot.mouth"):
                latest[event["method"]] = event
        for event in sorted(latest.values(), key=lambda e: e["t"]):
            self._send(event["method"], event["params"])

    def _play_tick(self, s, now):
        elapsed = self.elapsed(now)
        events = s["clip"]["events"]
        due = {}
        # Coalesce missed continuous samples; never burst through old motion/sounds.
        while s["index"] < len(events) and events[s["index"]]["t"] <= elapsed:
            e = events[s["index"]]
            s["index"] += 1
            if e["method"] == "studio.posture":
                for previous in sorted(due.values(), key=lambda x: x["t"]):
                    self._send(previous["method"], previous["params"])
                # Zero continuous channels before handing a transition to robotd.
                for method, params in CLEANUP:
                    self._send(method, dict(params))
                self._send(e["method"], e["params"])
                s.update(base=e["t"], anchor=now, posture_barrier=True)
                return
            if e["method"] != "robot.sound" or elapsed - e["t"] <= .12 or e["params"].get("hold") is False:
                due[(e["method"], e["params"].get("tag") if e["method"] == "robot.sound" else None)] = e
        for e in sorted(due.values(), key=lambda x: x["t"]):
            if self.session is not s:
                return
            method, params = e["method"], e["params"]
            self._send(method, params)
        if elapsed >= s["duration"] and self.session is s:
            self._boundary()
            if s["loop"] >= s["loops"]:
                self._outputs.append((None, None, s, None, False))
            else:
                # Next loop starts on a future tick with fresh checks; no catch-up loops.
                s.update(loop=s["loop"] + 1, index=0, base=0., anchor=now)

    def _execute_posture(self, s, target):
        # Called under the send lock, outside the state lock: heartbeats stay fast.
        with self.pad._lk:
            why = self.ready_why(s["conn"], self.pad.clock())
            if why:
                raise Refused(why)
            if "sit_toggle" not in (self.pad._known_skills() or []):
                raise Refused("这只鸭子没装坐站模型")
        self.pad._call("回放坐下" if target == "sit" else "回放站起", "robot.do", {"skill": "sit_toggle"},
                       s["by"], posture_target=target, ordered=True)

    def close(self):
        with self._ordered():
            with self.pad._lk:
                self._stop("Studio 关闭了")
