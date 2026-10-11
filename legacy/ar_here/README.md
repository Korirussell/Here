# ar_here

The AR caption layer of HERE: live video with glass captions above each speaker, showing who is talking, what they said, and how it sounded.

This project runs on its own with a mock backend. See `CLAUDE.md` for the full brief, the data contract and the accessibility rules.

## Setup (Windows)

**Backend (mock)**
```
cd server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python mock_server.py --test-pattern
```

**Frontend**
```
cd web
npm install
npm run dev
```
Open http://localhost:5173. Use a Chromium browser for best WebGL performance, fullscreen with F11.

## Using your iPhone as the camera

Install an iPhone-as-webcam app (Camo, DroidCam or Iriun) on the phone and its client on Windows. The phone then appears as a normal webcam. Find its index by trying:
```
python mock_server.py --camera 1
python mock_server.py --camera 2
```
The mock uses OpenCV's basic face detector so captions land on real faces. It is rough on purpose; the vision project replaces it.

## Calibrate the field of view

Bearings only line up if the horizontal field of view is right. Put two objects at the left and right edges of the frame, measure the angle between them from the phone, and pass it in:
```
python mock_server.py --camera 1 --hfov 64
```

## Controls

- **D**: toggle face boxes with ids and bearings
- `?lite=1`: cheap rendering without glass or bloom
- `?ws=ws://192.168.1.20:8765`: connect to a server on another machine
- `--script-offset 12`: jump the fake conversation to the crosstalk part

## Credits

- Emoji: Microsoft Fluent Emoji 3D, MIT license (`web/public/emoji/LICENSE-fluentui-emoji`)
- Font: Atkinson Hyperlegible by the Braille Institute, SIL Open Font License
