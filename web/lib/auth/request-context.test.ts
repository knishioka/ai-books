import { describe, expect, it } from "vitest";

import {
  getViewerEmailFromHeaders,
  sanitizedViewerHeaders,
  VIEWER_USER_EMAIL_HEADER,
  viewerHeadersWithEmail,
} from "./request-context";

describe("viewer request context", () => {
  it("strips client-supplied viewer email headers", () => {
    const headers = new Headers({
      accept: "text/html",
      [VIEWER_USER_EMAIL_HEADER]: "spoof@example.com",
    });

    const sanitized = sanitizedViewerHeaders(headers);

    expect(sanitized.get("accept")).toBe("text/html");
    expect(sanitized.get(VIEWER_USER_EMAIL_HEADER)).toBeNull();
  });

  it("sets only the verified email after sanitizing inbound headers", () => {
    const headers = new Headers({
      [VIEWER_USER_EMAIL_HEADER]: "spoof@example.com",
    });

    const nextHeaders = viewerHeadersWithEmail(headers, "owner@example.com");

    expect(getViewerEmailFromHeaders(nextHeaders)).toBe("owner@example.com");
  });

  it("does not attach a viewer email when the verified email is absent", () => {
    const headers = new Headers({
      [VIEWER_USER_EMAIL_HEADER]: "spoof@example.com",
    });

    const nextHeaders = viewerHeadersWithEmail(headers, null);

    expect(getViewerEmailFromHeaders(nextHeaders)).toBeNull();
  });
});
