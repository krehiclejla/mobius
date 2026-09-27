// `device.motion` — tilt and motion readings for a visible mini-app.
//
// The opaque app frame cannot use the sensor events itself: iOS grants them
// only after `requestPermission()` in a trusted document, and other browsers
// withhold them from cross-origin frames. The shell listens on its own window
// and streams bounded, rate-limited samples to the exact frame that asked.
// Samples stop when the app finishes, cancels, or stops being visible.

export const DEVICE_MOTION = 'device.motion'

const DEFAULT_RATE_HZ = 60

function vector(value) {
  if (!value) return null
  return { x: value.x ?? null, y: value.y ?? null, z: value.z ?? null }
}

function rotation(value) {
  if (!value) return null
  return { alpha: value.alpha ?? null, beta: value.beta ?? null, gamma: value.gamma ?? null }
}

function screenAngle(target) {
  const angle = target.screen?.orientation?.angle
  return Number.isFinite(angle) ? angle : 0
}

// iOS exposes a static permission request on each event constructor. It must
// run while the tap that opened the capability still grants user activation;
// a tap inside the app frame activates this ancestor document too.
async function requestSensorPermission(target) {
  const gated = [target.DeviceMotionEvent, target.DeviceOrientationEvent]
    .filter((constructor) => typeof constructor?.requestPermission === 'function')
  if (!gated.length) return 'not-required'
  const answers = await Promise.all(gated.map((constructor) => constructor.requestPermission()))
  return answers.every((answer) => answer === 'granted') ? 'granted' : 'denied'
}

export function createDeviceMotionProvider({ target = globalThis, now = () => Date.now() } = {}) {
  return {
    version: 1,
    exclusive: false,
    onDeactivate: 'finish',
    async open({ input, declaration, channel }) {
      const declaredMax = Number(declaration?.limits?.max_rate_hz) || DEFAULT_RATE_HZ
      const requested = Number(input?.rateHz)
      const rateHz = Math.max(1, Math.min(declaredMax, Number.isFinite(requested) ? requested : declaredMax))
      const minIntervalMs = 1000 / rateHz

      const permission = await requestSensorPermission(target)
      if (permission === 'denied') {
        throw Object.assign(new Error('Motion sensor access was not allowed.'), {
          name: 'NotAllowedError', code: 'permission_denied',
        })
      }

      let motion = null
      let orientation = null
      let lastSentAt = -Infinity
      let samples = 0
      let stopped = false

      const send = () => {
        const at = now()
        if (at - lastSentAt < minIntervalMs) return
        lastSentAt = at
        samples += 1
        channel.event('sample', { motion, orientation, screenAngle: screenAngle(target), at })
      }
      const onMotion = (event) => {
        motion = {
          acceleration: vector(event.acceleration),
          accelerationIncludingGravity: vector(event.accelerationIncludingGravity),
          rotationRate: rotation(event.rotationRate),
          intervalMs: event.interval ?? null,
        }
        send()
      }
      const onOrientation = (event) => {
        orientation = {
          alpha: event.alpha ?? null,
          beta: event.beta ?? null,
          gamma: event.gamma ?? null,
          absolute: Boolean(event.absolute),
        }
        send()
      }
      target.addEventListener('devicemotion', onMotion)
      target.addEventListener('deviceorientation', onOrientation)

      const stop = () => {
        if (stopped) return false
        stopped = true
        target.removeEventListener('devicemotion', onMotion)
        target.removeEventListener('deviceorientation', onOrientation)
        return true
      }

      channel.ready({ permission, rateHz })
      return {
        control(action) {
          if (action === 'finish') {
            if (stop()) channel.result({ samples })
          } else if (action === 'cancel') {
            if (stop()) {
              channel.error({ name: 'AbortError', code: 'aborted', message: 'Motion readings were cancelled.' })
            }
          }
        },
      }
    },
  }
}
