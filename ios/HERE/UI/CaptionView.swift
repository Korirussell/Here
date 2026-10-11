import SwiftUI

/// One caption box (README §14 "What the screen shows"):
/// header = color dot + "Person N" + tone chip; body = committed words, provisional words dimmer,
/// two lines max (newest words win); a tail toward the speaker; typing dots before any words.
struct CaptionView: View {
    let caption: CaptionState
    let color: Color
    let title: String
    var tail: TailEdge = .bottom
    var simpleGlass = false
    var offCameraHint: String? = nil
    /// Spatial V2: a more see-through plate (still >= 4.5:1, see `spatialPlate`) and, for a tracked
    /// person in view, a pin control in the header. The legacy overlay leaves all three alone.
    var spatial = false
    var pinned: Bool? = nil
    var onPin: (() -> Void)? = nil

    enum TailEdge { case bottom, top, leading, trailing, none }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            header
            if caption.typing {
                TypingDots().frame(height: 26)
            } else {
                bodyText
                    .font(Typeface.caption())
                    .lineLimit(2)
                    .truncationMode(.head)                 // keep the newest words when it overflows
                    .fixedSize(horizontal: false, vertical: true)
                    .legible()
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .frame(maxWidth: Tunables.captionMaxWidth, alignment: .leading)
        .modifier(CaptionPlate(spatial: spatial, simple: simpleGlass))
        // Ties the whole card to the speaker's color, not just the header dot -- a soft tinted edge
        // and glow instead of every caption being the same neutral dark plate regardless of who's
        // talking (README §14: identity is never by color *alone*, but the color still carries
        // meaning once "Person N" is also stated, which it always is in the header).
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(color.opacity(spatial ? Tunables.captionBorderOpacity : 0.45), lineWidth: 1))
        .shadow(color: color.opacity(simpleGlass ? 0 : 0.35), radius: 12, x: 0, y: 4)
        .overlay(alignment: tailAlignment) { tailView }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(title) said \(caption.text) \(caption.pending)")
    }

    private var header: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 9, height: 9)
            Text(title).font(Typeface.header()).foregroundStyle(color).legible()
            if let hint = offCameraHint {
                Text(hint).font(Typeface.small()).foregroundStyle(.white.opacity(0.75))
            }
            if let tone = caption.tone, tone != .neutral {
                ToneChip(tone: tone, confidence: caption.toneConfidence)
            }
            if let pinned, let onPin {
                PinButton(pinned: pinned, color: color, label: title, action: onPin)
                    .padding(.vertical, -8)            // a full-size tap target without a taller header
            }
        }
        // Spatial V2: a short line of text must not squeeze the header (the tone chip was wrapping).
        .fixedSize(horizontal: spatial, vertical: false)
    }

    /// Committed words in white, provisional words a dimmer but *opaque* gray (README §14: >= 4.5:1,
    /// measured against the brightest booth background). Opaque, not `.white.opacity(...)`, on purpose:
    /// a translucent color blends with whatever the glass tint lets through, so it quietly loses
    /// contrast right along with the backdrop; a fixed color keeps the ratio independent of the scene.
    private var bodyText: Text {
        let committed = Text(verbatim: caption.text).foregroundStyle(.white)
        guard !caption.pending.isEmpty else { return committed }
        let pending = Text(verbatim: (caption.text.isEmpty ? "" : " ") + caption.pending)
            .foregroundStyle(Color(white: Tunables.captionPendingWhite))
        return Text("\(committed)\(pending)")
    }

    private var tailAlignment: Alignment {
        switch tail {
        case .bottom: .bottom
        case .top: .top
        case .leading: .leading
        case .trailing: .trailing
        case .none: .center
        }
    }

    @ViewBuilder private var tailView: some View {
        switch tail {
        case .bottom: Tail().fill(.black.opacity(0.45)).frame(width: 18, height: 10).offset(y: 9)
        case .top: Tail().fill(.black.opacity(0.45)).frame(width: 18, height: 10).rotationEffect(.degrees(180)).offset(y: -9)
        case .leading, .trailing, .none: EmptyView()
        }
    }
}

/// The card behind a caption: Liquid Glass (legacy) or the more see-through Spatial V2 plate.
struct CaptionPlate: ViewModifier {
    let spatial: Bool
    let simple: Bool

    func body(content: Content) -> some View {
        if spatial {
            content.spatialPlate(simple: simple)
        } else {
            content.hereGlass(simple: simple)
        }
    }
}

/// Emoji + word; fades with low confidence (README §14).
struct ToneChip: View {
    let tone: Tone
    let confidence: Double

    var body: some View {
        HStack(spacing: 3) {
            Text(ToneStyle.emoji(tone))
            Text(ToneStyle.word(tone)).font(Typeface.small())
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 2)
        .background(.white.opacity(0.16), in: Capsule())
        .foregroundStyle(.white)
        .opacity(0.45 + 0.55 * min(1, max(0, confidence)))
    }
}

/// Shown on speech_start, before any words arrive (fast path, README §10).
struct TypingDots: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        if reduceMotion {
            Text("…").font(Typeface.caption()).foregroundStyle(.white.opacity(0.8))
        } else {
            TimelineView(.animation) { timeline in
                let t = timeline.date.timeIntervalSinceReferenceDate
                HStack(spacing: 6) {
                    ForEach(0..<3) { i in
                        Circle()
                            .fill(.white)
                            .frame(width: 8, height: 8)
                            .opacity(0.35 + 0.65 * max(0, sin(t * 6 - Double(i) * 0.8)))
                    }
                }
            }
        }
    }
}

struct Tail: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.midX, y: rect.maxY))
        p.closeSubpath()
        return p
    }
}
