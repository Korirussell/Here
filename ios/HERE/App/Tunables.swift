import CoreGraphics
import Foundation

/// Every (tune) number in the app, in one place (README §22: tunables in one config file).
enum IndicatorPlacement { case corner, perimeter }

enum Tunables {
    // Attribution (README §13)
    static let matchDeg = 15.0            // face within this of the speech bearing
    static let tieDeg = 5.0               // two faces this close in distance: prefer the recent speaker
    static let moveDeg = 30.0             // re-attribute if the bearing moves this far...
    static let moveHoldS = 1.0            // ...for this long
    static let relabelWindowS = 5.0       // a lost face back within this keeps its "Person N"
    // (tune) widened from 12: a brief track drop-and-recreate (common handheld/close-range, where
    // small camera movement swings the computed bearing a lot) must still match the same physical
    // person to the same "Person N", or every drop mints a fresh label -- the "cycling through
    // Person 1, 2, 3..." symptom.
    static let relabelDeg = 25.0          // ...if it reappears this close to where it was
    static let faceLostHoldS = 0.5        // a tracked face gone this long -> caption falls back to off-camera

    // Faces (README §14 pipeline)
    static let detectEveryFrames = 6      // detection 5 Hz at 30 fps, Vision tracking in between
    static let detectEveryFramesHot = 12  // thermal .serious: halve the detection rate
    // (tune) widened from 0.3: VNTrackObjectRequest(.fast) drops a track the instant one frame's
    // confidence dips below this, no grace period -- too easy to trip at close/handheld range.
    static let trackMinConfidence: Float = 0.2
    static let faceMatchIoU = 0.25
    // Two detections (or two tracks) overlapping more than this are treated as one real face, not two.
    static let faceDedupIoU = 0.3
    // (tune) widened from 2: how many consecutive missed *detections* (every detectEveryFrames) before
    // giving up on a track, i.e. up to this many * detectEveryFrames frames of grace.
    static let missedDetectionsBeforeDrop = 3
    static let maxFaces = 6

    // Camera
    static let fps: Int32 = 30
    static let defaultHFOVUltraWide = 106.0   // used until the device format reports one (or Settings overrides)
    /// Derived from defaultHFOVUltraWide assuming a 16:9 capture format (the hd1080/hd720 presets in
    /// CameraController), until the device's actual format dimensions report a precise aspect ratio.
    /// Used when mounted in portrait (Geo.effectiveHFOVDeg) before the camera has started.
    static let defaultVFOVUltraWide: Double = {
        let aspect = 9.0 / 16.0
        return 2 * atan(tan(defaultHFOVUltraWide / 2 * .pi / 180) * aspect) * 180 / .pi
    }()

    // Captions
    static let captionHoldS = 6.0         // final caption stays this long
    static let typingTimeoutS = 3.0       // speech_start with no words: drop the typing dots after this
    static let captionMaxWidth: CGFloat = 340
    static let ringHoldS = 1.2            // ring stays this long after the last word

    // Alerts panel
    static let alertFadeS = 4.0           // ended alerts fade out after this

    // Belt (README §14 belt logic)
    static let beltFrontDeg = 30.0        // speech in front within this: no buzz
    static let beltSamePersonS = 5.0      // don't re-buzz the same person within this
    static let beltNonAlertGapS = 1.5     // at most one non-alert pattern per this
    static let beltOnsetMinConfidence = 0.5  // speech_start below this waits for the utterance's bearing

    // Network
    static let heartbeatS = 2.0
    static let clockPingS = 10.0
    static let relayPrimaryConnectS = 2.0 // primary relay must connect within this, else the fallback takes over
    static let relayPrimaryRetryS = 5.0   // while on the fallback, try the primary again this often
    static let sceneHz = 3.0              // extras only (Settings: send scene)

    // MARK: - Spatial presentation (Settings > Developer > Visual style: Spatial V2)
    //
    // Presentation only. Nothing below filters what the phone receives or tracks; it decides what
    // earns a place on screen, how strongly, and for how long.

    // Room baseline + prominence: "how far above the room's normal level is this sound?"
    static let baselineRiseS = 6.0             // the baseline creeps up toward a louder room this slowly...
    static let baselineFallS = 2.0             // ...and settles back down this quickly
    static let prominenceFullScale = 0.35      // `sources` energy this far above the baseline = fully prominent
    static let prominencePeakHoldS = 4.0       // a sound's peak prominence decays with this time constant
    static let unknownLevelProminence = 0.5    // no level data near the sound: confidence x this
    static let soundSourceMatchDeg = 25.0      // a `sources` entry this close to a sound's bearing is its level
                                               // (only when the sound names no `source_id` that is in the list)

    // perception_enrichment: the laptop's attention judgment for an earlier caption or sound. It only
    // ever arrives after the event; without one, the rules above decide exactly as before.
    static let enrichmentKeepS = 30.0          // a judgment is kept this long after it arrived
    static let enrichAmbientOpacityMax = 0.5   // ambient = minimal: never more opaque than this
    static let enrichAmbientTier = 2.0         // ...and the first to wait when places run out
    static let enrichImportantTier = 4.5       // important = a full card, ranked like an ordinary alert
    static let enrichUrgentTier = 9.0          // urgent = interrupt: top of the stack, with danger
    static let enrichSpeechTier = 7.0          // speech judged important (or addressed to the wearer)
    static let enrichSpeechOpacityFloor = 0.6  // ...stays readable after the words stop
    static let directedAtUserRaise = 0.8       // `directed_at_user` at or above this raises speech; conservative
    static let enrichBeltPriorityImportant = 2 // belt: an important sound buzzes like a priority-2 alert...
    static let enrichBeltPriorityUrgent = 3    // ...an urgent one like priority 3 (the emergency pattern)

    // Relevance: score = (semantic x weight + prominence x weight) x habituation
    static let semanticImportance: [SoundLabel: Double] = [
        .alarm: 1.0, .doorbell: 0.9, .babyCrying: 0.9, .loudBang: 0.8, .knock: 0.75, .phoneRinging: 0.7,
        .speech: 0.6, .dogBarking: 0.55, .laughter: 0.35, .other: 0.3, .music: 0.25, .tv: 0.25,
        .siren: 1.0, .carHorn: 0.8, .vehicle: 0.5, .door: 0.6, .unknown: 0.3,
    ]
    static let semanticImportanceDefault = 0.3
    static let semanticWeight = 0.5
    static let prominenceWeight = 0.5
    static let suppressBelowScore = 0.30       // below this: received and tracked, not drawn
    static let relevantAtScore = 0.55          // at or above: Level 2 (relevant); between: Level 1 (ambient)
    static let urgentToneAtSemantic = 0.75     // a sound this important takes the alert color, not its category's
    static let habituateAfterS = 8.0           // a steady sound starts losing importance after this...
    static let habituateOverS = 10.0           // ...reaching the floor this much later
    static let habituationFloor = 0.35
    static let speechBaseScore = 0.75          // speech outranks ordinary sounds; loudness fills the rest
    static let pinnedIdleScore = 0.5
    static let pinnedIdleProminence = 0.35
    static let pinnedRankBoost = 10.0          // a pinned person competes one level up
    static let alertBaseScore = 1.0
    static let maxPerimeterIcons = 5           // notifications drawn at once; the rest stay tracked and take a place when one frees up

    // Lifetimes, seconds after a sound's last update
    static let soundLifetimeS = 4.5            // every sound stays up equally long; intensity shows as opacity, not duration
    static let presenceFadeOutS = 0.8          // the tail of a lifetime fades over this
    static let presenceFadeInS = 0.25

    // Opacity by level, from least to most prominent (opacity is the distance/prominence cue)
    static let soundOpacityMin = 0.3           // the faintest sound that is shown at all
    static let soundOpacityMax = 1.0           // the most intense
    static let speechOpacityMin = 0.85
    static let speechOpacityMax = 1.0
    static let speechHeldOpacity = 0.55        // speech ended, its caption is still up
    static let pinnedIdleOpacity = 0.45

    // Where directional indicators go. `.corner`: a stack of notifications in the top-right, each an
    // icon inside a ring with a green dot on the ring toward the sound (out of the wearer's way).
    // `.perimeter`: the earlier design, icons on an invisible ellipse around the middle of the view.
    static let indicatorPlacement = IndicatorPlacement.corner
    static let cornerSlot: CGFloat = 72               // one notification's height in the stack (fits the largest ring)
    static let cornerRingPadding: CGFloat = 9         // ring radius beyond the icon disc
    static let cornerDotSize: CGFloat = 9             // the green direction dot
    static let cornerTop: CGFloat = 14
    static let cornerTrailing: CGFloat = 18

    // The invisible perimeter (`.perimeter` only): an ellipse around the middle of the view, front at the top
    static let perimeterRadiusX: CGFloat = 0.41       // fraction of the view's width
    static let perimeterRadiusY: CGFloat = 0.37       // fraction of the view's height
    static let perimeterVerticalOffset: CGFloat = 0.02  // center shift, fraction of height (+ = down)
    static let perimeterStackInset: CGFloat = 0.17    // a lower-priority icon at the same bearing sits this much further in
    static let perimeterMinSeparationDeg = 16.0       // icons closer than this share a bearing and stack
    static let inViewAnchorY: CGFloat = 0.42          // where a resolved sound lands vertically, fraction of height
    static let unknownDirectionSpacing: CGFloat = 150 // sounds with no bearing sit in a row at the top center
    static let pathFollowS = 0.12                     // smoothing while an icon follows the wearer's turn

    // Resolution: offscreen -> centered -> dissolved (or handed to a tracked face)
    static let inwardThresholdDeg = 35.0       // closer to center than this: the icon starts moving inward
    static let resolveCompleteDeg = 10.0       // this close: it has arrived where the sound is
    static let resolveHoldS = 0.5              // the localized pulse plays for this long...
    static let resolveDissolveS = 0.7          // ...then the treatment dissolves over this
    static let handoffS = 0.6                  // perimeter icon -> tracked face (the turn-around)

    // Icons
    static let iconSize: CGFloat = 40
    static let iconScaleAmbient: CGFloat = 0.85
    static let iconScaleRelevant: CGFloat = 1.0
    static let iconScaleSpeech: CGFloat = 1.1
    static let iconScaleImportant: CGFloat = 1.3
    static let iconLabelEdge: CGFloat = 130    // an icon this close to a side edge grows its words inward
    static let iconPlateOpacity = 0.45         // dark disc behind the symbol so it reads over a bright scene
    static let pulseScale: CGFloat = 1.9
    static let pulseDurationS = 0.7
    static let urgentPulsePeriodS = 1.1        // Level 4 pulses for as long as the event is active
    static let pulseStrengthMin = 0.35         // pulse ring opacity for the weakest sound...
    static let pulseStrengthMax = 0.95         // ...and the strongest

    // Speaker aura (around the head, never over the mouth)
    static let auraExpandX: CGFloat = 2.7      // horizontal extent, in face-box widths
    static let auraExpandY: CGFloat = 2.5      // vertical extent, in face-box heights
    static let auraInnerClear: CGFloat = 0.34  // fraction of the radius left fully clear (eyes and mouth)
    static let auraPeakAt: CGFloat = 0.52      // where the glow is strongest: just outside the head
    static let auraBlurRadius: CGFloat = 16
    static let auraRimOpacity = 0.55           // a tighter, brighter band inside the haze
    static let auraActiveOpacityMin = 0.5      // speaking quietly...
    static let auraActiveOpacity = 0.9         // ...speaking loudly (smoothed speech intensity in between)
    static let auraRestOpacity = 0.18          // speech ended, caption still up: a quiet tie to the person
    static let auraPinnedOpacity = 0.0         // a silent pinned person: no glow (the tag says it)

    // Object aura: a visible object that a sound has been attributed to glows in the sound's color
    static let auraAttachConfidence = 0.55     // fusion score needed before an object glows (below: top-right notification only)
    static let objectAuraOpacityMin = 0.35     // the quietest sound...
    static let objectAuraOpacityMax = 0.8      // ...the loudest (same smoothed intensity as the notification's opacity)
    static let objectAuraDangerBoost = 1.25    // a warning glows harder than an ordinary sound
    static let auraMaxOpacity = 0.95
    static let objectAuraExpand: CGFloat = 1.7 // glow extent relative to the object's box
    static let objectAuraInnerClear: CGFloat = 0.25  // the middle stays mostly clear so the object itself is visible
    static let objectAuraPeakAt: CGFloat = 0.6
    static let objectAuraCoarseSize: CGFloat = 0.6   // an object known only by region: glow this fraction of the region's width
    static let objectAuraMinSize: CGFloat = 70
    static let objectAuraMaxAspect: CGFloat = 1.6    // a tall or wide box still gets a rounded glow, not a streak
    static let objectAuraPulse: CGFloat = 0.03       // small pulse...
    static let objectAuraPulseIntensity: CGFloat = 0.04  // ...a little larger with intensity; none with Reduce Motion
    static let auraCrowdCount = 3              // more glowing things than this in view...
    static let auraCrowdDim = 0.7              // ...and the non-warning object glows are dimmed by this
    static let auraFadeS = 1.2                 // active -> resting
    static let auraRestFadeS = 1.0             // resting -> gone, at the end of the caption's hold
    static let auraBreathScale: CGFloat = 0.03         // pulse while speaking quietly...
    static let auraBreathLoudnessScale: CGFloat = 0.05 // ...plus this much more at full intensity
    static let auraBreathS = 0.9
    static let soundMergeDeg = 20.0            // a "new" sound event this close to a live one of the same kind is the same source
    static let soundMergeS = 2.5
    static let speakingBadgeSize: CGFloat = 26

    // Pinned people (session only; keyed by the tracker's "Person N")
    static let pinValidS = relabelWindowS      // an out-of-view pin's bearing is trusted exactly as long as the tracker could still re-identify them
    static let pinLostNoticeS = 4.0            // "no longer tracked" stays up this long, then the pin is dropped
    static let pinnedTagOpacity = 0.7
    static let nametagRevealS = 4.0            // tapping a face shows its name tag (and pin control) for this long
    static let pinHitSize: CGFloat = 32

    // MARK: - Session people (identity above Vision tracks; on-device only)

    static let sessionPersonExpiryS = 900.0    // an unpinned, unenrolled person gone this long is forgotten
    static let spatialCredibleS = 8.0          // out of view, a last-known bearing is shown for this long after they were last seen or heard
    // Re-identification. Two evidence channels, either of which can carry a match on its own:
    //   appearance (what they look like) and place+time (where they were, how recently);
    //   score = 1 - (1 - visualWeight x appearance)(1 - spatialWeight x placeAndTime) + continuity bonuses
    static let reidVisualWeight = 1.0
    static let reidBearingWeight = 0.9         // weight of the place+time channel
    static let reidBodyBonus = 0.15            // a body detection carried them through the gap
    static let reidAudioBonus = 0.10           // they were just heard from this direction
    static let reidPinBonus = 0.03             // pin continuity: a pinned person is expected back
    static let reidEdgeBonus = 0.05            // came back in at the side of the frame they left by
    static let reidAccept = 0.5
    // Stage 1, continuity: a track that reappears where someone just was, moments later, is them.
    static let reidContinuityS = 2.0           // "moments later"
    static let reidContinuityAccept = 0.6      // place-and-time score needed (discounted by poor appearance)
    // Hysteresis: an identity, once given, is kept unless a rival is clearly better.
    static let reidSwitchMargin = 0.2
    static let reidPendingS = 2.0              // an ambiguous new track waits this long for better evidence before taking the best match
    // Descriptor bank: only good views of a person are kept as what they look like.
    static let descriptorMinFaceConfidence = 0.75
    static let descriptorMinFacePixels = 36.0
    static let reidAcceptPinned = 0.5
    static let reidHighConfidence = 0.75       // shown as a confident match in diagnostics
    static let reidAmbiguityMargin = 0.10      // best must lead the runner-up by this, or nobody is matched
    static let reidVisualVeto = 0.15           // appearance this unlike: not them, wherever they are standing
    static let reidVisualSupport = 0.6         // below this, appearance discounts "they were just here" proportionally
    static let reidBearingBaseDeg = 25.0       // bearing tolerance right after they vanish...
    static let reidBearingDegPerS = 3.0        // ...widening as time passes (people move)...
    static let reidBearingMaxDeg = 60.0
    static let reidBearingRelevantS = 10.0     // ...until the old bearing says nothing at all
    static let reidTemporalTauS = 20.0
    static let reidAudioRecentS = 2.0
    static let reidEdgeFraction = 0.2          // "at the side of the frame": within this of an edge
    static let reidDecisionLog = 6             // decisions kept for the debug overlay
    // Appearance: Vision feature prints of the face region and of the torso (clothing), a small
    // gallery per person so different poses are covered. (tune on device: the two distances below)
    static let printDistanceSame = 0.35        // feature-print distance at or under this = similarity 1
    static let printDistanceDifferent = 1.05   // at or over this = similarity 0
    static let printGallerySize = 6            // descriptors kept per person, per region
    static let printNoveltyDistance: Float = 0.25  // a new descriptor is kept only if it differs this much
    static let printRefreshEveryDetects = 5    // re-identification cadence while visible (~1 s at 5 Hz detection)
    static let printFaceExpand: CGFloat = 1.3  // face crop, in face-box sizes
    static let printTorsoWidthFaces: CGFloat = 2.4
    static let printTorsoHeightFaces: CGFloat = 2.0
    static let assocVisualMin = 0.5            // a detection far from any track is still that track's person if it looks this alike
    // Body fallback: a person detection holds a track when the face stops being detected
    static let bodyFallbackEnabled = true
    static let bodyMatchIoU = 0.3
    static let bodyHeadWidthFraction: CGFloat = 0.38  // estimated head width inside an upper-body box
    static let smallFacePixels = 40.0          // diagnostics: a face shorter than this is flagged "small"
    // Attribution for an established person (their audio source is known and still theirs)
    static let matchDegEstablished = 25.0
    // (tune) Spread-aware attribution: the laptop's bearing_spread_deg widens the match window up to
    // this (a fixed-beam ReSpeaker that only knows "front" sends ~30, so a lone talker 25 deg off-centre
    // still gets the caption on their face). Capped so a "could be anywhere" spread can't grab a face
    // across the room. A spread <= matchDeg leaves attribution exactly as before.
    static let matchDegSpreadMax = 40.0
    // Score given when several faces sit in a spread-widened window and nothing (audio-source owner,
    // recent speaker) tells them apart: the nearest is picked but marked tentative (README principle 4).
    static let tentativeAttributionScore = 0.2
    // Known people (explicit enrollment only)
    static let knownMatchSimilarity = 0.8
    static let knownMatchMargin = 0.1

    // MARK: - Distance tiers (how much to trust the face as it gets smaller)

    static let faceClosePixels = 110.0         // face at least this tall: close. Face identity is strong.
    static let faceFarPixels = 45.0            // face shorter than this: far. Body, clothing and continuity carry identity.
    static let faceWeightClose = 0.55          // share of appearance similarity taken from the face when close...
    static let faceWeightFar = 0.15            // ...and when far (the rest is torso / clothing)
    static let faceMinConfidence: Float = 0.5  // a detection needs this to start a *new* track
    static let faceWeakConfidence: Float = 0.25  // ...but this is enough to keep an existing one going
    static let personHistorySize = 8           // recent bearings / audio sources remembered per person
    static let personHistoryEveryS = 1.0

    // MARK: - Speaker locks (which person an active speech source belongs to)

    static let speakerLockThreshold = 0.3      // a new source needs at least this claim (within ~10 deg) to be given to someone
    static let speakerAmbiguityMargin = 0.15   // ...and to lead the next person by this; otherwise it stays unattributed
    static let existingSpeakerContinuityBonus = 0.6   // the current holder of a source is hard to displace
    static let speakerLockMatchDeg = 30.0      // the holder still "agrees" with their source out to this angle (new claims: matchDeg)
    static let speakerSwitchMargin = 0.25      // a challenger must beat the holder by this...
    static let speakerSwitchHoldS = 1.0        // ...continuously for this long
    static let speakerReleaseHoldS = 2.5       // silence shorter than this is a pause, not the end
    static let speakerOcclusionGraceS = 3.0    // after the holder disappears, nobody can take the source for this long
    static let speakerSourceBearingDeg = 25.0  // a new sentence this close to a live source (same stream) is that source
    static let audioBearingSmoothing = 0.4     // how far one direction reading moves a source's smoothed bearing
    static let mouthActivityWeight = 0.3       // lip movement added to a plausible candidate's score
    static let mouthActivitySmoothing = 0.5
    static let mouthActivityFull = 0.012       // (tune on device) std of lip opening / face height that counts as clearly talking
    static let mouthMinSamples = 4
    static let mouthHistory = 6                // samples kept (~1.2 s at 5 Hz detection)
    static let speakerLogSize = 12
    static let reidPredictS = 2.0              // motion prediction: a vanished person is expected along their path for this long
    static let bearingVelocitySmoothing = 0.3

    // MARK: - Room geometry and calibration

    static let assumedSourceRadiusM = 1.5      // sources are assumed this far from the array unless something better is known
    static let faceHeightM = 0.21              // a face box is about this tall in the real world
    static let faceDistanceMaxM = 8.0
    static let calibrationMinSamples = 3
    static let calibrationMaxSamples = 40
    static let calibrationGoodDeg = 6.0        // mean absolute error at or under this: GOOD
    static let calibrationFairDeg = 12.0       // ...at or under this: FAIR; over: BAD
    static let calibrationDirectionSpreadDeg = 30.0   // samples must span more than this to tell error types apart
    static let fusionUncertaintyFullDeg = 30.0 // geometric or calibration uncertainty this large: no object attachments at all
    static let transcriptLogSize = 12

    // MARK: - Sound labels on the phone (the laptop sends a label and its raw classifier score)

    // A label scoring under its bar is shown as unknown ("?") with its direction, not as a confident
    // wrong icon. Short impacts (doorbell, knock, door) legitimately score low on the laptop's model,
    // so their bars are the laptop's own thresholds; everything else needs a clear score.
    static let soundLabelMinConfidence: [SoundLabel: Double] = [
        .doorbell: 0.25, .knock: 0.2, .door: 0.2, .loudBang: 0.5, .dogBarking: 0.5, .babyCrying: 0.4,
    ]
    static let soundLabelDefaultMinConfidence = 0.45
    static let soundLabelSwitchRatio = 1.3     // a continuing sound changes label only when the challenger has this much more evidence
    static let soundVoteDecay = 0.8            // older classifier windows count for less

    // MARK: - Scene objects (quiet, stationary things; on-device, Apple Vision only)

    static let objectScanIntervalS = 1.0       // one scan about every second
    static let objectStripCount = 5            // overlapping vertical strips classified per scan...
    static let objectStripWidth: CGFloat = 0.34
    static let objectSalientRegions = 3        // ...plus up to this many salient-object regions
    static let objectLabelsConsidered = 8      // top classifier labels looked at per region
    static let objectMinConfidence = 0.35
    static let objectConfirmSightings = 2      // seen this many times before it's believed
    static let objectSmoothing = 0.3           // how far a new sighting moves its bearing/size/confidence
    static let objectBearingToleranceDeg = 18.0  // same object if within this (plus half its width): strips are coarse
    static let objectBearingTolerancePreciseDeg = 6.0  // ...and within this when both sightings are tight detector boxes
    static let objectDetectIntervalS = 0.33      // with a bundled detector: about 3 scans a second
    static let objectDetectorMinConfidence = 0.4
    static let objectClassifierEveryScans = 3    // with a detector, the region classifier only runs every Nth scan (for doors, windows...)
    static let objectBearingWeight = 0.6
    static let objectSizeWeight = 0.2
    static let objectVisualWeight = 0.2
    static let objectReacquireScore = 0.4
    static let objectVisibleS = 2.5            // seen this recently: visible
    static let objectLostS = 6.0               // in view but unseen this long: temporarily lost, then dormant
    static let objectReacquiredFlagS = 2.0
    static let objectGoneS = 20.0              // should be in view and hasn't been found for this long: it's gone
    static let objectUnconfirmedGoneS = 4.0
    static let objectExpiryS = 900.0           // never seen again in this long: forgotten
    // Fusion: which person or object occupies the direction a sound comes from
    static let fusionBearingToleranceDeg = 15.0  // plus half the candidate's width
    static let fusionBearingWeight = 0.5         // spatial correspondence is primary...
    static let fusionVisibilityWeight = 0.15     // ...something in view right now counts for more than something remembered...
    static let fusionSpatialWeight = 0.15        // ...as does being sure where it is
    static let fusionSemanticWeight = 0.2        // optional: the kind of sound fits the object (a tie-breaker, never required)
    static let fusionPriorWeight = 0.2           // this source was already attributed to it: don't flip
    static let fusionAcceptScore = 0.45
    static let fusionAmbiguityMargin = 0.12      // lead needed over the next candidate
    static let fusionOccupyAgreement = 0.5       // a candidate this well aligned "occupies" the direction
    static let fusionPersonHalfWidthDeg = 5.0

    // MARK: - Spatial notifications: intensity, lifecycle, priority

    static let intensityAttackS = 0.08         // opacity rises this fast with the sound...
    static let intensityReleaseS = 0.45        // ...and falls a little more smoothly
    static let dangerOpacityFloor = 0.85       // a warning never fades into the background
    static let alertOpacityFloor = 0.7
    static let soundReleaseHoldS = 1.5         // a sound that went quiet is kept this long in case it resumes (same for every sound)
    static let speechReleaseHoldS = 1.5
    static let notificationFadeS = 0.6         // then it fades over this and is removed
    static let pinnedCaptionWidth: CGFloat = 240

    // Caption plate (Spatial V2): flat black at this alpha keeps white and pending text >= 4.5:1 even
    // over a pure white scene (PresentationTests.testCaptionPlateContrast), while a third of the
    // environment still shows through.
    static let captionPlateOpacity = 0.66
    static let captionPendingWhite = 0.85      // pending words: opaque gray of this brightness
    static let captionBorderOpacity = 0.55
    static let captionAttachGap: CGFloat = 10  // between a perimeter icon and its caption

    // Quiet chrome (Spatial V2): persistent UI has to earn its place
    static let beltIndicatorHoldS = 2.0        // the belt indicator shows for this long after a buzz
    static let quietChromeOpacity = 0.4
}
