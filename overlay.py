"""Draws the tracking box, skeleton, swipe trail and progress bars on the camera frame."""
import cv2

from gestures import Pose

FONT = cv2.FONT_HERSHEY_SIMPLEX
IDLE, ACTIVE, DONE = (235, 180, 70), (60, 190, 255), (110, 220, 100)   # BGR: blue, amber, green

CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
               (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18),
               (18, 19), (19, 20), (0, 17)]


def _chip(frame, text, x, y, background, scale=0.55):
    (w, h), base = cv2.getTextSize(text, FONT, scale, 1)
    cv2.rectangle(frame, (x, y - h - base - 8), (x + w + 14, y), background, -1)
    cv2.putText(frame, text, (x + 7, y - base - 3), FONT, scale, (255, 255, 255), 1, cv2.LINE_AA)


def _corner_box(frame, box, colour, thickness=3):
    x0, y0, x1, y1 = box
    length = max(14, min(36, (x1 - x0) // 4, (y1 - y0) // 4))
    cv2.rectangle(frame, (x0, y0), (x1, y1), colour, 1)
    for (cx, cy, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        cv2.line(frame, (cx, cy), (cx + sx * length, cy), colour, thickness)
        cv2.line(frame, (cx, cy), (cx, cy + sy * length), colour, thickness)


def _bar(frame, x0, y, width, fraction, colour, centred=False):
    cv2.rectangle(frame, (x0, y), (x0 + width, y + 8), (40, 40, 40), -1)
    if centred:
        mid = x0 + width // 2
        cv2.rectangle(frame, (mid, y), (mid + int(fraction * width / 2), y + 8), colour, -1)
        cv2.line(frame, (mid, y - 3), (mid, y + 11), (200, 200, 200), 1)
    else:
        cv2.rectangle(frame, (x0, y), (x0 + int(fraction * width), y + 8), colour, -1)


def draw_overlay(frame, hand, status, flash=""):
    h, w = frame.shape[:2]

    if hand is not None:
        x0, y0, x1, y1 = hand.box()
        box = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
        busy = abs(status.swipe) > 0.25 or status.hold > 0.05
        colour = DONE if flash else (ACTIVE if busy else IDLE)

        pts = [(int(x * w), int(y * h)) for x, y in hand.points]
        for a, b in CONNECTIONS:
            cv2.line(frame, pts[a], pts[b], colour, 2, cv2.LINE_AA)
        for p in pts:
            cv2.circle(frame, p, 3, (255, 255, 255), -1, cv2.LINE_AA)

        _corner_box(frame, box, colour)
        label = status.pose.value if status.pose else "Hand"
        _chip(frame, label, box[0], box[1] - 4 if box[1] > 30 else box[1] + 28, colour)

        # swipe trail: where the palm has been over the last fraction of a second
        trail = [(int(x * w), int(y * h)) for x, y in status.trail]
        for i in range(1, len(trail)):
            cv2.line(frame, trail[i - 1], trail[i], ACTIVE, 1 + i * 4 // len(trail), cv2.LINE_AA)

        # progress bar under the box: swipe distance, or how long a pose has been held
        bar_w = max(140, box[2] - box[0])
        bar_x = (box[0] + box[2]) // 2 - bar_w // 2
        bar_y = min(box[3] + 12, h - 50)
        if status.pose == Pose.OPEN_PALM:
            _bar(frame, bar_x, bar_y, bar_w, status.swipe, ACTIVE, centred=True)
            cv2.putText(frame, "< prev      next >", (bar_x + bar_w // 2 - 62, bar_y + 24),
                        FONT, 0.45, (230, 230, 230), 1, cv2.LINE_AA)
        elif status.hold > 0:
            _bar(frame, bar_x, bar_y, bar_w, status.hold, ACTIVE)
            cv2.putText(frame, "keep holding", (bar_x, bar_y + 24), FONT, 0.45, (230, 230, 230), 1, cv2.LINE_AA)

    if status.fingers:   # five dots (thumb..pinky): handy for seeing why a pose was read the way it was
        for i, (name, up) in enumerate(zip("TIMRP", status.fingers)):
            cx = 24 + i * 26
            cv2.circle(frame, (cx, 24), 9, IDLE if up else (70, 70, 70), -1, cv2.LINE_AA)
            cv2.putText(frame, name, (cx - 5, 29), FONT, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

    strip = frame.copy()
    cv2.rectangle(strip, (0, h - 34), (w, h), (20, 20, 20), -1)
    cv2.addWeighted(strip, 0.65, frame, 0.35, 0, frame)
    cv2.putText(frame, status.hint, (12, h - 11), FONT, 0.55, (235, 235, 235), 1, cv2.LINE_AA)

    if flash:
        (tw, th), _ = cv2.getTextSize(flash, FONT, 1.3, 3)
        x = (w - tw) // 2
        cv2.rectangle(frame, (x - 20, 50), (x + tw + 20, 60 + th + 24), DONE, -1)
        cv2.putText(frame, flash, (x, 60 + th + 8), FONT, 1.3, (20, 40, 20), 3, cv2.LINE_AA)
