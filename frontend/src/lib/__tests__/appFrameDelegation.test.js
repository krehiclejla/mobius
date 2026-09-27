import { test } from 'node:test'
import assert from 'node:assert/strict'
import { APP_FRAME_ALLOW } from '../appFrameDelegation.js'

function directives(allow) {
  return new Map(allow.split(';').map(part => {
    const [feature, ...origins] = part.trim().split(/\s+/)
    return [feature, origins]
  }))
}

test('game features reach the opaque app document instead of only its src origin', () => {
  const allow = directives(APP_FRAME_ALLOW)
  // A bare entry means `src`, which an opaque sandboxed document never
  // matches, so fullscreen and controllers would be silently denied.
  assert.deepEqual(allow.get('fullscreen'), ['*'])
  assert.deepEqual(allow.get('gamepad'), ['*'])
})

test('raw clipboard writes stay undelegated so apps keep using the host broker', () => {
  assert.deepEqual(directives(APP_FRAME_ALLOW).get('clipboard-write'), [])
})

test('motion sensors are not delegated to app frames', () => {
  const allow = directives(APP_FRAME_ALLOW)
  for (const sensor of ['accelerometer', 'gyroscope', 'magnetometer']) {
    assert.equal(allow.has(sensor), false, sensor)
  }
})
