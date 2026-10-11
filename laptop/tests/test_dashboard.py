"""The operator dashboard renders from tapped messages without hardware (and never breaks sending)."""

from types import SimpleNamespace

from contracts.python.here_contract import Alert, Heartbeat, Source, Sources, SpeechStart, Utterance
from laptop.dashboard import Dashboard, compass


class Client:
    connected = True
    sent = dropped = 0

    def __init__(self):
        self.out = []

    def send(self, msg):
        self.out.append(msg)
        return True


def utt(text, pending, final):
    return Utterance(src="laptop", t=1.0, utt_id="u-1", t_start=0.5, t_end=1.0, text=text, pending=pending,
                     final=final, bearing_deg=-90.0, bearing_spread_deg=1.0, source_id=0, overlap=False,
                     tone="questioning", tone_confidence=0.8, loudness=0.5, engine="faster_whisper")


def test_compass_marks_front_right_back_left():
    ring = compass(bearing=90.0, wearer=180.0, alerts=[0.0], speech=True)
    mid = len(ring) // 2
    assert ring[0].strip(". ").startswith("F") and ring[-1].strip(". ").startswith("B")
    assert ring[mid].rstrip().endswith("R") and ring[mid].lstrip().startswith("L")
    assert "@" in ring[mid] and ring[mid].index("@") > ring[mid].index("+")       # talker to the right
    assert any("w" in r for r in ring[mid + 1:]) and any("!" in r for r in ring[:mid])


def test_dashboard_follows_the_messages():
    client = Client()
    app = SimpleNamespace(audio=SimpleNamespace(level_db=-32.0), speech=None)
    d = Dashboard(app, client, wearer_bearing=170.0)
    d.wrap(client)
    client.send(Sources(src="laptop", t=1.0, sources=[Source(id=0, bearing_deg=-90.0, energy=0.6, speech=True,
                                                             confidence=0.8)]))
    client.send(SpeechStart(src="laptop", t=1.0, utt_id="u-1", bearing_deg=-90.0, source_id=0, confidence=0.8))
    client.send(utt("are you", "going", False))
    client.send(utt("Are you going?", "", True))
    client.send(Alert(src="laptop", t=1.2, alert_id="a-1", kind="doorbell", label="Doorbell", emoji="x", priority=2,
                      bearing_deg=165.0, active=True, confidence=0.9))
    client.send(Heartbeat(src="laptop", t=2.0, status="degraded", engine="faster_whisper", doa_source="builtin",
                          detail="no raw mics"))
    assert len(client.out) == 6                         # every message still went out
    ring, info = d.lines()
    text = "\n".join(info)
    assert "-90.0 deg" in text and "SPEECH" in text and "-32.0 dBFS" in text
    assert "Are you going?" in text and "ALERT Doorbell at +165 deg" in text and "no raw mics" in text
    assert "captions 1" in text and "alerts 1" in text and "+170 deg (ignored)" in text
    client.send(Alert(src="laptop", t=11.2, alert_id="a-1", kind="doorbell", label="Doorbell", emoji="x",
                      priority=2, bearing_deg=165.0, active=False, confidence=0.9))
    assert "ALERT" not in "\n".join(d.lines()[1])


def test_rich_view_renders():
    from rich.console import Console
    d = Dashboard(SimpleNamespace(audio=None, speech=None), Client())
    console = Console(record=True, width=120)
    console.print(d.renderable())
    assert "HERE hearing module" in console.export_text()
