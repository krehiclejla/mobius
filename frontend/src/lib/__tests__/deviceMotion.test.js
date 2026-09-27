import { test } from 'node:test'
import assert from 'node:assert/strict'

import { builtInCapabilityProviders } from '../capabilityProviders.js'
import { createDeviceMotionProvider, DEVICE_MOTION } from '../deviceMotion.js'

function sensorWindow({ permission } = {}) {
  const listeners = new Map()
  const win = {
    screen: { orientation: { angle: 90 } },
    addEventListener(type, fn) { listeners.set(type, fn) },
    removeEventListener(type, fn) { if (listeners.get(type) === fn) listeners.delete(type) },
    fire(type, event) { listeners.get(type)?.(event) },
    listening: () => [...listeners.keys()].sort(),
  }
  if (permission) {
    const request = async () => permission
    win.DeviceMotionEvent = { requestPermission: request }
    win.DeviceOrientationEvent = { requestPermission: request }
  }
  return win
}

function recordingChannel() {
  const log = []
  return {
    log,
    ready(value) { log.push(['ready', value]) },
    event(name, value) { log.push([name, value]) },
    result(value) { log.push(['result', value]) },
    error(error) { log.push(['error', error.code]) },
  }
}

test('motion samples are clamped to the reviewed rate ceiling', async () => {
  const win = sensorWindow()
  let clock = 0
  const provider = createDeviceMotionProvider({ target: win, now: () => clock })
  const channel = recordingChannel()
  await provider.open({
    input: { rateHz: 1000 },
    declaration: { limits: { max_rate_hz: 10 } },
    channel,
  })
  assert.deepEqual(channel.log[0], ['ready', { permission: 'not-required', rateHz: 10 }])

  win.fire('deviceorientation', { alpha: 1, beta: 2, gamma: 3, absolute: false })
  clock = 50
  win.fire('deviceorientation', { alpha: 9, beta: 9, gamma: 9 })
  clock = 100
  win.fire('devicemotion', { accelerationIncludingGravity: { x: 0, y: 9.8, z: 0 }, interval: 16 })

  const samples = channel.log.filter(([name]) => name === 'sample').map(([, value]) => value)
  assert.equal(samples.length, 2)
  assert.deepEqual(samples[0].orientation, { alpha: 1, beta: 2, gamma: 3, absolute: false })
  assert.equal(samples[0].screenAngle, 90)
  assert.deepEqual(samples[1].motion.accelerationIncludingGravity, { x: 0, y: 9.8, z: 0 })
  assert.deepEqual(samples[1].orientation, { alpha: 9, beta: 9, gamma: 9, absolute: false })
})

test('ios permission prompt is required before any listener attaches', async () => {
  const denied = sensorWindow({ permission: 'denied' })
  await assert.rejects(
    createDeviceMotionProvider({ target: denied }).open({ input: {}, declaration: {}, channel: recordingChannel() }),
    (error) => error.name === 'NotAllowedError' && error.code === 'permission_denied',
  )
  assert.deepEqual(denied.listening(), [])

  const granted = sensorWindow({ permission: 'granted' })
  const channel = recordingChannel()
  await createDeviceMotionProvider({ target: granted }).open({ input: {}, declaration: {}, channel })
  assert.equal(channel.log[0][1].permission, 'granted')
  assert.deepEqual(granted.listening(), ['devicemotion', 'deviceorientation'])
})

test('finish and cancel release the sensors and settle exactly once', async () => {
  for (const [action, expected] of [['finish', ['result', { samples: 0 }]], ['cancel', ['error', 'aborted']]]) {
    const win = sensorWindow()
    const channel = recordingChannel()
    const control = await createDeviceMotionProvider({ target: win }).open({ input: {}, declaration: {}, channel })
    control.control(action)
    control.control('finish')
    control.control('cancel')
    assert.deepEqual(win.listening(), [], action)
    assert.deepEqual(channel.log.slice(1), [expected], action)
  }
})

test('the shell registers device motion as a built-in provider', () => {
  assert.equal(builtInCapabilityProviders()[DEVICE_MOTION].version, 1)
})
