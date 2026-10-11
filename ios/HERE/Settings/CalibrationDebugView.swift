import SwiftUI

/// Developer > Calibration & audio check.
///
/// Three jobs: (1) tell the app where the wearer stands relative to the array, (2) *measure* whether
/// directions line up, with a number and a verdict, and (3) show every stage of a sound's direction
/// from the array to the screen, so a wrong attachment can be traced to the stage that caused it.
struct CalibrationDebugView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var store: SettingsStore

    var body: some View {
        Form {
            procedure
            position
            check
            chain
            speakers
            transcription
            soundEvents
        }
        .navigationTitle("Calibration")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: 1. procedure

    private var procedure: some View {
        Section {
            Text("1. Array on its mark, front arrow pointing where the tape says.\n"
                 + "2. Wearer on the wearer mark, **facing the same way the array's front arrow points** (not looking at the array).\n"
                 + "3. Tap Set reference.\n"
                 + "4. Enter how far the wearer mark is from the array, and in which direction, below.\n"
                 + "5. Start the check. One person stands where the camera can see them and says a sentence; "
                 + "repeat from a second spot. Read the verdict.")
                .font(.footnote)
            Button { model.calibrate() } label: { Label("Set reference (heading = 0 now)", systemImage: "scope") }
            LabeledContent("Heading now", value: String(format: "%+.1f°  (%@)", model.headingDeg, model.headingQuality))
        } header: { Label("Demo calibration (under a minute)", systemImage: "list.number") }
    }

    // MARK: 2. where the wearer stands

    private var position: some View {
        Section {
            VStack(alignment: .leading) {
                Text(String(format: "Wearer is %.1f m from the array", store.value.wearerDistanceM))
                Slider(value: $store.value.wearerDistanceM, in: 0...5, step: 0.1)
            }
            VStack(alignment: .leading) {
                Text(String(format: "…in direction %+.0f° from the array's front (%@)", store.value.wearerBearingDeg,
                            Geo.describe(relative: store.value.wearerBearingDeg)))
                Slider(value: $store.value.wearerBearingDeg, in: -180...180, step: 5)
            }
            VStack(alignment: .leading) {
                Text(String(format: "Sounds are assumed about %.1f m from the array", store.value.sourceRadiusM))
                Slider(value: $store.value.sourceRadiusM, in: 0.5...6, step: 0.1)
            }
            let room = model.room
            if room.isDisplaced {
                Text(String(format: "Ignoring this would misplace a sound 1.5 m from the array by up to %.0f°. "
                            + "With it, the remaining uncertainty (sources nearer or farther than assumed) is up to %.0f°.",
                            worst { room.parallaxError(micBearing: $0, sourceDistance: 1.5) }, worst(room.listenerUncertainty)))
                    .font(.footnote).foregroundStyle(.secondary)
            } else {
                Text("0 m = the wearer is at the array: the array's angle is used as the wearer's angle, unchanged.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        } header: { Label("Where the wearer stands", systemImage: "figure.stand") }
    }

    private func worst(_ f: (Double) -> Double) -> Double {
        stride(from: -180.0, to: 180.0, by: 15).map(f).max() ?? 0
    }

    // MARK: 3. measured check

    private var check: some View {
        Section {
            Toggle("Run calibration check", isOn: $model.calibrationRunning)
            let c = model.calibration
            LabeledContent("Calibration quality") {
                Text(c.quality.rawValue).bold().foregroundStyle(color(c.quality))
            }
            LabeledContent("Samples", value: "\(c.samples.count)")
            if let last = c.samples.last {
                LabeledContent("Expected", value: String(format: "%+.0f°", last.expected))
                LabeledContent("Measured", value: String(format: "%+.0f°", last.measured))
                LabeledContent("Error", value: String(format: "%+.0f°", last.error))
            }
            if !c.samples.isEmpty {
                LabeledContent("Mean / max error", value: String(format: "%+.1f° / %.0f°", c.meanError, c.maxError))
                LabeledContent("Jitter", value: String(format: "%.1f°", c.jitter))
                Text(c.diagnosis).font(.footnote)
            }
            Text("Automatic samples: while the check runs, each finished sentence from the only person in view is "
                 + "compared with where the array heard it. Manual samples: a helper makes a sound from a marked spot, then tap its direction.")
                .font(.footnote).foregroundStyle(.secondary)
            HStack {
                ForEach([("Front", 0.0), ("Right", 90), ("Behind", 180), ("Left", -90)], id: \.1) { name, bearing in
                    Button(name) { model.calibrationSample(expectedMicBearing: bearing) }.buttonStyle(.bordered)
                }
            }
            if c.quality != .unknown, c.quality != .good, c.diagnosis.hasPrefix("Constant offset") {
                Button(String(format: "Apply %+.0f° correction to the heading", c.suggestedHeadingCorrection)) {
                    model.applyCalibrationCorrection()
                }
            }
            Button("Clear samples", role: .destructive) { model.resetCalibrationCheck() }
        } header: { Label("Calibration check", systemImage: "checkmark.seal") }
    }

    private func color(_ q: CalibrationCheck.Quality) -> Color {
        switch q {
        case .good: .green
        case .fair: .yellow
        case .bad: .red
        case .unknown: .secondary
        }
    }

    // MARK: 4. the transform chain, live

    private var chain: some View {
        Section {
            if model.sources.isEmpty { Text("No sources from the array right now.").foregroundStyle(.secondary) }
            ForEach(Array(model.sources.enumerated()), id: \.offset) { _, s in
                Text(chainLines(s).joined(separator: "\n")).font(.caption.monospaced())
            }
            if let line = attributionLine { Text(line).font(.caption.monospaced()) }
            ForEach(model.scene.presences.filter { $0.objectId != nil }) { p in
                Text(String(format: "%@ → object #%d  confidence %.2f", p.id, p.objectId ?? 0, p.associationConfidence))
                    .font(.caption.monospaced())
            }
        } header: { Label("Direction, stage by stage", systemImage: "arrow.triangle.branch") }
    }

    private func soundLines(_ s: SoundTag) -> [String] {
        let evidence = s.votes.sorted { $0.value > $1.value }.map { String(format: "%@ %.2f", $0.key.rawValue, $0.value) }
        let mic = s.micBearing.map { String(format: "%+.0f°", $0) } ?? "-"
        let here = s.bearing.map { String(format: "%+.0f°", $0) } ?? "-"
        return ["shown as: " + s.label.rawValue + "   laptop said: " + (s.heardAs?.rawValue ?? "-") + String(format: " %.2f", s.confidence)
                    + (s.model.map { "  (" + $0 + ")" } ?? ""),
                "evidence: " + evidence.joined(separator: ", "),
                "array " + mic + " -> wearer " + here + "   x\(s.count)"]
    }

    private var attributionLine: String? {
        guard let d = model.attributionDecision else { return nil }
        let err = d.angularError.map { String(format: "err %.0f° ", $0) } ?? ""
        return "last attribution: " + (d.candidate ?? "-") + " " + err + (d.accepted ? "accepted" : "rejected") + " — " + d.reason
    }

    /// One source's direction at every stage, and how each visible person/object compares.
    private func chainLines(_ s: Source) -> [String] {
        let room = model.room
        let world = room.listenerBearing(micBearing: s.bearingDeg)
        let rel = Geo.relative(world, heading: model.headingDeg)
        var lines = [String(format: "source #%d%@  energy %.2f  conf %.2f", s.id, s.speech ? " speech" : "", s.energy, s.confidence)]
        lines.append(String(format: "raw array direction: %+.0f°", s.bearingDeg))
        lines.append(String(format: "from the wearer (%.1f m model): %+.0f° ± %.0f°", room.sourceRadiusM, world,
                            room.listenerUncertainty(micBearing: s.bearingDeg)))
        lines.append(String(format: "wearer heading %+.0f° -> relative %+.0f° (%@), belt motor %d", model.headingDeg, rel,
                            Geo.describe(relative: rel), Geo.motor(forRelative: rel)))
        for f in model.faces {
            let d = model.distance(to: f)
            let predicted = room.micBearing(ofEntityAt: f.bearing, distance: d)
            lines.append(String(format: "  %@: seen at %+.0f°, ~%.1f m -> array should hear %+.0f° (diff %+.0f°)", f.label, f.bearing, d,
                                predicted, Geo.angDiff(s.bearingDeg, predicted)))
        }
        for o in model.objects where o.isConfirmed {
            lines.append(String(format: "  %@ #%d: at %+.0f° from the wearer (diff %+.0f°)", o.cls.name, o.id, o.worldBearing,
                                Geo.angDiff(world, o.worldBearing)))
        }
        return lines
    }

    // MARK: speaker locks

    private var speakers: some View {
        Section {
            if model.speakerLocks.associations.isEmpty { Text("No active speech sources.").foregroundStyle(.secondary) }
            ForEach(model.speakerLocks.associations) { a in
                Text(lockLines(a).joined(separator: "\n")).font(.caption.monospaced())
            }
            ForEach(Array(model.speakerLocks.log.reversed().enumerated()), id: \.offset) { _, line in
                Text(line).font(.caption2.monospaced()).foregroundStyle(.secondary)
            }
            Text("One lock per speech source. A source changes hands only when a challenger beats the holder by the "
                 + "switch margin for the hold time, and never during the grace period after the holder disappears. "
                 + "Every lock, switch and release is logged above with its scores.")
                .font(.footnote).foregroundStyle(.secondary)
        } header: { Label("Speaker locks", systemImage: "lock.fill") }
    }

    private func lockLines(_ a: ActiveSpeakerAssociation) -> [String] {
        let who = a.personId.flatMap { id in model.sessionPeople.first { $0.id == id }?.displayName } ?? "unattributed"
        var lines = [String(format: "source %d (stream %d): %@  [%@]  confidence %.2f", a.id, a.sourceId, who, a.state.rawValue, a.confidence)]
        lines.append(String(format: "bearing raw %+.0f°  smoothed %+.0f°  last speech %.1f s ago", a.rawBearing, a.smoothedBearing,
                            model.now - a.lastSpeechAt))
        for f in model.faces {
            guard let pid = f.personId else { continue }
            lines.append(String(format: "  %@: score %.2f  mouth %.2f%@", f.label, a.lastScores[pid] ?? 0, f.mouthActivity,
                                pid == a.personId ? "  (holder, includes continuity bonus)" : (pid == a.challengerId ? "  (challenger)" : "")))
        }
        return lines
    }

    // MARK: 5. transcription and sound labels

    private var transcription: some View {
        Section {
            if model.transcripts.isEmpty { Text("No finished utterances yet.").foregroundStyle(.secondary) }
            ForEach(model.transcripts.reversed()) { t in
                VStack(alignment: .leading, spacing: 2) {
                    Text(t.text.isEmpty ? "(empty)" : t.text)
                    Text(String(format: "%.1f s  loudness %.2f  array %+.0f° → wearer %+.0f°  spread %.0f°%@  source #%d",
                                t.durationS, t.loudness, t.micBearing, t.listenerBearing, t.spreadDeg, t.overlap ? " OVERLAP" : "", t.sourceId))
                    Text("\(t.engine)  " + (t.lagMs.map { String(format: "lag %.0f ms  ", $0) } ?? "") + t.stages)
                    Text("→ " + t.assigned)
                }
                .font(.caption.monospaced())
            }
            Text("The contract carries no transcription confidence, so none is shown. Audio level, VAD and the beam used are on the laptop (its dashboard and logs).")
                .font(.footnote).foregroundStyle(.secondary)
        } header: { Label("Transcription record", systemImage: "text.bubble") }
    }

    private var soundEvents: some View {
        Section {
            if model.sounds.isEmpty { Text("No sound events right now.").foregroundStyle(.secondary) }
            ForEach(model.sounds.values.sorted { $0.updated > $1.updated }) { s in
                Text(soundLines(s).joined(separator: "\n"))
                .font(.caption.monospaced())
            }
            Text("The laptop sends one label and its score per event, not its top-3. A label under its bar is shown as unknown.")
                .font(.footnote).foregroundStyle(.secondary)
        } header: { Label("Sound labels", systemImage: "waveform") }
    }
}
