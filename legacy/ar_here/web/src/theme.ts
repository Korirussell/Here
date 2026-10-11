import font400 from '@fontsource/atkinson-hyperlegible/files/atkinson-hyperlegible-latin-400-normal.woff?url'
import font700 from '@fontsource/atkinson-hyperlegible/files/atkinson-hyperlegible-latin-700-normal.woff?url'
import type { Tone } from './contract'

/**
 * Every visual knob lives here so the look can be tuned in one place.
 * Units are screen pixels unless noted.
 */
export const theme = {
  font: {
    // Atkinson Hyperlegible: designed by the Braille Institute for low-vision
    // readers. Distinct letterforms (I/l/1, 0/O) matter for fast caption reading.
    regular: font400,
    bold: font700,
  },
  color: {
    text: '#F4F6FB',
    // Provisional words: dimmer but still above 4.5:1 on the smoked glass.
    textPending: '#A9B3C6',
    // Tint of the glass. Darker = more contrast for white text.
    glassTint: '#3E4452',
    // One accent per speaker, assigned by speaker_id. Never the only cue:
    // every caption also carries the speaker's name.
    speakers: ['#79C7FF', '#FFC27A', '#C9A8FF', '#86E3BC', '#FF9EBB', '#F2E37A'],
  },
  caption: {
    fontSize: 30,
    lineHeight: 1.22,
    maxTextWidth: 470,
    minWidth: 300,
    /** Roughly two lines; older words scroll off the front. */
    maxChars: 78,
    pad: 22,
    headerHeight: 34,
    labelSize: 19,
    radius: 26,
    depth: 26,
    /** Space between the top of a face and the bottom of its caption tail. */
    gap: 30,
    screenMargin: 24,
    tailWidth: 26,
    tailHeight: 14,
  },
  glass: {
    thickness: 34,
    roughness: 0.32,
    ior: 1.22,
    chromaticAberration: 0.035,
    anisotropicBlur: 0.12,
    distortion: 0.08,
    distortionScale: 0.4,
    samples: 6,
    backside: false,
  },
  motion: {
    /** Seconds for a caption to catch up with a moving face. */
    follow: 0.16,
    enter: 0.22,
    /** How long a removed caption takes to shrink away. */
    exitMs: 520,
  },
  bloom: { intensity: 0.7, threshold: 1.0, smoothing: 0.25 },
} as const

/** ?lite=1 swaps refractive glass and bloom for cheap stand-ins (slow GPUs, screenshots). */
export const lite = new URLSearchParams(location.search).has('lite')

export function speakerColor(speakerId: number): string {
  const list = theme.color.speakers
  return list[((speakerId % list.length) + list.length) % list.length]
}

/** Tone chips describe how something sounded, matching captioning convention. */
export const tones: Record<Tone, { label: string; image: string }> = {
  neutral: { label: 'calm', image: '/emoji/neutral.png' },
  excited: { label: 'excited', image: '/emoji/excited.png' },
  laughing: { label: 'laughing', image: '/emoji/laughing.png' },
  shouting: { label: 'shouting', image: '/emoji/shouting.png' },
  whispering: { label: 'whispering', image: '/emoji/whispering.png' },
  questioning: { label: 'asking', image: '/emoji/questioning.png' },
  sad: { label: 'sad', image: '/emoji/sad.png' },
}
