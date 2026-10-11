import SwiftUI

/// A still of one debug scenario, drawn by the real Spatial V2 view from the real presentation code.
/// `time` defaults to the scenario's most telling moment; pass another to look at a different beat.
struct ScenarioStill: View {
    let scenario: SpatialScenario
    var time: Double? = nil
    var bright = false
    /// Developer view of the same moment: the raw tracking rectangles the production layer never draws.
    var debugBoxes = false
    var sceneDebug = false

    var body: some View {
        GeometryReader { geo in
            let harness = ScenarioHarness(scenario)
            let snap = harness.advance(to: time ?? scenario.previewTime)
            let proj = Projector(layer: nil, captureAngle: 0, hfovDeg: harness.hfovDeg, heading: snap.heading, size: geo.size)
            ZStack {
                if bright {
                    LinearGradient(colors: [Color(red: 0.55, green: 0.65, blue: 0.75), Color(red: 0.95, green: 0.9, blue: 0.8)],
                                   startPoint: .top, endPoint: .bottom)
                } else {
                    LinearGradient(colors: [Color(white: 0.20), Color(white: 0.07)], startPoint: .top, endPoint: .bottom)
                }
                ForEach(snap.faces) { face in
                    let r = proj.rect(forVisionBox: face.box)
                    VStack(spacing: 4) {
                        Circle().fill(Color(white: 0.55)).frame(width: r.width, height: r.height)
                        RoundedRectangle(cornerRadius: r.width / 2).fill(Color(white: 0.4))
                            .frame(width: r.width * 1.8, height: r.height * 2.2)
                    }
                    .position(x: r.midX, y: r.midY + r.height * 1.25)
                }
                if debugBoxes {
                    ForEach(snap.faces) { face in
                        let r = proj.rect(forVisionBox: face.box)
                        Rectangle().stroke(Palette.person(face.colorIndex), lineWidth: 2)
                            .frame(width: r.width, height: r.height).position(x: r.midX, y: r.midY)
                    }
                }
                if sceneDebug {
                    SceneUnderstandingOverlay(objects: snap.objects, proj: proj, size: geo.size, now: snap.t)
                }
                SpatialSceneView(scene: snap.scene, captions: snap.visibleCaptions, faces: snap.faces, proj: proj,
                                 size: geo.size, now: snap.t, forceReduceMotion: scenario.reduceMotion)
            }
        }
        .ignoresSafeArea()
        .preferredColorScheme(.dark)
    }
}

#Preview("01 Quiet room", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(1)) }
#Preview("02 Weak sound", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(2)) }
#Preview("03 Loud sound", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(3)) }
#Preview("04 Weak + loud", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(4)) }
#Preview("05 Three sounds", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(5)) }
#Preview("06 Five sounds", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(6)) }
#Preview("07 Getting louder", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(7)) }
#Preview("08 Getting quieter", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(8)) }
#Preview("09 Loud stops", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(9)) }
#Preview("10 Quiet stops", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(10)) }
#Preview("11 Dip then resumes", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(11)) }
#Preview("12 Unknown sound", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(12)) }
#Preview("13 Alarm behind", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(13)) }
#Preview("14 Unidentified speech", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(14)) }
#Preview("15 Blue to person color", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(15)) }
#Preview("16 Visible speaker", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(16)) }
#Preview("17 Speaker leaves", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(17)) }
#Preview("18 Speaker returns", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(18)) }
#Preview("19 Pinned visible", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(19)) }
#Preview("20 Pinned offscreen", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(20)) }
#Preview("21 Pinned offscreen speaking", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(21)) }
#Preview("22 Pinned returns", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(22)) }
#Preview("23 Leaves and re-enters", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(23)) }
#Preview("24 Ambiguous crossing", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(24)) }
#Preview("25 Distant face", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(25)) }
#Preview("26 Body only", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(26)) }
#Preview("27 Known person", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(27)) }
#Preview("28 Danger + speech + sound", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(28)) }
#Preview("29 Over capacity", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(29)) }
#Preview("30 Reduce Motion", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(30)) }
#Preview("31 Aura near edge", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(31)) }
#Preview("32 Aura two speakers", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(32)) }
#Preview("33 Aura moving", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(33)) }
#Preview("34 Aura quiet", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(34)) }
#Preview("35 Aura loud", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(35)) }
#Preview("36 Aura Reduce Motion", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(36)) }
#Preview("37 ID moving", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(37)) }
#Preview("38 ID head turn", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(38)) }
#Preview("39 ID occluded", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(39)) }
#Preview("40 ID out/back left", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(40)) }
#Preview("41 ID out/back right", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(41)) }
#Preview("42 ID other side", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(42)) }
#Preview("43 ID crossing", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(43)) }
#Preview("44 ID overlap", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(44)) }
#Preview("45 ID leaves speaking", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(45)) }
#Preview("46 ID pinned leaves", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(46)) }
#Preview("47 ID pinned returns", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(47)) }
#Preview("48 ID farther", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(48)) }
#Preview("49 ID body only", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(49)) }
#Preview("50 ID body then face", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(50)) }
#Preview("51 ID in and out", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(51)) }
#Preview("52 Objects quiet", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(52)) }
#Preview("52 Objects quiet (Scene Understanding)", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(52), sceneDebug: true) }
#Preview("53 Objects look away and back", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(53), sceneDebug: true) }
#Preview("54 Knock at the door", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(54)) }
#Preview("55 Phone rings", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(55)) }
#Preview("56 Unknown among objects", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(56)) }
#Preview("57 Speech is not an object", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(57)) }
#Preview("A. Production aura (no boxes)", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(16)) }
#Preview("B. Debug: raw face box", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(16), debugBoxes: true) }
#Preview("Captions over a bright room", traits: .landscapeLeft) { ScenarioStill(scenario: .numbered(16), bright: true) }

#Preview("Sound icons", traits: .landscapeLeft) {
    ZStack {
        Color(white: 0.12)
        HStack(spacing: 26) {
            ForEach(SoundLabel.allCases, id: \.self) { label in
                SpatialNotification(presence: SpatialPresence(
                    id: label.rawValue, sourceType: .sound, worldBearing: 0, category: .other,
                    icon: SoundIcon.symbol(label: label), tone: Relevance.tone(label: label),
                    label: nil, prominence: 1, urgency: 0, persistence: 0, score: 1, level: .relevant,
                    firstSeen: 0, lastSeen: 0), relative: 45, compass: true)
            }
        }
    }
}
