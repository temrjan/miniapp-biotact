/**
 * Geocoding Utilities
 *
 * Provides reverse geocoding (coordinates → address) using OpenStreetMap Nominatim API.
 * Free service, no API key required, supports multiple languages.
 */

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface NominatimAddress {
  road?: string; // Street name
  house_number?: string; // House number
  suburb?: string; // District/neighborhood
  city?: string; // City name
  state?: string; // State/region
  country?: string; // Country name
  postcode?: string; // Postal code
}

interface NominatimResponse {
  address: NominatimAddress;
  display_name: string; // Full address string
}

export interface ReverseGeocodeResult {
  street: string;
  house: string;
  city: string;
  country: string;
  fullAddress: string;
}

// ═══════════════════════════════════════════════════════════════════
// Constants
// ═══════════════════════════════════════════════════════════════════

const NOMINATIM_API_URL = 'https://nominatim.openstreetmap.org/reverse';
const REQUEST_TIMEOUT = 5000; // 5 seconds
const USER_AGENT = 'Biotact Delivery App'; // Required by Nominatim

// ═══════════════════════════════════════════════════════════════════
// Main Function: reverseGeocode
// ═══════════════════════════════════════════════════════════════════

/**
 * Convert coordinates to human-readable address
 *
 * @param latitude - Latitude (-90 to 90)
 * @param longitude - Longitude (-180 to 180)
 * @param language - Language code for address (default: 'ru')
 * @returns Address components
 *
 * @example
 * ```ts
 * const address = await reverseGeocode(41.311081, 69.240562);
 * console.log(address.street); // "ул. Алишера Навои"
 * console.log(address.house); // "12"
 * ```
 */
export async function reverseGeocode(
  latitude: number,
  longitude: number,
  language: 'ru' | 'uz' | 'en' = 'ru'
): Promise<ReverseGeocodeResult> {
  // ═══════════════════════════════════════════════════════════════════
  // Validate inputs
  // ═══════════════════════════════════════════════════════════════════

  if (latitude < -90 || latitude > 90) {
    throw new Error('Latitude must be between -90 and 90');
  }

  if (longitude < -180 || longitude > 180) {
    throw new Error('Longitude must be between -180 and 180');
  }

  // ═══════════════════════════════════════════════════════════════════
  // Build request URL
  // ═══════════════════════════════════════════════════════════════════

  const params = new URLSearchParams({
    format: 'json',
    lat: latitude.toString(),
    lon: longitude.toString(),
    'accept-language': language,
    addressdetails: '1',
  });

  const url = `${NOMINATIM_API_URL}?${params}`;

  // ═══════════════════════════════════════════════════════════════════
  // Make request with timeout
  // ═══════════════════════════════════════════════════════════════════

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

  try {
    const response = await fetch(url, {
      headers: {
        'User-Agent': USER_AGENT,
      },
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      throw new Error(`Nominatim API error: ${response.status}`);
    }

    const data: NominatimResponse = await response.json();

    // ═══════════════════════════════════════════════════════════════════
    // Parse address components
    // ═══════════════════════════════════════════════════════════════════

    const addr = data.address;

    return {
      street: addr.road || addr.suburb || '',
      house: addr.house_number || '',
      city: addr.city || addr.state || '',
      country: addr.country || '',
      fullAddress: data.display_name,
    };
  } catch (error) {
    if (error instanceof Error) {
      if (error.name === 'AbortError') {
        throw new Error('Превышено время ожидания при определении адреса');
      }
      throw new Error(`Ошибка геокодирования: ${error.message}`);
    }
    throw new Error('Неизвестная ошибка при определении адреса');
  } finally {
    clearTimeout(timeoutId);
  }
}

// ═══════════════════════════════════════════════════════════════════
// Helper: Format Address for Display
// ═══════════════════════════════════════════════════════════════════

/**
 * Format address components into a readable string
 *
 * @example
 * ```ts
 * formatAddress({ street: 'ул. Навои', house: '12', city: 'Ташкент' })
 * // Returns: "ул. Навои, 12, Ташкент"
 * ```
 */
export function formatAddress(address: Partial<ReverseGeocodeResult>): string {
  const parts: string[] = [];

  if (address.street) parts.push(address.street);
  if (address.house) parts.push(address.house);
  if (address.city) parts.push(address.city);

  return parts.join(', ');
}

// ═══════════════════════════════════════════════════════════════════
// Export Types
// ═══════════════════════════════════════════════════════════════════

export type { NominatimAddress, NominatimResponse };
