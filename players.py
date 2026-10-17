"""Music backends: YouTube (a browser we control), Spotify, and a demo player.

All of them expose the same small interface. Methods may block for a moment
(they talk to a browser or a web API), so the app always calls them from a
background thread, never from the camera loop.
"""
import os
import platform
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path


class Player(ABC):
    name = "Player"

    def __init__(self):
        self.title = "Nothing playing"
        self.playing = False
        self.volume = None        # percent, or None when we can't read it

    @abstractmethod
    def play_pause(self): ...

    @abstractmethod
    def next(self): ...

    @abstractmethod
    def previous(self): ...

    @abstractmethod
    def volume_up(self): ...

    @abstractmethod
    def volume_down(self): ...

    def tick(self):
        """Called every second or so: refresh the title and handle auto-advance."""

    def close(self):
        pass


# ------------------------------------------------------------------- YouTube
POLL_JS = """
const v = document.querySelector('video');
if (!v) return null;
const p = document.getElementById('movie_player');
const skip = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button');
if (skip) skip.click();                         // press YouTube's own "Skip" button when it appears
return {paused: v.paused, ended: v.ended, time: v.currentTime,
        ad: !!(p && p.classList.contains('ad-showing')),
        volume: (p && p.getVolume) ? p.getVolume() : Math.round(v.volume * 100),
        title: document.title};
"""

TOGGLE_JS = "const v = document.querySelector('video'); if (v) { if (v.paused) v.play(); else v.pause(); }"

VOLUME_JS = """
const p = document.getElementById('movie_player');
const v = document.querySelector('video');
const now = (p && p.getVolume) ? p.getVolume() : Math.round(v.volume * 100);
const target = Math.max(0, Math.min(100, now + arguments[0]));
if (p && p.setVolume) { p.setVolume(target); if (p.unMute) p.unMute(); } else { v.volume = target / 100; }
"""


def load_playlist(path):
    with open(path, encoding="utf-8") as handle:
        lines = [line.strip() for line in handle]
    return [line for line in lines if line and not line.startswith("#")]


class YouTubePlayer(Player):
    """Opens YouTube in a browser window that this program controls directly.

    Going through the page itself (rather than pressing media keys) means next /
    previous always work, even for a single video with no playlist.
    """

    name = "YouTube"
    VOLUME_STEP = 10

    def __init__(self, entries, browser="chrome"):
        super().__init__()
        self._queue = self._expand(entries)
        if not self._queue:
            raise RuntimeError("The playlist is empty. Add links or 'search:' lines to playlist.txt")
        self._index = 0
        self._time = 0.0
        self._driver = self._launch(browser)
        self._play_index(0)

    # --- building the queue
    @staticmethod
    def _expand(entries):
        queue = []
        for entry in entries:
            if entry.lower().startswith("search:"):
                queue.append({"query": entry[7:].strip(), "url": None})
            elif entry.startswith(("http://", "https://")):
                if "list=" in entry or "/playlist" in entry:
                    queue += YouTubePlayer._playlist_items(entry)
                else:
                    queue.append({"query": None, "url": entry})
            else:
                queue.append({"query": entry, "url": None})   # plain text is treated as a search
        return queue

    @staticmethod
    def _ydl_flat(target):
        import yt_dlp

        options = {"quiet": True, "extract_flat": True, "skip_download": True}
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(target, download=False)

    @classmethod
    def _playlist_items(cls, url):
        info = cls._ydl_flat(url)
        return [{"query": None, "url": f"https://www.youtube.com/watch?v={e['id']}"}
                for e in info.get("entries", []) if e.get("id")]

    def _resolve(self, item):
        """Turn a search into a video link (done lazily, when the song is about to play)."""
        if item["url"]:
            return item["url"]
        info = self._ydl_flat(f"ytsearch1:{item['query']}")
        entries = info.get("entries") or []
        if not entries:
            raise RuntimeError(f"No YouTube results for '{item['query']}'")
        item["url"] = f"https://www.youtube.com/watch?v={entries[0]['id']}"
        return item["url"]

    # --- browser
    @staticmethod
    def _launch(browser):
        from selenium import webdriver

        if browser == "edge":
            options, driver_class = webdriver.EdgeOptions(), webdriver.Edge
        else:
            options, driver_class = webdriver.ChromeOptions(), webdriver.Chrome
        profile = Path.home() / ".gesture-jukebox" / f"{browser}-profile"
        profile.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={profile}")   # remembers cookie choice / YouTube login
        options.add_argument("--autoplay-policy=no-user-gesture-required")
        options.add_argument("--window-size=1100,720")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        return driver_class(options=options)

    def _play_index(self, index):
        from selenium.webdriver.support.ui import WebDriverWait

        self._index = index % len(self._queue)
        self.title = "Loading..."
        self._driver.get(self._resolve(self._queue[self._index]))

        if "consent." in self._driver.current_url:
            self.title = "Accept the YouTube consent box in the browser window"
            timeout = 90
        else:
            timeout = 20
        WebDriverWait(self._driver, timeout).until(
            lambda d: d.execute_script("return !!document.querySelector('video')"))
        self._driver.execute_script("const v = document.querySelector('video'); if (v && v.paused) v.play();")
        self._poll()

    def _poll(self):
        state = self._driver.execute_script(POLL_JS)
        if not state:
            return
        self.playing = not state["paused"]
        self.volume = int(state["volume"])
        self._time = state["time"]
        title = state["title"].replace(" - YouTube", "").strip()
        if title and title != "YouTube":
            self.title = title
        if state["ended"] and not state["ad"]:
            self.next()   # song finished: carry on with the queue

    # --- controls
    def play_pause(self):
        self._driver.execute_script(TOGGLE_JS)
        self._poll()

    def next(self):
        self._play_index(self._index + 1)

    def previous(self):
        if self._time > 3:      # same habit as most players: restart first, then go back
            self._driver.execute_script("document.querySelector('video').currentTime = 0;")
        else:
            self._play_index(self._index - 1)

    def volume_up(self):
        self._driver.execute_script(VOLUME_JS, self.VOLUME_STEP)
        self._poll()

    def volume_down(self):
        self._driver.execute_script(VOLUME_JS, -self.VOLUME_STEP)
        self._poll()

    def tick(self):
        self._poll()

    def close(self):
        try:
            self._driver.quit()
        except Exception:
            pass


# ------------------------------------------------------------------- Spotify
class SpotifyApiPlayer(Player):
    """Spotify Web API. Needs Premium and a developer app (see the README)."""

    name = "Spotify (Web API)"
    VOLUME_STEP = 10

    def __init__(self):
        super().__init__()
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        scope = "user-modify-playback-state user-read-playback-state"
        self._api = spotipy.Spotify(auth_manager=SpotifyOAuth(scope=scope))
        self._progress_ms = 0
        self.tick()    # fails early with a clear message if the login didn't work

    def _device(self):
        """Pick the device to control. The API refuses to act when nothing is 'active'."""
        devices = self._api.devices().get("devices", [])
        if not devices:
            raise RuntimeError("Open Spotify on a phone or computer first")
        active = next((d for d in devices if d["is_active"]), None)
        if active is None:
            self._api.transfer_playback(devices[0]["id"], force_play=False)
            return devices[0]["id"]
        return active["id"]

    def tick(self):
        state = self._api.current_playback()
        if not state or not state.get("item"):
            return
        item = state["item"]
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        self.title = f"{item['name']} - {artists}" if artists else item["name"]
        self.playing = state["is_playing"]
        self._progress_ms = state.get("progress_ms") or 0
        self.volume = (state.get("device") or {}).get("volume_percent", self.volume)

    def play_pause(self):
        device = self._device()
        if self.playing:
            self._api.pause_playback(device_id=device)
        else:
            self._api.start_playback(device_id=device)
        time.sleep(0.4)
        self.tick()

    def next(self):
        self._api.next_track(device_id=self._device())
        time.sleep(0.5)
        self.tick()

    def previous(self):
        device = self._device()
        self._api.previous_track(device_id=device)
        if self._progress_ms > 3000:
            # Spotify's first "previous" only restarts the song once you're past 3 seconds in.
            time.sleep(0.3)
            self._api.previous_track(device_id=device)
        time.sleep(0.5)
        self.tick()

    def _change_volume(self, delta):
        current = self.volume if self.volume is not None else 50
        self.volume = max(0, min(100, current + delta))
        self._api.volume(self.volume, device_id=self._device())

    def volume_up(self):
        self._change_volume(self.VOLUME_STEP)

    def volume_down(self):
        self._change_volume(-self.VOLUME_STEP)


class SpotifyDesktopPlayer(Player):
    """Controls the Spotify desktop app directly. No Premium or developer account needed.

    macOS: AppleScript (targets Spotify specifically).
    Linux: playerctl over MPRIS (targets Spotify specifically).
    Windows: the system media keys, which go to whichever app last played audio.
    """

    name = "Spotify (desktop app)"
    VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "volume_up": 0xAF, "volume_down": 0xAE}

    def __init__(self):
        super().__init__()
        self._os = platform.system()
        if self._os == "Linux" and not shutil.which("playerctl"):
            raise RuntimeError("Install playerctl first: sudo apt install playerctl")
        self.title = "Spotify desktop app"

    @staticmethod
    def _run(*command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        return result.stdout.strip()

    def _mac(self, script):
        return self._run("osascript", "-e", f'tell application "Spotify" to {script}')

    def _linux(self, *args):
        return self._run("playerctl", "-p", "spotify", *args)

    def _windows_key(self, action):
        import ctypes

        user32 = ctypes.windll.user32
        user32.keybd_event(self.VK[action], 0, 0, 0)   # key down
        user32.keybd_event(self.VK[action], 0, 2, 0)   # key up

    def _send(self, action):
        if self._os == "Darwin":
            self._mac({"play_pause": "playpause", "next": "next track",
                       "previous": "previous track"}[action])
        elif self._os == "Linux":
            self._linux({"play_pause": "play-pause", "next": "next", "previous": "previous"}[action])
        else:
            self._windows_key(action)
        time.sleep(0.4)
        self.tick()

    def play_pause(self):
        self._send("play_pause")

    def next(self):
        self._send("next")

    def previous(self):
        if self._os == "Darwin":
            self._mac("set player position to 0")   # so a single "previous" really goes back
        self._send("previous")

    def _change_volume(self, delta):
        if self._os == "Darwin":
            current = int(self._mac("get sound volume") or 50)
            self._mac(f"set sound volume to {max(0, min(100, current + delta))}")
        elif self._os == "Linux":
            self._linux("volume", f"{abs(delta) / 100}{'+' if delta > 0 else '-'}")
        else:
            self._windows_key("volume_up" if delta > 0 else "volume_down")   # system volume
        self.tick()

    def volume_up(self):
        self._change_volume(10)

    def volume_down(self):
        self._change_volume(-10)

    def tick(self):
        try:
            if self._os == "Darwin":
                script = ('if application "Spotify" is running then\n'
                          'tell application "Spotify" to return (name of current track) & "||" & '
                          '(artist of current track) & "||" & (player state as string) & "||" & '
                          '(sound volume as string)\nend if')
                parts = self._run("osascript", "-e", script).split("||")
                if len(parts) == 4:
                    self.title = f"{parts[0]} - {parts[1]}"
                    self.playing = parts[2] == "playing"
                    self.volume = int(parts[3])
            elif self._os == "Linux":
                title = self._linux("metadata", "--format", "{{title}} - {{artist}}")
                if title:
                    self.title = title
                self.playing = self._linux("status") == "Playing"
                volume = self._linux("volume")
                if volume:
                    self.volume = round(float(volume) * 100)
        except Exception:
            pass   # a failed status read isn't worth interrupting the music for


# ---------------------------------------------------------------------- Demo
class DemoPlayer(Player):
    """Pretend player: lets you test the gestures without a browser or any account."""

    name = "Demo"
    SONGS = ["Demo song 1", "Demo song 2", "Demo song 3", "Demo song 4"]

    def __init__(self):
        super().__init__()
        self._index, self.playing, self.volume = 0, True, 50
        self.title = self.SONGS[0]

    def play_pause(self):
        self.playing = not self.playing

    def next(self):
        self._index = (self._index + 1) % len(self.SONGS)
        self.title = self.SONGS[self._index]

    def previous(self):
        self._index = (self._index - 1) % len(self.SONGS)
        self.title = self.SONGS[self._index]

    def volume_up(self):
        self.volume = min(100, self.volume + 10)

    def volume_down(self):
        self.volume = max(0, self.volume - 10)


def create_player(mode, playlist_path="playlist.txt", browser="chrome"):
    if mode == "youtube":
        return YouTubePlayer(load_playlist(playlist_path), browser)
    if mode == "spotify":
        keys = ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI")
        return SpotifyApiPlayer() if all(os.environ.get(k) for k in keys) else SpotifyDesktopPlayer()
    return DemoPlayer()
