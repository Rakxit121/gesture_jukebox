
# """Thin wrapper around MediaPipe Hands."""
# import cv2

# from gestures import Hand


# class HandTracker:
#     def __init__(self, complexity=1, detect_confidence=0.6, track_confidence=0.5):
#         import mediapipe as mp

#         self._hands = mp.solutions.hands.Hands(
#             static_image_mode=False,
#             max_num_hands=1,                 # one hand is enough and keeps the box unambiguous
#             model_complexity=complexity,     # 0 = faster, 1 = more accurate
#             min_detection_confidence=detect_confidence,
#             min_tracking_confidence=track_confidence,
#         )

#     def process(self, frame_bgr):
#         rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
#         rgb.flags.writeable = False
#         result = self._hands.process(rgb)
#         if not result.multi_hand_landmarks:
#             return None
#         landmarks = result.multi_hand_landmarks[0].landmark
#         return Hand([(p.x, p.y) for p in landmarks])

#     def close(self):
#         self._hands.close()




"""Hand tracking wrapper using MediaPipe Tasks API."""

from pathlib import Path

import cv2
import mediapipe as mp

from gestures import Hand


class HandTracker:
    def __init__(
        self,
        complexity=1,
        detect_confidence=0.6,
        track_confidence=0.5,
    ):
        model_path = Path(__file__).resolve().parent / "hand_landmarker.task"

        if not model_path.is_file():
            raise FileNotFoundError(
                f"Hand landmark model not found: {model_path}\n"
                "Download hand_landmarker.task and place it next to tracker.py."
            )

        self._mp = mp
        self._timestamp_ms = 0

        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=str(model_path)
            ),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=detect_confidence,
            min_hand_presence_confidence=detect_confidence,
            min_tracking_confidence=track_confidence,
        )

        self._hands = mp.tasks.vision.HandLandmarker.create_from_options(
            options
        )

    def process(self, frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=rgb,
        )

        # Video mode requires monotonically increasing timestamps.
        self._timestamp_ms += 1

        result = self._hands.detect_for_video(
            image,
            self._timestamp_ms,
        )

        if not result.hand_landmarks:
            return None

        landmarks = result.hand_landmarks[0]
        return Hand([(point.x, point.y) for point in landmarks])

    def close(self):
        self._hands.close()
