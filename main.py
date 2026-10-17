"""Gesture Jukebox: control YouTube or Spotify with your hand.

    python main.py                  # YouTube, songs from playlist.txt
    python main.py --mode spotify
    python main.py --mode demo      # no accounts needed: just try the gestures
"""
import argparse
import queue
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor

import cv2
from PIL import Image, ImageTk

from gestures import Gesture, GestureEngine
from overlay import draw_overlay
from players import create_player
from tracker import HandTracker

BG, PANEL, CARD, TEXT, MUTED = "#0e1116", "#151a22", "#1e2630", "#e6edf3", "#8b98a9"
ACCENT, GOOD = "#4da3ff", "#5fd38d"

ACTIONS = {   # gesture -> (text shown on screen, player method)
    Gesture.NEXT: ("NEXT >>", "next"),
    Gesture.PREVIOUS: ("<< PREVIOUS", "previous"),
    Gesture.PLAY_PAUSE: ("PLAY / PAUSE", "play_pause"),
    Gesture.VOLUME_UP: ("VOLUME UP", "volume_up"),
    Gesture.VOLUME_DOWN: ("VOLUME DOWN", "volume_down"),
}
SOURCES = [("YouTube", "youtube"), ("Spotify", "spotify"), ("Demo", "demo")]


def parse_args():
    p = argparse.ArgumentParser(description="Control music with hand gestures")
    p.add_argument("--mode", choices=[s[1] for s in SOURCES], default="youtube")
    p.add_argument("--playlist", default="playlist.txt", help="YouTube mode: links and 'search:' lines")
    p.add_argument("--browser", choices=["chrome", "edge"], default="chrome")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--swipe", type=float, default=0.28, help="swipe distance as a fraction of the frame")
    p.add_argument("--complexity", type=int, choices=[0, 1], default=1, help="0 = faster, 1 = more accurate")
    return p.parse_args()


def short(exc):
    lines = str(exc).strip().splitlines()
    return (lines[0] if lines else exc.__class__.__name__)[:90]


class JukeboxApp:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.player, self.mode = None, args.mode
        self.starting = False
        self.engine = GestureEngine(args.swipe)
        self.actions = ThreadPoolExecutor(max_workers=1)   # runs player calls in order, off the UI thread
        self.frames = queue.Queue(maxsize=1)
        self.messages = queue.Queue()
        self.flash = ("", 0.0)
        self.running = True
        self._tick_pending = False
        self._last_panel = 0.0
        self._photo = None

        self._build_ui()
        self._start_player(self.mode)
        threading.Thread(target=self._vision_loop, daemon=True).start()
        self._refresh()

    # ------------------------------------------------------------------ UI
    def _button(self, parent, text, command, width=None, primary=False):
        button = tk.Button(parent, text=text, command=command, width=width, relief="flat", bd=0,
                           bg=ACCENT if primary else CARD, fg="#08131f" if primary else TEXT,
                           activebackground="#6bb4ff" if primary else "#2a3441", activeforeground=TEXT,
                           font=("Segoe UI", 10, "bold"), padx=10, pady=8, cursor="hand2")
        return button

    def _build_ui(self):
        root = self.root
        root.title("Gesture Jukebox")
        root.configure(bg=BG)
        root.geometry("1280x720")
        root.minsize(1000, 620)

        self.canvas = tk.Canvas(root, bg="#000000", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True, padx=(14, 7), pady=14)
        self.image_item = self.canvas.create_image(0, 0, anchor="center")
        self.canvas_text = self.canvas.create_text(0, 0, text="Starting camera...", fill=MUTED,
                                                   font=("Segoe UI", 14))

        side = tk.Frame(root, bg=PANEL, width=340)
        side.pack(side="right", fill="y", padx=(7, 14), pady=14)
        side.pack_propagate(False)

        tk.Label(side, text="Gesture Jukebox", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=18, pady=(16, 0))
        tk.Label(side, text="Control music with your hand", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w", padx=18)

        # source switcher
        row = tk.Frame(side, bg=PANEL)
        row.pack(fill="x", padx=18, pady=(14, 8))
        self.source_buttons = {}
        for label, mode in SOURCES:
            b = self._button(row, label, lambda m=mode: self._switch_source(m))
            b.pack(side="left", expand=True, fill="x", padx=2)
            self.source_buttons[mode] = b
        self._highlight_source()

        # now playing
        card = tk.Frame(side, bg=CARD)
        card.pack(fill="x", padx=18, pady=6)
        tk.Label(card, text="NOW PLAYING", bg=CARD, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(
            anchor="w", padx=14, pady=(12, 0))
        self.title_label = tk.Label(card, text="", bg=CARD, fg=TEXT, font=("Segoe UI", 13, "bold"),
                                    wraplength=290, justify="left", height=3, anchor="nw")
        self.title_label.pack(fill="x", padx=14, pady=(2, 0))
        self.state_label = tk.Label(card, text="", bg=CARD, fg=GOOD, font=("Segoe UI", 10, "bold"))
        self.state_label.pack(anchor="w", padx=14)
        self.volume_bar = tk.Canvas(card, height=6, bg="#2a3441", highlightthickness=0)
        self.volume_bar.pack(fill="x", padx=14, pady=(8, 2))
        self.volume_label = tk.Label(card, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 9))
        self.volume_label.pack(anchor="w", padx=14, pady=(0, 12))

        # manual controls
        controls = tk.Frame(side, bg=PANEL)
        controls.pack(fill="x", padx=18, pady=6)
        for text, gesture, primary in (("Prev", Gesture.PREVIOUS, False), ("Play / Pause", Gesture.PLAY_PAUSE, True),
                                       ("Next", Gesture.NEXT, False)):
            self._button(controls, text, lambda g=gesture: self._perform(g), primary=primary).pack(
                side="left", expand=True, fill="x", padx=2)
        controls2 = tk.Frame(side, bg=PANEL)
        controls2.pack(fill="x", padx=18)
        self._button(controls2, "Vol -", lambda: self._perform(Gesture.VOLUME_DOWN)).pack(
            side="left", expand=True, fill="x", padx=2)
        self._button(controls2, "Vol +", lambda: self._perform(Gesture.VOLUME_UP)).pack(
            side="left", expand=True, fill="x", padx=2)

        # sensitivity
        self.swipe_label = tk.Label(side, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.swipe_label.pack(fill="x", padx=18, pady=(12, 0))
        scale = tk.Scale(side, from_=0.12, to=0.45, resolution=0.01, orient="horizontal", showvalue=False,
                         bg=PANEL, troughcolor=CARD, highlightthickness=0, bd=0, fg=TEXT,
                         command=self._set_swipe)
        scale.set(self.args.swipe)
        scale.pack(fill="x", padx=18)

        # guide
        guide = ("Open palm, swipe right:   next song\n"
                 "Open palm, swipe left:   previous song\n"
                 "Fist (hold):   play / pause\n"
                 "Thumbs up (hold):   volume up\n"
                 "Thumbs down (hold):   volume down")
        tk.Label(side, text=guide, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), justify="left",
                 anchor="w").pack(fill="x", padx=18, pady=(10, 4))

        # activity log
        self.log_box = tk.Text(side, height=6, bg=CARD, fg=TEXT, relief="flat", bd=0, font=("Segoe UI", 9),
                               state="disabled", padx=10, pady=8)
        self.log_box.pack(fill="both", expand=True, padx=18, pady=(4, 16))

        keys = {"<space>": Gesture.PLAY_PAUSE, "<Right>": Gesture.NEXT, "<Left>": Gesture.PREVIOUS,
                "<Up>": Gesture.VOLUME_UP, "<Down>": Gesture.VOLUME_DOWN}
        for key, gesture in keys.items():
            root.bind(key, lambda _e, g=gesture: self._perform(g))
        root.bind("<Escape>", lambda _e: self.close())
        root.protocol("WM_DELETE_WINDOW", self.close)

    def _highlight_source(self):
        for mode, button in self.source_buttons.items():
            selected = mode == self.mode
            button.configure(bg=ACCENT if selected else CARD, fg="#08131f" if selected else TEXT)

    def _set_swipe(self, value):
        self.engine.swipe_distance = float(value)
        self.swipe_label.configure(text=f"Swipe distance: {float(value):.0%}  (lower = easier)")

    def _log(self, text):
        self.messages.put(text)

    # -------------------------------------------------------------- players
    def _switch_source(self, mode):
        if mode != self.mode or self.player is None:
            self.mode = mode
            self._highlight_source()
            self._start_player(mode)

    def _start_player(self, mode):
        old, self.player, self.starting = self.player, None, True
        self._log(f"Starting {mode}...")

        def work():
            if old is not None:
                old.close()
            try:
                player = create_player(mode, self.args.playlist, self.args.browser)
                if mode == self.mode:
                    self.player = player
                    self._log(f"{player.name} ready")
                else:
                    player.close()
            except Exception as exc:
                self._log(f"Could not start {mode}: {short(exc)}")
            finally:
                self.starting = False

        threading.Thread(target=work, daemon=True).start()

    def _perform(self, gesture):
        label, method = ACTIONS[gesture]
        self.flash = (label, time.monotonic() + 0.9)
        self._log(label.replace(">>", "").replace("<<", "").strip().title())
        self.actions.submit(self._call, method)

    def _call(self, method):
        player = self.player
        if player is None:
            self._log("Player isn't ready yet")
            return
        try:
            getattr(player, method)()
        except Exception as exc:
            self._log(f"{player.name}: {short(exc)}")

    def _tick(self):
        try:
            if self.player is not None:
                self.player.tick()
        except Exception as exc:
            self._log(f"{self.player.name}: {short(exc)}")
        finally:
            self._tick_pending = False

    # --------------------------------------------------------- camera loop
    def _vision_loop(self):
        capture = cv2.VideoCapture(self.args.camera)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
        if not capture.isOpened():
            self._log(f"Could not open camera {self.args.camera}")
            return
        tracker = HandTracker(self.args.complexity)
        last_tick = 0.0

        while self.running:
            ok, frame = capture.read()
            if not ok:
                time.sleep(0.05)
                continue
            frame = cv2.flip(frame, 1)   # mirror: moving your hand right moves it right on screen
            now = time.monotonic()

            hand = tracker.process(frame)
            gesture = self.engine.update(hand, now)
            if gesture:
                self._perform(gesture)

            label = self.flash[0] if now < self.flash[1] else ""
            draw_overlay(frame, hand, self.engine.status, label)

            try:
                self.frames.get_nowait()   # drop an unseen frame: always show the newest
            except queue.Empty:
                pass
            self.frames.put(frame)

            if now - last_tick > 1.2 and not self._tick_pending:
                last_tick, self._tick_pending = now, True
                self.actions.submit(self._tick)

        capture.release()
        tracker.close()

    # ------------------------------------------------------------ UI update
    def _refresh(self):
        if not self.running:
            return
        try:
            self._show(self.frames.get_nowait())
        except queue.Empty:
            pass
        now = time.monotonic()
        if now - self._last_panel > 0.2:
            self._last_panel = now
            self._update_panel()
        self.root.after(15, self._refresh)

    def _show(self, frame):
        width, height = max(self.canvas.winfo_width(), 2), max(self.canvas.winfo_height(), 2)
        scale = min(width / frame.shape[1], height / frame.shape[0])
        resized = cv2.resize(frame, (int(frame.shape[1] * scale), int(frame.shape[0] * scale)))
        self._photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)))
        self.canvas.coords(self.image_item, width // 2, height // 2)
        self.canvas.itemconfigure(self.image_item, image=self._photo)
        self.canvas.itemconfigure(self.canvas_text, state="hidden")

    def _update_panel(self):
        player = self.player
        if player is None:
            self.title_label.configure(text="Starting..." if self.starting else "Not connected")
            self.state_label.configure(text="")
            volume = None
        else:
            self.title_label.configure(text=player.title)
            self.state_label.configure(text="Playing" if player.playing else "Paused",
                                       fg=GOOD if player.playing else MUTED)
            volume = player.volume

        self.volume_bar.delete("all")
        if volume is not None:
            self.volume_bar.create_rectangle(0, 0, self.volume_bar.winfo_width() * volume / 100, 6,
                                             fill=ACCENT, width=0)
            self.volume_label.configure(text=f"Volume {volume}%")
        else:
            self.volume_label.configure(text="")

        while True:
            try:
                text = self.messages.get_nowait()
            except queue.Empty:
                break
            self.log_box.configure(state="normal")
            self.log_box.insert("1.0", f"{time.strftime('%H:%M:%S')}  {text}\n")
            self.log_box.configure(state="disabled")

    def close(self):
        self.running = False
        self.actions.shutdown(wait=False, cancel_futures=True)
        player, self.player = self.player, None
        if player is not None:
            threading.Thread(target=player.close, daemon=True).start()
        self.root.after(200, self.root.destroy)


def main():
    args = parse_args()
    root = tk.Tk()
    JukeboxApp(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
