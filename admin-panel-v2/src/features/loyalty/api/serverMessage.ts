import axios from 'axios'

/** The draw page shows mutation refusals as the SERVER wrote them: every refusal of step 3
 * carries the number or draw the administrator needs ("ready to freeze: N", "draw №N is live").
 * A generic "something went wrong" would hide exactly that. */
export const serverMessage = (error: unknown): string => {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string') {
      return detail
    }
    return error.message
  }
  return error instanceof Error ? error.message : String(error)
}
