import { api, ApiError } from "./api";

/**
 * Pictures on QR methods, plans and products (docs/CONSOLE_API.md "Pictures"): the upload rules
 * the server enforces, checked here first so an obvious mistake never leaves the browser, and
 * the plain sentence for every refusal. The server stays the authority.
 */

/** The largest upload the server takes: 2 MiB. */
export const PICTURE_MAX_BYTES = 2097152;

/** `PictureOut`: the asset id and its version (the content hash, which changes on a replace). */
export interface Picture {
  id: string;
  version: string;
}

/** The limits in words, shown next to every picture control. */
export const PICTURE_LIMITS = "JPEG or PNG, up to 2 MB, 64 to 4096 pixels a side";

const TYPES = new Set(["image/jpeg", "image/png"]);
const EXTENSION = /\.(jpe?g|png)$/i;

/**
 * The read route's address. A platform admin must name the company (`client_id`); `v` only
 * makes the browser fetch again after a replace (the route itself revalidates on its ETag).
 */
export function pictureUrl(tenantId: string | null, assetId: string, version?: string): string {
  const query = new URLSearchParams();
  if (tenantId) query.set("client_id", tenantId);
  if (version) query.set("v", version);
  // `toString()` rather than `query.size`: `size` is missing in older browsers, which would drop the query.
  const text = query.toString();
  return `/console/v1/media/${encodeURIComponent(assetId)}${text ? `?${text}` : ""}`;
}

const SENTENCES: Record<string, string> = {
  picture_empty: "That file is empty. Choose a different picture.",
  picture_too_large: "That picture is bigger than 2 MB. Choose a smaller one.",
  picture_type_unsupported:
    "Use a JPEG or PNG picture. Other kinds of file, including GIF and SVG, are not accepted.",
  picture_unreadable:
    "We could not read that picture. Check that it opens on your computer, or save it again as a JPEG or PNG.",
  picture_dimensions:
    "The picture must be 64 to 4096 pixels on each side, and its long side at most 10 times its short side.",
  picture_limit: "This company has reached its limit of 300 pictures. Remove one you no longer use first.",
  picture_kind_not_qr: "Only a DuitNow QR method can have a picture.",
  picture_name_invalid:
    "That file's name can't be used. Rename it with plain letters and numbers, then choose it again.",
};

const GENERIC = "The picture could not be saved. Check the file and try again.";

/** The sentence for a server code; any code not listed here (or none) gets one generic sentence. */
export function pictureErrorText(code: string | null | undefined): string {
  return code && Object.hasOwn(SENTENCES, code) ? SENTENCES[code] : GENERIC;
}

/**
 * The sentence for a failed upload or removal. A 413 is the upload parser's own refusal (it has
 * no picture code), so it reads as too large; every other unknown failure, including Starlette's
 * uncoded 422, reads as the generic sentence and never as the server's raw text.
 */
export function pictureFailure(cause: unknown): string {
  if (!(cause instanceof ApiError)) return GENERIC;
  if (cause.status === 413) return SENTENCES.picture_too_large;
  return pictureErrorText(cause.code);
}

/** A sentence when the file will certainly be refused (wrong kind, empty, over 2 MiB), else null. */
export function pictureProblem(file: File): string | null {
  if (!TYPES.has(file.type) || !EXTENSION.test(file.name)) return SENTENCES.picture_type_unsupported;
  if (file.size === 0) return SENTENCES.picture_empty;
  if (file.size > PICTURE_MAX_BYTES) return SENTENCES.picture_too_large;
  return null;
}

/** Sets or replaces the picture at `path` (a `.../picture` route): the file alone, as the part `file`. */
export function putPicture(path: string, file: File): Promise<Picture> {
  const form = new FormData();
  form.append("file", file);
  return api<Picture>(path, { method: "PUT", body: form });
}

/** Removes the picture at `path`; answers the same when there was none. */
export function removePicture(path: string): Promise<void> {
  return api<void>(path, { method: "DELETE" });
}
