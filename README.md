# Gesture Jukebox

Control YouTube or Spotify with your hand. A box and skeleton follow your hand on screen, so you can see what it is tracking and which gesture it thinks you are making.

## Setup
```bash
pip install -r requirements.txt     # Python 3.9 - 3.12
python main.py --mode demo          # try the gestures first, no accounts needed
```
On Linux you may also need `sudo apt install python3-tk`.

## Gestures
| Gesture | Action |
|---|---|
| Open palm, swipe right | Next song |
| Open palm, swipe left | Previous song |
| Fist, hold ~0.7 s | Play / pause |
| Thumbs up, hold | Volume up (repeats while held) |
| Thumbs down, hold | Volume down |

The window also has buttons, and the keyboard works too: arrow keys, space, Esc.
Under your hand a bar fills as you swipe or hold, so you can see how close you are to triggering. The five dots at the top left (T I M R P = thumb to pinky) light up for each finger the program thinks is out.

## Music sources
**YouTube** (`python main.py`): opens a Chrome (or `--browser edge`) window that the app controls directly. Next and previous load the next video in `playlist.txt`; this also works with single videos and with no YouTube playlist. A song that ends moves on to the next one, and YouTube's own "Skip ad" button is pressed when it appears. Put links, playlist links or `search: song name` lines in `playlist.txt`. In the UK/EU the first run shows a cookie box; accept it once in that window, since the profile is remembered in `~/.gesture-jukebox`. Log in to YouTube there for Premium.

**Spotify** (`python main.py --mode spotify`):
- Without setup, it controls the **Spotify desktop app** directly (AppleScript on macOS, `playerctl` on Linux, media keys on Windows). Windows media keys go to whichever app last played audio, and the volume gestures change system volume there.
- For the Web API (Premium, works with any device): create an app at developer.spotify.com, then set `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET` and `SPOTIPY_REDIRECT_URI` (e.g. `http://127.0.0.1:8888/callback`) before launching. Open Spotify on a device first.

You can switch source from the buttons in the app.

## Tuning
- Swipes missed or too hard? Drag the "Swipe distance" slider (lower = easier), or start with `--swipe 0.2`.
- Poses read wrongly? Watch the finger dots and keep your whole hand in view, about an arm's length from the camera, with good light.
- Slow computer? `--complexity 0`.
- A swipe's return stroke is ignored for about 1.3 s. Move your hand back slowly or drop it out of view.

## Files
`main.py` app window · `tracker.py` MediaPipe hand tracking · `gestures.py` pose and swipe logic · `overlay.py` box and skeleton drawing · `players.py` YouTube / Spotify / demo.
"# gesture_jukebox" 
