import SwiftUI

extension PresenceTone {
    /// Color by meaning: red danger, green environment, blue unattributed human, neutral unknown,
    /// and a person's own color once speech is theirs.
    var color: Color {
        switch self {
        case .danger: Palette.emergency
        case .environment: Palette.environment
        case .human: Palette.human
        case .neutral: Palette.unknown
        case .person(let index): Palette.person(index)
        }
    }
}

/// One expanding ring: "it just happened, here". Plays once per occurrence, or keeps going for as
/// long as an important event is active. Never shown with Reduce Motion.
struct PulseRing: View {
    let color: Color
    let strength: Double
    var repeats = false
    @State private var out = false

    var body: some View {
        Circle()
            .stroke(color, lineWidth: 2)
            .scaleEffect(out ? Tunables.pulseScale : 1)
            .opacity(out ? 0 : strength)
            .onAppear {
                let once = Animation.easeOut(duration: repeats ? Tunables.urgentPulsePeriodS : Tunables.pulseDurationS)
                withAnimation(repeats ? once.repeatForever(autoreverses: false) : once) { out = true }
            }
            .allowsHitTesting(false)
    }
}

/// One spatial notification: a sound, a voice or a person out of view. Each dimension says one thing.
/// The symbol is what it is. The color is its category, or the person's identity color. The ring is
/// the wearer's surroundings with a marker toward the source. Opacity is how intense it is right now.
struct SpatialNotification: View {
    let presence: SpatialPresence
    var relative: Double? = nil
    var directionKnown = true
    var reduceMotion = false
    /// Which way the words grow from the icon: toward the middle when the icon is near a side edge.
    var labelSide: HorizontalAlignment = .center
    /// Corner notification: a ring around the icon stands for the wearer's surroundings (top = ahead),
    /// and a green dot on it marks where the sound is. Words sit to the left, toward the view.
    var compass = false

    private var color: Color { presence.tone.color }
    private var diameter: CGFloat { Tunables.iconSize * Relevance.iconScale(presence.level) }
    private var keepsPulsing: Bool { presence.level == .important && presence.active }

    var body: some View {
        let above = abs(relative ?? 0) > 90          // lower half of the path: words go above the icon
        ZStack {
            if reduceMotion {
                // No motion: the "it's happening" cue is a steady halo instead of an expanding ring.
                if keepsPulsing || presence.speaking {
                    Circle().stroke(color.opacity(0.45), lineWidth: 4).scaleEffect(1.22)
                }
            } else if presence.pulseCount > 0 {
                PulseRing(color: color, strength: Relevance.pulseStrength(level: presence.level, prominence: presence.prominence),
                          repeats: keepsPulsing)
                    .id("\(presence.pulseCount)-\(keepsPulsing)")
            }
            Circle().fill(.black.opacity(Tunables.iconPlateOpacity))
            Circle().stroke(color.opacity(0.85), lineWidth: presence.level >= .speech ? 2 : 1.5)
            Image(systemName: presence.icon)
                .font(.system(size: diameter * 0.44, weight: .semibold))
                .foregroundStyle(color)
        }
        .frame(width: diameter, height: diameter)
        .overlay { if compass { compassRing } }
        .overlay(alignment: .topTrailing) {
            if presence.isPinned {
                Image(systemName: "pin.fill")
                    .font(.system(size: diameter * 0.26, weight: .bold))
                    .foregroundStyle(.white)
                    .offset(x: 3, y: -3)
            }
        }
        .overlay(alignment: .leading) {
            if compass, presence.captionText == nil, let words {
                wordsLabel(words)
                    .frame(width: 0, height: 0, alignment: .trailing)
                    .offset(x: -(Tunables.cornerRingPadding + Tunables.cornerDotSize / 2 + 6))
            }
        }
        .overlay(alignment: Alignment(horizontal: labelSide, vertical: above ? .top : .bottom)) {
            if !compass, let words {
                Text(words)
                    .font(Typeface.small(presence.level == .important ? .footnote : .caption2))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(.black.opacity(Tunables.captionPlateOpacity), in: Capsule())
                    .overlay(Capsule().stroke(color.opacity(0.5), lineWidth: 1))
                    .fixedSize()
                    // Zero-size anchor on the icon's edge; the words overflow away from the icon,
                    // whatever height Dynamic Type gives them.
                    .frame(width: 0, height: 0, alignment: Alignment(horizontal: labelSide, vertical: above ? .bottom : .top))
                    .offset(y: above ? -4 : 4)
            }
        }
        .opacity(presence.opacity)
        .allowsHitTesting(false)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }

    private func wordsLabel(_ text: String) -> some View {
        Text(text)
            .font(Typeface.small(presence.level == .important ? .footnote : .caption2))
            .foregroundStyle(.white)
            .lineLimit(1)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(.black.opacity(Tunables.captionPlateOpacity), in: Capsule())
            .overlay(Capsule().stroke(color.opacity(0.5), lineWidth: 1))
            .fixedSize()
    }

    private var compassRing: some View {
        let ring = diameter + Tunables.cornerRingPadding * 2
        return ZStack {
            if let relative, !presence.stale {
                Circle().stroke(.white.opacity(0.4), lineWidth: 1.5)
                // The marker takes the notification's own color (green for an environmental sound,
                // red for danger, a person's color for a person) with a dark edge so it reads on anything.
                Circle()
                    .fill(color)
                    .overlay(Circle().stroke(.black.opacity(0.7), lineWidth: 1.5))
                    .frame(width: Tunables.cornerDotSize, height: Tunables.cornerDotSize)
                    .offset(Perimeter.ringMarker(relative: relative, radius: ring / 2))
                    .animation(reduceMotion ? nil : .smooth(duration: Tunables.pathFollowS), value: relative)
            } else {
                // Direction unknown: no dot, and a broken ring so it doesn't look like "everywhere".
                Circle().stroke(.white.opacity(0.4), style: StrokeStyle(lineWidth: 1.5, dash: [3, 5]))
            }
        }
        .frame(width: ring, height: ring)
    }

    /// Words where they help: important events and pinned people always, clearer sounds briefly.
    private var words: String? {
        if presence.stale { return (presence.label ?? "Pinned") + " · location unknown" }
        guard var text = presence.label else { return directionKnown ? nil : "direction unknown" }
        if presence.count > 1, presence.sourceType != .person { text += " ×\(presence.count)" }
        if presence.level == .important {
            text += " · " + (relative.map(Geo.describe(relative:)) ?? "direction unknown")
        } else if !directionKnown {
            text += " · direction unknown"
        }
        return text
    }

    private var accessibilityText: String {
        let what: String
        switch presence.sourceType {
        case .speech: what = "Someone speaking"
        case .person:
            what = (presence.label ?? "Person") + (presence.speaking ? " speaking" : (presence.isPinned ? ", pinned" : ""))
                + (presence.captionText.map { ": \($0)" } ?? "")
        case .alert: what = (presence.label ?? "Alert") + " alert"
        case .sound: what = presence.label ?? "Sound"
        }
        if presence.stale { return what + ", location unknown" }
        return what + ", " + (relative.map(Geo.describe(relative:)) ?? "direction unknown")
    }
}

/// A soft glow around a speaker's head in their identity color -- the same color as their caption.
///
/// The face box is used for exactly two things, where the glow is centered and how big it is, and is
/// never drawn. The glow is two radial gradients stretched into an ellipse and blurred: a wide haze
/// and a tighter, brighter rim just outside the head. Both are fully transparent in the middle, so
/// eyes and mouth stay clear, and both fade to nothing well before any edge, so there are no corners.
/// How strong it is, and how much it pulses, follow the person's smoothed speech intensity.
struct SpeakerAura: View {
    let color: Color
    let rect: CGRect                 // the face box, in screen points: geometry only
    let activity: Double             // 1 while speaking, easing out after
    let resting: Double              // 1 while the caption is still up
    let intensity: Double            // smoothed speech intensity, 0...1
    var reduceMotion = false
    @State private var breathe = false

    var body: some View {
        let w = rect.width * Tunables.auraExpandX
        let h = rect.height * Tunables.auraExpandY
        let opacity = SoundContinuity.auraOpacity(activity: activity, resting: resting, intensity: intensity)
        let pulse = CGFloat(SoundContinuity.auraPulse(activity: activity, intensity: intensity, reduceMotion: reduceMotion))
        ZStack {
            glow(inner: Tunables.auraInnerClear, peak: Tunables.auraPeakAt, outer: 1, strength: 1, diameter: h)
            glow(inner: Tunables.auraInnerClear, peak: Tunables.auraPeakAt * 0.92, outer: Tunables.auraPeakAt * 1.35,
                 strength: Tunables.auraRimOpacity, diameter: h)
        }
        .frame(width: h, height: h)
        .scaleEffect(x: h > 0 ? w / h : 1, y: 1)             // circle -> ellipse; no shape with corners is ever involved
        .blur(radius: Tunables.auraBlurRadius)
        .blendMode(.screen)                                   // light added to the scene, not paint over it
        .scaleEffect(1 + (breathe ? pulse : 0))
        .opacity(opacity)
        .animation(.easeInOut(duration: 0.25), value: opacity)
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: Tunables.auraBreathS).repeatForever(autoreverses: true)) { breathe = true }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private func glow(inner: CGFloat, peak: CGFloat, outer: CGFloat, strength: Double, diameter: CGFloat) -> some View {
        Circle().fill(RadialGradient(stops: [
            .init(color: color.opacity(0), location: 0),
            .init(color: color.opacity(0), location: inner),
            .init(color: color.opacity(strength), location: peak),
            .init(color: color.opacity(0), location: min(1, outer)),
        ], center: .center, startRadius: 0, endRadius: diameter / 2))
    }
}

/// "THIS is the thing making the sound": a soft glow on a visible object, in the color of the sound's
/// notification. The object's box only says where and how big; what is drawn is a blurred ellipse
/// that fades out before any edge, with the middle left mostly clear so the object stays visible.
/// Strength and a small pulse follow the sound's smoothed intensity; it lives exactly as long as
/// the sound's notification does.
struct ObjectAuraView: View {
    let aura: ObjectAura
    let rect: CGRect                 // the object's box or region in screen points: geometry only
    var reduceMotion = false
    @State private var breathe = false

    var body: some View {
        let color = aura.tone.color
        // A tight box: glow around it. A region only: a modest glow at its middle, not a glowing strip.
        let base = aura.precise ? rect.size
            : CGSize(width: rect.width * Tunables.objectAuraCoarseSize, height: rect.width * Tunables.objectAuraCoarseSize)
        let rawW = max(Tunables.objectAuraMinSize, base.width * Tunables.objectAuraExpand)
        let rawH = max(Tunables.objectAuraMinSize, base.height * Tunables.objectAuraExpand)
        let w = max(rawW, rawH / Tunables.objectAuraMaxAspect)
        let h = max(rawH, rawW / Tunables.objectAuraMaxAspect)
        let pulse = CGFloat(SoundContinuity.objectAuraPulse(intensity: aura.intensity, reduceMotion: reduceMotion))
        Circle()
            .fill(RadialGradient(stops: [
                .init(color: color.opacity(0.12), location: 0),
                .init(color: color.opacity(0.12), location: Tunables.objectAuraInnerClear),
                .init(color: color, location: Tunables.objectAuraPeakAt),
                .init(color: color.opacity(0), location: 1),
            ], center: .center, startRadius: 0, endRadius: h / 2))
            .frame(width: h, height: h)
            .scaleEffect(x: h > 0 ? w / h : 1, y: 1)
            .blur(radius: Tunables.auraBlurRadius)
            .blendMode(.screen)
            .scaleEffect(1 + (breathe ? pulse : 0))
            .opacity(aura.opacity)
            .animation(.easeInOut(duration: 0.25), value: aura.opacity)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: Tunables.auraBreathS).repeatForever(autoreverses: true)) { breathe = true }
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)             // the notification on the object already says what and where
    }
}

/// Small "talking now" mark beside the head, in the speaker's color.
struct SpeakingBadge: View {
    let color: Color
    let activity: Double
    var reduceMotion = false

    var body: some View {
        Image(systemName: "waveform")
            .font(.system(size: Tunables.speakingBadgeSize * 0.5, weight: .bold))
            .symbolEffect(.variableColor.iterative, isActive: !reduceMotion && activity >= 1)
            .foregroundStyle(color)
            .frame(width: Tunables.speakingBadgeSize, height: Tunables.speakingBadgeSize)
            .background(.black.opacity(Tunables.iconPlateOpacity), in: Circle())
            .overlay(Circle().stroke(color.opacity(0.8), lineWidth: 1.5))
            .opacity(activity)
            .animation(.easeOut(duration: 0.2), value: activity)
            .allowsHitTesting(false)
            .accessibilityHidden(true)             // the caption already says who is speaking
    }
}

/// Pin / unpin a tracked person for this session.
struct PinButton: View {
    let pinned: Bool
    let color: Color
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: pinned ? "pin.fill" : "pin")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(pinned ? color : .white.opacity(0.8))
                .frame(width: Tunables.pinHitSize, height: Tunables.pinHitSize)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(pinned ? "Unpin \(label)" : "Pin \(label) for now")
    }
}

/// Name tag above a person's head when they have no caption up: who they are, and the pin control.
/// Shown for pinned people (quietly) and for a few seconds after tapping a face.
struct PersonTag: View {
    let label: String
    let color: Color
    let pinned: Bool
    var canEnroll = false
    let onPin: () -> Void
    var onEnroll: () -> Void = {}

    var body: some View {
        HStack(spacing: 2) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(label).font(Typeface.small()).foregroundStyle(.white).legible().padding(.leading, 3)
            PinButton(pinned: pinned, color: color, label: label, action: onPin)     // "Pin for now"
            if canEnroll {
                Button(action: onEnroll) {
                    Image(systemName: "person.crop.circle.badge.plus")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.8))
                        .frame(width: Tunables.pinHitSize, height: Tunables.pinHitSize)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Add \(label) to People")
            }
        }
        .padding(.leading, 10)
        .background(.black.opacity(Tunables.captionPlateOpacity), in: Capsule())
        .overlay(Capsule().stroke(color.opacity(Tunables.captionBorderOpacity), lineWidth: 1))
    }
}

/// A pinned person out of view: their words stay beside their notification. Two lines, newest words
/// kept, on the caption plate. Drawn at full strength; only the icon's opacity follows intensity.
struct PinnedCaptionCard: View {
    let presence: SpatialPresence
    var relative: Double? = nil

    var body: some View {
        let color = presence.tone.color
        let committed = Text(verbatim: presence.captionText ?? "").foregroundStyle(.white)
        let pending = Text(verbatim: ((presence.captionText ?? "").isEmpty ? "" : " ") + presence.captionPending)
            .foregroundStyle(Color(white: Tunables.captionPendingWhite))
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 5) {
                Circle().fill(color).frame(width: 8, height: 8)
                Text(presence.label ?? "").font(Typeface.small()).foregroundStyle(color).legible()
                Image(systemName: "pin.fill").font(.system(size: 9, weight: .bold)).foregroundStyle(.white.opacity(0.8))
                if let relative, !presence.stale {
                    Text(Geo.describe(relative: relative)).font(Typeface.small(.caption2)).foregroundStyle(.white.opacity(0.75))
                }
            }
            Text("\(committed)\(pending)")
                .font(Typeface.caption(.subheadline))
                .lineLimit(2)
                .truncationMode(.head)
                .fixedSize(horizontal: false, vertical: true)
                .legible()
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .frame(width: Tunables.pinnedCaptionWidth, alignment: .leading)
        .spatialPlate(cornerRadius: 14, simple: true)
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(color.opacity(Tunables.captionBorderOpacity), lineWidth: 1))
        .opacity(presence.fade)
        .allowsHitTesting(false)
        .accessibilityHidden(true)             // the notification's own label already carries the words
    }
}
