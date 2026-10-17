"""Hand pose + gesture logic.

Works on plain (x, y) landmark lists in 0..1 coordinates, so it has no
dependency on MediaPipe or a camera and can be tested with made-up hands.
"""
import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

# MediaPipe hand landmark indices
WRIST, THUMB_MCP, THUMB_TIP = 0, 2, 4
INDEX_MCP, MIDDLE_MCP = 5, 9
PALM_POINTS = (0, 5, 9, 13, 17)
FINGER_TIPS = (8, 12, 16, 20)
FINGER_PIPS = (6, 10, 14, 18)


class Pose(Enum):
    OPEN_PALM = "Open palm"
    FIST = "Fist"
    THUMBS_UP = "Thumbs up"
    THUMBS_DOWN = "Thumbs down"
    OTHER = "Hand"


class Gesture(Enum):
    NEXT = "next"
    PREVIOUS = "previous"
    PLAY_PAUSE = "play_pause"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


@dataclass
class Hand:
    points: List[Tuple[float, float]]   # 21 landmarks, normalised to the frame

    @property
    def palm_size(self):
        return max(_dist(self.points[WRIST], self.points[MIDDLE_MCP]), 1e-6)

    def center(self):
        xs = [self.points[i][0] for i in PALM_POINTS]
        ys = [self.points[i][1] for i in PALM_POINTS]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def box(self, pad=0.025):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (max(0.0, min(xs) - pad), max(0.0, min(ys) - pad),
                min(1.0, max(xs) + pad), min(1.0, max(ys) + pad))

    def fingers(self):
        """[thumb, index, middle, ring, pinky] -> True when the finger is stretched out."""
        wrist = self.points[WRIST]
        # A thumb counts as out when its tip is well away from the base of the index finger.
        states = [_dist(self.points[THUMB_TIP], self.points[INDEX_MCP]) > 0.6 * self.palm_size]
        # A finger counts as out when its tip is further from the wrist than its middle joint.
        # Comparing distances (not heights) means it works however the hand is rotated.
        for tip, pip in zip(FINGER_TIPS, FINGER_PIPS):
            states.append(_dist(self.points[tip], wrist) > 1.1 * _dist(self.points[pip], wrist))
        return states

    def pose(self):
        thumb, *others = self.fingers()
        out = sum(others)
        if out >= 3:
            return Pose.OPEN_PALM
        if out == 0:
            if not thumb:
                return Pose.FIST
            mcp, tip = self.points[THUMB_MCP], self.points[THUMB_TIP]
            vertical = (tip[1] - mcp[1]) / max(_dist(mcp, tip), 1e-6)   # image y grows downward
            if vertical < -0.6:
                return Pose.THUMBS_UP
            if vertical > 0.6:
                return Pose.THUMBS_DOWN
        return Pose.OTHER


@dataclass
class Status:
    """What the overlay needs to show the user what the program is 'thinking'."""
    pose: Optional[Pose] = None
    fingers: Optional[List[bool]] = None
    hint: str = "Show your hand to the camera"
    swipe: float = 0.0     # -1..1, progress towards a left/right swipe
    hold: float = 0.0      # 0..1, progress of a held pose
    trail: list = field(default_factory=list)


HINTS = {
    Pose.OPEN_PALM: "Open palm: swipe left or right to change song",
    Pose.FIST: "Fist: hold to play / pause",
    Pose.THUMBS_UP: "Thumbs up: hold for volume up",
    Pose.THUMBS_DOWN: "Thumbs down: hold for volume down",
    Pose.OTHER: "Open palm to swipe, fist to pause, thumbs for volume",
}


class GestureEngine:
    def __init__(self, swipe_distance=0.28):
        self.swipe_distance = swipe_distance    # fraction of the frame width the hand must travel
        self.window = 0.6                       # seconds of movement considered for a swipe
        self.min_speed = 0.45                   # frame-widths per second; slow drifting isn't a swipe
        self.swipe_cooldown = 0.6
        self.return_guard = 1.3                 # ignore the hand coming back the other way
        self.stable_frames = 3                  # a pose must last this many frames to count
        self.hold_times = {Pose.FIST: 0.7, Pose.THUMBS_UP: 0.35, Pose.THUMBS_DOWN: 0.35}
        self.repeat_every = 0.7                 # volume repeats while a thumb is held

        self.status = Status()
        self._track = deque()
        self._candidate, self._candidate_count = None, 0
        self._pose, self._pose_since = None, 0.0
        self._static_fired_at = None
        self._last_swipe = (float("-inf"), 0)   # (time, direction)
        self._last_seen = float("-inf")

    def update(self, hand, now):
        """Feed the tracked hand (or None). Returns a Gesture when one just happened."""
        if hand is None:
            if now - self._last_seen > 0.3:     # tolerate a dropped frame or two
                self._track.clear()
                self._candidate, self._pose = None, None
            self.status = Status()
            return None

        self._last_seen = now
        pose = self._stable_pose(hand.pose(), now) or Pose.OTHER
        status = Status(pose=pose, fingers=hand.fingers(), hint=HINTS[pose])

        if pose == Pose.OPEN_PALM:
            gesture = self._swipe(hand.center(), now, status)
        else:
            self._track.clear()
            gesture = self._hold(pose, now, status)

        self.status = status
        return gesture

    def _stable_pose(self, raw, now):
        """Debounce: the raw pose flickers between frames, so wait until it settles."""
        if raw == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate, self._candidate_count = raw, 1
        if self._candidate_count >= self.stable_frames and raw != self._pose:
            self._pose, self._pose_since = raw, now
            self._static_fired_at = None
        return self._pose

    def _hold(self, pose, now, status):
        need = self.hold_times.get(pose)
        if need is None:
            return None

        held = now - self._pose_since
        status.hold = min(1.0, held / need)
        if held < need:
            return None

        if pose == Pose.FIST:
            if self._static_fired_at is not None:    # one toggle per fist, not one per frame
                return None
            self._static_fired_at = now
            return Gesture.PLAY_PAUSE

        if self._static_fired_at is None or now - self._static_fired_at >= self.repeat_every:
            self._static_fired_at = now
            return Gesture.VOLUME_UP if pose == Pose.THUMBS_UP else Gesture.VOLUME_DOWN
        return None

    def _swipe(self, center, now, status):
        self._track.append((now, center[0], center[1]))
        while self._track and now - self._track[0][0] > self.window:
            self._track.popleft()
        status.trail = [(x, y) for _, x, y in self._track]
        if len(self._track) < 5:
            return None

        t0, x0, y0 = self._track[0]
        t1, x1, y1 = self._track[-1]
        dx, dy = x1 - x0, y1 - y0
        horizontal = abs(dx) > 1.5 * abs(dy)
        status.swipe = max(-1.0, min(1.0, dx / self.swipe_distance)) if horizontal else 0.0

        direction = 1 if dx > 0 else -1
        xs = [p[1] for p in self._track]
        steps = [b - a for a, b in zip(xs, xs[1:])]
        one_way = sum(1 for s in steps if s * direction > 0) >= 0.7 * len(steps)
        speed = abs(dx) / max(t1 - t0, 1e-3)

        if abs(dx) < self.swipe_distance or abs(dx) < 2 * abs(dy) or speed < self.min_speed or not one_way:
            return None

        last_time, last_direction = self._last_swipe
        since = now - last_time
        if since < self.swipe_cooldown or (direction != last_direction and since < self.return_guard):
            self._track.clear()   # that was the hand returning, not a new swipe
            return None

        self._last_swipe = (now, direction)
        self._track.clear()
        # The camera image is mirrored, so right on screen is the user's right.
        return Gesture.NEXT if direction > 0 else Gesture.PREVIOUS
