/**
 * Internal request context forwarded from `web/proxy.ts` into Server Components.
 *
 * This header is never an authentication source. The proxy strips any inbound client value
 * and sets it only after Supabase `getUser()` and the single-user allowlist have succeeded.
 * Layout consumes it only for presentation (nav email / signed-in links).
 */

export const VIEWER_USER_EMAIL_HEADER = "x-ai-books-viewer-user-email";

/** Clone `headers` without any client-supplied viewer context. */
export function sanitizedViewerHeaders(headers: Headers): Headers {
  const sanitized = new Headers(headers);
  sanitized.delete(VIEWER_USER_EMAIL_HEADER);
  return sanitized;
}

/** Clone `headers`, strip spoofed context, then attach the verified owner email. */
export function viewerHeadersWithEmail(
  headers: Headers,
  email: string | null | undefined,
): Headers {
  const nextHeaders = sanitizedViewerHeaders(headers);
  if (email) {
    nextHeaders.set(VIEWER_USER_EMAIL_HEADER, email);
  }
  return nextHeaders;
}

export function getViewerEmailFromHeaders(
  headers: Pick<Headers, "get">,
): string | null {
  return headers.get(VIEWER_USER_EMAIL_HEADER);
}
