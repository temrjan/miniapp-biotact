/** Channel-post texts, assembled on the client from the draw the server returned (P-6).
 * Both are plain strings — the post is a manual operation, and what reaches the channel
 * must be exactly what a verifier later re-checks. */

interface DrawForPost {
  draw_number: number
  total_tickets: number
  participant_hash: string | null
}

export const fingerprintPostText = (draw: DrawForPost): string =>
  [
    `🎟 Розыгрыш №${draw.draw_number} — список участников запечатан.`,
    ``,
    `Билетов: ${draw.total_tickets}`,
    `Отпечаток (SHA-256):`,
    draw.participant_hash ?? '',
    ``,
    `После розыгрыша опубликуем полный список — каждый может пересчитать хеш командой sha256sum и сверить.`,
  ].join('\n')

export const cancelPostText = (draw: DrawForPost): string =>
  [
    `⚠️ Розыгрыш №${draw.draw_number} отменён.`,
    ``,
    `Опубликованный ранее отпечаток больше не действителен:`,
    draw.participant_hash ?? '',
    ``,
    `Билеты возвращены участникам и сыграют в следующем розыгрыше.`,
  ].join('\n')
