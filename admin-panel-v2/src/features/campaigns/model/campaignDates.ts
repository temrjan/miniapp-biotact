export const CAMPAIGN_ZONE = 'Ташкент · UTC+05:00'

interface Instant { seconds: bigint; fraction: string }

function calendarSeconds(date: string): bigint | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})$/.exec(date)
  if (!match) return null
  const [year, month, day, hour, minute, second] = match.slice(1).map(Number)
  if (year === undefined || month === undefined || day === undefined || hour === undefined ||
      minute === undefined || second === undefined || year < 1 || month < 1 || month > 12 ||
      day < 1 || day > 31 || hour > 23 || minute > 59 || second > 59) return null
  const value = new Date(0)
  value.setUTCFullYear(year, month - 1, day)
  value.setUTCHours(hour, minute, second, 0)
  if (value.getUTCMonth() !== month - 1 || value.getUTCDate() !== day) return null
  return BigInt(value.getTime() / 1000)
}

export function parseInstant(wire: string): Instant | null {
  const match = /^(\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2})(?:\.(\d+))?([Zz]|[+-]\d{2}:\d{2})$/.exec(wire)
  if (!match?.[1] || !match[3]) return null
  const seconds = calendarSeconds(match[1])
  const zone = match[3]
  if (seconds === null) return null
  let offset = 0
  if (zone.toUpperCase() !== 'Z') {
    const hours = Number(zone.slice(1, 3)), minutes = Number(zone.slice(4, 6))
    if (hours > 23 || minutes > 59) return null
    offset = (hours * 60 + minutes) * 60 * (zone[0] === '+' ? 1 : -1)
  }
  return { seconds: seconds - BigInt(offset), fraction: match[2] ?? '' }
}

export function compareInstants(left: string, right: string): number {
  const a = parseInstant(left), b = parseInstant(right)
  if (!a || !b) throw new Error('Invalid campaign timestamp')
  if (a.seconds !== b.seconds) return a.seconds < b.seconds ? -1 : 1
  const precision = Math.max(a.fraction.length, b.fraction.length)
  const af = a.fraction.padEnd(precision, '0'), bf = b.fraction.padEnd(precision, '0')
  return af === bf ? 0 : af < bf ? -1 : 1
}

export function toTashkentInput(wire: string | null): string {
  if (wire === null) return ''
  const instant = parseInstant(wire)
  if (!instant) throw new Error('Invalid campaign timestamp')
  return new Date(Number(instant.seconds + 18000n) * 1000).toISOString().slice(0, 19)
}

export function fromTashkentInput(input: string): string | null {
  if (!input) return null
  // Native datetime-local may omit :00 even with step=1.
  const full = input.length === 16 ? `${input}:00` : input
  if (calendarSeconds(full) === null) throw new Error('Проверьте дату и время.')
  return `${full}+05:00`
}

export function formatCampaignDate(wire: string | null): string {
  return wire === null ? 'Не задано' : toTashkentInput(wire).replace('T', ' ')
}
