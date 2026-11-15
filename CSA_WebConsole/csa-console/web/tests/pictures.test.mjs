import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const apiUrl = moduleUrl(await readFile(new URL("../lib/api.ts", import.meta.url), "utf8"));
const source = await readFile(new URL("../lib/pictures.ts", import.meta.url), "utf8");
const pictures = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));
const { ApiError } = await import(apiUrl);

const ASSET = "66666666-6666-4666-8666-666666666666";
const TENANT = "77777777-7777-4777-8777-777777777777";
const PATH = "/console/v1/payments/methods/aaaaaaaa-0000-4000-8000-000000000012/picture";

async function capture(run, reply = () => Response.json({ id: ASSET, version: "v1" })) {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    return reply(path, init);
  };
  try { return [await run(), requests]; } finally { globalThis.fetch = original; }
}
const bytes = (size, type, name) => new File([new Uint8Array(size)], name, { type });

test("pictureUrl names the asset, adds client_id for a platform admin and v only to bust the cache", () => {
  assert.equal(pictures.pictureUrl(null, ASSET), `/console/v1/media/${ASSET}`);
  assert.equal(pictures.pictureUrl(null, ASSET, "abc"), `/console/v1/media/${ASSET}?v=abc`);
  assert.equal(pictures.pictureUrl(TENANT, ASSET), `/console/v1/media/${ASSET}?client_id=${TENANT}`);
  assert.equal(
    pictures.pictureUrl(TENANT, ASSET, "a b"),
    `/console/v1/media/${ASSET}?client_id=${TENANT}&v=a+b`,
  );
});

test("pictureUrl pins its four shapes, also where URLSearchParams has no size property", () => {
  const original = Object.getOwnPropertyDescriptor(URLSearchParams.prototype, "size");
  // An older browser: `size` is undefined on every URLSearchParams.
  Object.defineProperty(URLSearchParams.prototype, "size", { get: () => undefined, configurable: true });
  try {
    assert.equal(new URLSearchParams("a=1").size, undefined);
    const base = `/console/v1/media/${ASSET}`;
    assert.equal(pictures.pictureUrl(null, ASSET), base);
    assert.equal(pictures.pictureUrl(TENANT, ASSET), `${base}?client_id=${TENANT}`);
    assert.equal(pictures.pictureUrl(null, ASSET, "abc"), `${base}?v=abc`);
    assert.equal(pictures.pictureUrl(TENANT, ASSET, "abc"), `${base}?client_id=${TENANT}&v=abc`);
  } finally {
    if (original) Object.defineProperty(URLSearchParams.prototype, "size", original);
  }
});

test("pictureProblem refuses what the server refuses before anything is sent", () => {
  assert.equal(pictures.PICTURE_MAX_BYTES, 2097152);
  assert.equal(pictures.pictureProblem(bytes(2097152, "image/jpeg", "qr.jpg")), null);
  assert.equal(pictures.pictureProblem(bytes(1000, "image/png", "qr.png")), null);
  assert.equal(pictures.pictureProblem(bytes(1000, "image/jpeg", "qr.jpeg")), null);
  assert.equal(pictures.pictureProblem(bytes(1000, "image/svg+xml", "qr.svg")), pictures.pictureErrorText("picture_type_unsupported"));
  assert.equal(pictures.pictureProblem(bytes(1000, "image/gif", "qr.gif")), pictures.pictureErrorText("picture_type_unsupported"));
  assert.equal(pictures.pictureProblem(bytes(1000, "image/png", "qr.txt")), pictures.pictureErrorText("picture_type_unsupported"));
  assert.equal(pictures.pictureProblem(bytes(2097153, "image/jpeg", "qr.jpg")), pictures.pictureErrorText("picture_too_large"));
  assert.equal(pictures.pictureProblem(bytes(0, "image/png", "qr.png")), pictures.pictureErrorText("picture_empty"));
});

test("putPicture sends PUT with the file as the only part, the CSRF header and no Content-Type", async () => {
  const file = bytes(10, "image/png", "qr.png");
  const [picture, requests] = await capture(() => pictures.putPicture(PATH, file));
  assert.deepEqual(picture, { id: ASSET, version: "v1" });
  assert.equal(requests.length, 1);
  const [path, init] = requests[0];
  assert.equal(path, PATH);
  assert.equal(init.method, "PUT");
  assert.ok(init.body instanceof FormData);
  assert.deepEqual([...init.body.keys()], ["file"]);
  assert.equal(init.body.get("file").name, "qr.png");
  assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
  assert.equal(init.headers.has("Content-Type"), false);
  assert.equal(init.credentials, "same-origin");
});

test("removePicture sends DELETE with the CSRF header and accepts the empty 204", async () => {
  const [result, requests] = await capture(
    () => pictures.removePicture(PATH), () => new Response(null, { status: 204 }),
  );
  assert.equal(result, undefined);
  const [path, init] = requests[0];
  assert.equal(path, PATH);
  assert.equal(init.method, "DELETE");
  assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
});

test("every picture code has its own plain sentence and nothing else shows raw text", () => {
  const known = [
    "picture_empty", "picture_too_large", "picture_type_unsupported", "picture_unreadable",
    "picture_dimensions", "picture_limit", "picture_kind_not_qr", "picture_name_invalid",
  ];
  const sentences = known.map((code) => pictures.pictureErrorText(code));
  assert.equal(new Set(sentences).size, known.length);
  const generic = pictures.pictureErrorText("something_new");
  assert.equal(generic, pictures.pictureErrorText(""));
  assert.equal(generic, pictures.pictureErrorText(undefined));
  for (const text of [...sentences, generic]) {
    assert.match(text, /^[A-Z].*[.]$/);
    assert.doesNotMatch(text, /picture_|_/);
  }
  assert.ok(!sentences.includes(generic));
  // Own keys only: an inherited name is not a code.
  assert.equal(pictures.pictureErrorText("constructor"), generic);
  assert.equal(pictures.pictureErrorText("toString"), generic);
});

test("pictureFailure reads the code, a 413 from the upload parser, and never raw server text", () => {
  const known = new ApiError(422, "picture_unreadable", "picture_unreadable");
  assert.equal(pictures.pictureFailure(known), pictures.pictureErrorText("picture_unreadable"));
  // The parser's own 413 has no picture code.
  const big = new ApiError(413, "file exceeds 2 MB", "file exceeds 2 MB");
  assert.equal(pictures.pictureFailure(big), pictures.pictureErrorText("picture_too_large"));
  // Starlette's uncoded 422 (a part with no file name) and a 5xx.
  const uncoded = new ApiError(422, "Too many fields", "Too many fields");
  assert.equal(pictures.pictureFailure(uncoded), pictures.pictureErrorText(""));
  assert.doesNotMatch(pictures.pictureFailure(uncoded), /Too many/);
  const down = new ApiError(502, "Something went wrong on our side. Try again.", "x");
  assert.equal(pictures.pictureFailure(down), pictures.pictureErrorText(""));
  assert.equal(pictures.pictureFailure(new Error("boom")), pictures.pictureErrorText(""));
  assert.equal(pictures.pictureFailure("boom"), pictures.pictureErrorText(""));
});

test("a refused upload surfaces as an ApiError carrying the server's code", async () => {
  const refuse = () => Response.json({ detail: "picture_dimensions" }, { status: 422 });
  const [caught] = await capture(
    () => pictures.putPicture(PATH, bytes(10, "image/png", "qr.png")).catch((cause) => cause), refuse,
  );
  assert.ok(caught instanceof ApiError);
  assert.equal(caught.status, 422);
  assert.equal(caught.code, "picture_dimensions");
  assert.equal(pictures.pictureFailure(caught), pictures.pictureErrorText("picture_dimensions"));
});
