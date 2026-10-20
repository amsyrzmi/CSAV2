import assert from "node:assert/strict";
import test from "node:test";
import { assertReleaseOrigin } from "../scripts/release-runtime.mjs";

test("release accepts only its baked private origin at runtime", () => {
  assertReleaseOrigin("http://gateway:8080", "http://gateway:8080/");
  for (const runtime of [
    undefined, "", "http://other:8080", "https://gateway:8080",
    "http://user:password@gateway:8080", "http://gateway:8080/path",
    "http://gateway:8080?query=1", "http://gateway:8080#fragment", "file:///tmp",
  ]) {
    assert.throws(() => assertReleaseOrigin("http://gateway:8080", runtime));
  }
});
