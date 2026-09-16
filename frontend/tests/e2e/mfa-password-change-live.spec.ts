import { createHmac } from 'node:crypto'

import { expect, test } from '@playwright/test'

const BACKEND_URL = process.env.VITE_API_BASE_URL || 'http://localhost:8000'

function decodeBase32(value: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const character of value.replaceAll('=', '').toUpperCase()) {
    const index = alphabet.indexOf(character)
    if (index < 0) throw new Error(`Unsupported base32 character: ${character}`)
    bits += index.toString(2).padStart(5, '0')
  }
  const bytes: number[] = []
  for (let offset = 0; offset + 8 <= bits.length; offset += 8) {
    bytes.push(Number.parseInt(bits.slice(offset, offset + 8), 2))
  }
  return Buffer.from(bytes)
}

function totp(secret: string, step: number): string {
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(step))
  const digest = createHmac('sha1', decodeBase32(secret)).update(counter).digest()
  const offset = digest[digest.length - 1] & 0x0f
  const binary =
    ((digest[offset] & 0x7f) << 24) |
    ((digest[offset + 1] & 0xff) << 16) |
    ((digest[offset + 2] & 0xff) << 8) |
    (digest[offset + 3] & 0xff)
  return (binary % 1_000_000).toString().padStart(6, '0')
}

test.describe('MFA after password-change revocation — live contract', () => {
  test.use({ storageState: { cookies: [], origins: [] } })
  test.beforeEach(({ browserName }) => {
    test.skip(browserName !== 'chromium', 'This is an API contract; the UI journey runs in all browsers.')
  })

  test('accepts a newly issued MFA challenge immediately after the cutoff', async ({ request }) => {
    const adminPassword = process.env.E2E_ADMIN_PASSWORD
    if (!adminPassword) throw new Error('E2E_ADMIN_PASSWORD is required')

    const marker = `exp-m01-mfa-${Date.now().toString().slice(-10)}`
    const initialPassword = 'Tmp-M01-Mfa-Start-9!'
    const permanentPassword = 'Tmp-M01-Mfa-Permanent-9!'
    const changedPassword = 'Tmp-M01-Mfa-Changed-9!'
    let adminToken = ''
    let userId = ''

    const login = (password: string) => request.post(`${BACKEND_URL}/api/v1/auth/login`, {
      form: { username: marker, password },
    })

    try {
      const adminLogin = await request.post(`${BACKEND_URL}/api/v1/auth/login`, {
        form: { username: 'admin', password: adminPassword },
      })
      expect(adminLogin.status()).toBe(200)
      adminToken = (await adminLogin.json()).access_token

      const registration = await request.post(`${BACKEND_URL}/api/v1/auth/register`, {
        data: {
          email: `${marker}@example.com`,
          username: marker,
          full_name: 'M01 MFA Synthetic User',
          password: initialPassword,
        },
      })
      expect(registration.status()).toBe(201)
      userId = (await registration.json()).id

      const firstLogin = await login(initialPassword)
      expect(firstLogin.status()).toBe(200)
      const firstToken = (await firstLogin.json()).access_token
      const reset = await request.post(`${BACKEND_URL}/api/v1/auth/first-time-reset`, {
        headers: { Authorization: `Bearer ${firstToken}` },
        data: { new_password: permanentPassword, confirm_password: permanentPassword },
      })
      expect(reset.status()).toBe(204)

      const permanentLogin = await login(permanentPassword)
      expect(permanentLogin.status()).toBe(200)
      const permanentToken = (await permanentLogin.json()).access_token
      const permanentHeaders = { Authorization: `Bearer ${permanentToken}` }

      const enrollment = await request.post(`${BACKEND_URL}/api/v1/auth/mfa/enroll/start`, {
        headers: permanentHeaders,
        data: {},
      })
      expect(enrollment.status()).toBe(200)
      const secret = (await enrollment.json()).secret as string
      const currentStep = Math.floor(Date.now() / 1000 / 30)
      const confirmation = await request.post(`${BACKEND_URL}/api/v1/auth/mfa/enroll/confirm`, {
        headers: permanentHeaders,
        data: { code: totp(secret, currentStep - 1) },
      })
      expect(confirmation.status()).toBe(200)

      const change = await request.post(`${BACKEND_URL}/api/v1/auth/change-password`, {
        headers: permanentHeaders,
        data: { current_password: permanentPassword, new_password: changedPassword },
      })
      expect(change.status()).toBe(204)

      const challengedLogin = await login(changedPassword)
      expect(challengedLogin.status()).toBe(200)
      const challenge = await challengedLogin.json()
      expect(challenge.mfa_required).toBe(true)
      expect(challenge.challenge_token).toBeTruthy()

      const verification = await request.post(`${BACKEND_URL}/api/v1/auth/mfa/verify`, {
        data: {
          challenge_token: challenge.challenge_token,
          code: totp(secret, currentStep),
        },
      })
      expect(verification.status()).toBe(200)
      const session = await verification.json()
      expect(session.access_token).toBeTruthy()

      const me = await request.get(`${BACKEND_URL}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      })
      expect(me.status()).toBe(200)
      expect((await me.json()).id).toBe(userId)
    } finally {
      if (adminToken && userId) {
        const cleanup = await request.patch(`${BACKEND_URL}/api/v1/users/${userId}/status`, {
          headers: { Authorization: `Bearer ${adminToken}` },
          data: { is_active: false },
        })
        expect(cleanup.status()).toBe(200)
      }
    }
  })
})
