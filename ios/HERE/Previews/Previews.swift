import SwiftUI

/// Mock data for SwiftUI previews (README §14 dev tips: iterate on captions, rings and the panel
/// without a camera, a relay or the laptop).
enum PreviewData {
    static func caption(_ text: String, pending: String = "", tone: Tone? = nil, final: Bool = false,
                        speaker: Speaker = .person(id: 1, label: "Person 1")) -> CaptionState {
        var c = CaptionState(uttId: UUID().uuidString, speaker: speaker, bearing: 35, started: 0, updated: 0)
        c.text = text
        c.pending = pending
        c.tone = tone
        c.toneConfidence = 0.8
        c.final = final
        return c
    }

    static let alerts: [AlertState] = [
        AlertState(alertId: "a1", kind: .doorbell, label: "Doorbell", emoji: "🔔", priority: 2, bearing: 165,
                   active: true, count: 2, updated: 0, lastBuzz: 0),
        AlertState(alertId: "a2", kind: .nameCalled, label: "Name called", emoji: "🗣️", priority: 2, bearing: -100,
                   active: true, count: 1, updated: 0, lastBuzz: 0),
    ]
}

private struct Backdrop<Content: View>: View {
    var dark = false
    @ViewBuilder var content: Content
    var body: some View {
        ZStack {
            if dark {
                Color(white: 0.04)                                  // dark room: the easy case
            } else {
                LinearGradient(colors: [Color(red: 0.55, green: 0.65, blue: 0.75), Color(red: 0.95, green: 0.9, blue: 0.8)],
                               startPoint: .top, endPoint: .bottom)  // bright: the hard case for legibility
            }
            content
        }
    }
}

// The app forces .preferredColorScheme(.dark) everywhere (ContentView) -- these custom glass/Palette
// colors don't read `colorScheme` at all, so the real legibility risk axis is backdrop *brightness*,
// not iOS's system light/dark setting (see the bright vs. dark Backdrop variants below). The explicit
// .preferredColorScheme traits on these two are a literal check, per the kickoff task, that the canvas
// itself renders identically either way -- it does, since nothing here is scheme-dependent.
#Preview("Captions (light scheme)", traits: .landscapeLeft) {
    Backdrop {
        HStack(alignment: .top, spacing: 30) {
            CaptionView(caption: PreviewData.caption("Hey, did you see the email about", pending: "the internship fair",
                                                     tone: .questioning),
                        color: Palette.person(0), title: "Person 1")
            CaptionView(caption: PreviewData.caption("Yeah! It's on Friday.", tone: .excited, final: true),
                        color: Palette.person(1), title: "Person 2")
            CaptionView(caption: PreviewData.caption(""), color: Palette.person(2), title: "Person 3")
        }
    }
    .preferredColorScheme(.light)
}

#Preview("Captions (dark scheme, dark room)", traits: .landscapeLeft) {
    Backdrop(dark: true) {
        HStack(alignment: .top, spacing: 30) {
            CaptionView(caption: PreviewData.caption("Hey, did you see the email about", pending: "the internship fair",
                                                     tone: .questioning),
                        color: Palette.person(0), title: "Person 1")
            CaptionView(caption: PreviewData.caption("Yeah! It's on Friday.", tone: .excited, final: true),
                        color: Palette.person(1), title: "Person 2")
            CaptionView(caption: PreviewData.caption(""), color: Palette.person(2), title: "Person 3")
        }
    }
    .preferredColorScheme(.dark)
}

#Preview("Off camera + chevron", traits: .landscapeLeft) {
    Backdrop {
        HStack(spacing: 6) {
            Chevron(leading: true, color: Palette.offCamera)
            CaptionView(caption: PreviewData.caption("Why is nobody looking at the person holding all the pizza?",
                                                     tone: .questioning, final: true, speaker: .offCamera),
                        color: Palette.offCamera, title: "Someone", tail: .none, offCameraHint: "behind you")
        }
    }
}

#Preview("Rings", traits: .landscapeLeft) {
    Backdrop {
        HStack(spacing: 80) {
            SpeakerRing(color: Palette.person(0), loudness: 0.3).frame(width: 110, height: 140)
            SpeakerRing(color: Palette.person(1), loudness: 0.9).frame(width: 110, height: 140)
        }
    }
}

#Preview("Sound panel", traits: .landscapeLeft) {
    Backdrop {
        VStack(spacing: 10) {
            ForEach(PreviewData.alerts) { a in
                AlertCard(alert: a, relative: a.bearing)
            }
            HStack(spacing: 16) {
                ForEach([0.0, 45, 90, 180, -90], id: \.self) { rel in DirectionRing(relative: rel) }
                DirectionRing(relative: nil)
            }
        }
    }
}

#Preview("Tone chips", traits: .landscapeLeft) {
    Backdrop {
        HStack {
            ForEach(Tone.allCases, id: \.self) { t in ToneChip(tone: t, confidence: t == .sad ? 0.2 : 0.9) }
        }
    }
}
