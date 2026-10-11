import SwiftUI

/// Colors, type and the glass treatment (SPEC §14 accessibility rules).
enum Palette {
    /// Person 1, 2, 3... fixed colors. Identity is never color alone: always "Person N" too.
    static let people: [Color] = [
        Color(red: 0.25, green: 0.85, blue: 0.85),   // teal
        Color(red: 1.00, green: 0.62, blue: 0.20),   // orange
        Color(red: 0.70, green: 0.50, blue: 1.00),   // violet
        Color(red: 1.00, green: 0.45, blue: 0.65),   // pink
        Color(red: 0.95, green: 0.85, blue: 0.25),   // yellow
        Color(red: 0.45, green: 0.85, blue: 0.40),   // green
    ]
    static func person(_ index: Int) -> Color { people[((index % people.count) + people.count) % people.count] }
    static let offCamera = Color(white: 0.85)
    static let alert = Color(red: 1.0, green: 0.42, blue: 0.25)
    static let emergency = Color(red: 1.0, green: 0.2, blue: 0.25)
    // Spatial notification color grammar (identity colors are `people`, above).
    static let environment = Color(red: 0.30, green: 0.90, blue: 0.45)   // green: environmental sounds
    static let human = Color(red: 0.35, green: 0.65, blue: 1.00)         // blue: a human sound nobody is attributed to yet
    static let unknown = Color(white: 0.82)                              // neutral: unclassified

    static func category(_ c: SoundCategory) -> Color {
        switch c {
        case .speech: .white
        case .alert: alert
        case .animal: Color(red: 0.95, green: 0.8, blue: 0.3)
        case .media: Color(red: 0.4, green: 0.7, blue: 1.0)
        case .other: Color(white: 0.8)
        }
    }
}

/// All fixed-pixel `.system(size:...)` before this was never affected by the user's preferred text size
/// at all -- a real gap given captions are the product (SPEC §14's legibility rules say nothing about
/// type size, but a user who needs Larger Text needs it here most of all). Built on `Font.TextStyle`
/// instead: every size here scales with Dynamic Type automatically. The `TextStyle` argument picks
/// which built-in style to scale *from* -- chosen to sit close to this call site's old fixed size, not
/// an exact match (once Dynamic Type is in play, matching the user's preference matters more than
/// matching the old pixel value).
enum Typeface {
    static func caption(_ style: Font.TextStyle = .title3) -> Font { .system(style, design: .rounded).weight(.semibold) }
    static func header() -> Font { .system(.subheadline, design: .rounded).weight(.bold) }
    static func small(_ style: Font.TextStyle = .caption) -> Font { .system(style, design: .rounded).weight(.medium) }
}

/// Tone chip content: how it *sounded*, emoji and word (SPEC §14).
enum ToneStyle {
    static func emoji(_ t: Tone) -> String {
        switch t {
        case .neutral: "💬"
        case .excited: "🤩"
        case .laughing: "😂"
        case .shouting: "📢"
        case .whispering: "🤫"
        case .questioning: "❓"
        case .sad: "😔"
        }
    }

    static func word(_ t: Tone) -> String {
        switch t {
        case .questioning: "asking"
        default: t.rawValue
        }
    }
}

extension View {
    /// Liquid Glass with a darker tint for legibility over bright scenes (SPEC §14). Under thermal
    /// pressure (`simple`) it falls back to a flat dark plate, which is cheaper to render.
    @ViewBuilder
    func hereGlass(cornerRadius: CGFloat = 18, tint: Color = .black.opacity(0.94), simple: Bool = false) -> some View {
        if simple {
            self.background(Color.black.opacity(0.72), in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
        } else {
            self.glassEffect(.regular.tint(tint), in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
        }
    }

    /// Spatial V2 caption plate: translucent enough that the room stays visible through it, without
    /// giving up SPEC §14's contrast floor. The black layer sits *on top of* the glass, so whatever
    /// the glass does with the scene behind it (at most: white), text is read against
    /// black at `captionPlateOpacity` over that -- >= 4.5:1 for committed and pending words alike
    /// (PresentationTests.testCaptionPlateContrast). Under thermal pressure the glass is dropped.
    func spatialPlate(cornerRadius: CGFloat = 18, simple: Bool = false) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        return background {
            ZStack {
                if !simple { Color.clear.glassEffect(.clear, in: shape) }
                shape.fill(Color.black.opacity(Tunables.captionPlateOpacity))
            }
        }
    }

    /// Text shadow that keeps white text readable on any background.
    func legible() -> some View {
        shadow(color: .black.opacity(0.65), radius: 2, x: 0, y: 1)
    }
}
