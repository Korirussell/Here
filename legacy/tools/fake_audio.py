"""Stand-in for audio_here's audio: any microphone -> audio_chunk (16 kHz mono s16le, 100 ms).

    python tools/fake_audio.py --list          # show input devices
    python tools/fake_audio.py [--device N]
    python tools/fake_audio.py --wav file.wav  # loop a 16 kHz mono WAV in real time (no mic needed)
"""

import argparse
import asyncio
import sys
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from here_common.client import DEFAULT_URL, HubClient  # noqa: E402

SR = 16000
CHUNK = 1600


async def main(args):
    hub = HubClient("audio_here", ["config"], args.url)
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=50)

    async def send_chunks():
        while True:
            t, pcm = await queue.get()
            await hub.send_with_binary("audio_chunk", pcm.tobytes(), t=t, drop_if_busy=False, sample_rate=SR,
                                       channels=1, format="s16le", samples=len(pcm), stream="processed")

    def push(t, pcm):
        if hub.connected and not queue.full():
            queue.put_nowait((t, pcm))

    if args.wav:
        w = wave.open(args.wav)
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2, "need 16 kHz mono 16-bit"
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16)
        silence = np.zeros(SR * 2, np.int16)
        audio = np.concatenate([audio, silence])

        async def play():
            i, t0 = 0, time.time()
            while True:
                chunk = audio[i:i + CHUNK]
                if len(chunk) < CHUNK:
                    i, chunk = 0, audio[:CHUNK]
                t = t0
                push(t, chunk.copy())
                i += CHUNK
                t0 += CHUNK / SR
                await asyncio.sleep(max(0.0, t0 - time.time()))

        print(f"looping {args.wav}")
        await asyncio.gather(hub.run(), send_chunks(), play())
        return

    def callback(indata, frames, time_info, status):
        now = time.time()
        # Wall time of the first sample: now minus how long ago the ADC captured it.
        lag = time_info.currentTime - time_info.inputBufferAdcTime if time_info.inputBufferAdcTime else frames / SR
        loop.call_soon_threadsafe(push, now - lag, indata[:, 0].copy())

    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=CHUNK, device=args.device,
                        callback=callback):
        print(f"streaming mic: {sd.query_devices(args.device, 'input')['name']}")
        await asyncio.gather(hub.run(), send_chunks())


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--wav")
    p.add_argument("--list", action="store_true")
    a = p.parse_args()
    if a.list:
        print(sd.query_devices())
        sys.exit()
    try:
        asyncio.run(main(a))
    except KeyboardInterrupt:
        pass
