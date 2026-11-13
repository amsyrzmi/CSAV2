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
const {
  planPieces, uploadVideo, resumeVideo, UploadStopped, stamp, secondsFromQuery, currentLine, sha256Hex,
  usableDuration, MAX_VIDEO_SECONDS, MAX_ACTIVE_PIECES, BACKOFF_MS, chooseSeek, keepRestore, clampSeek,
  uploadEnvironmentProblem, SECURE_CONTEXT_TEXT,
} = await import(moduleUrl(
  (await readFile(new URL("../lib/training-video.ts", import.meta.url), "utf8")).replace(/import type [^;]+;/g, ""),
));

const MIB = 1024 * 1024;
const PIECE = 8 * MIB;

/** A file that is only a size and a slice: no bytes are ever allocated. */
const fakeFile = (size) => ({ size, slice: (start, end) => ({ start, end, size: end - start }) });
const plan = (pieceCount) => ({ itemId: "item-1", pieceSize: PIECE, pieceCount });
const upload = (count, received = []) => ({
  item: { id: "item-1", status: "uploading" }, piece_size: PIECE, piece_count: count, received,
});
const DONE = { id: "item-1", status: "uploaded" };
const failure = (status, code, extra = {}) => Object.assign(new Error(code), { status, code, ...extra });

/** Deps that record every call; `put` and `complete` take scripted behaviour. */
function harness(count, { put, complete, status } = {}) {
  const log = { puts: [], sleeps: [], completes: 0, statuses: 0, active: 0, peak: 0 };
  const deps = {
    start: async () => upload(count),
    put: async (itemId, number, blob, sha) => {
      log.puts.push({ itemId, number, size: blob.size, start: blob.start, sha });
      log.active += 1;
      log.peak = Math.max(log.peak, log.active);
      try {
        await Promise.resolve();
        if (put) await put(number, log.puts.filter((p) => p.number === number).length);
      } finally {
        log.active -= 1;
      }
    },
    status: async () => {
      log.statuses += 1;
      return status ? status() : upload(count, []);
    },
    complete: async () => {
      log.completes += 1;
      if (complete) await complete(log.completes);
      return DONE;
    },
    sha256: async (blob) => `sha-${blob.start}`,
    sleep: async (ms) => { log.sleeps.push(ms); },
  };
  return { deps, log };
}

test("planPieces cuts the file into whole pieces and a remainder", () => {
  const pieces = planPieces(20 * MIB + 1, PIECE);
  assert.equal(pieces.length, 3);
  assert.deepEqual(pieces.map((p) => p.number), [1, 2, 3]);
  assert.deepEqual(pieces[0], { number: 1, start: 0, end: PIECE });
  assert.deepEqual(pieces[1], { number: 2, start: PIECE, end: 2 * PIECE });
  assert.deepEqual(pieces[2], { number: 3, start: 2 * PIECE, end: 20 * MIB + 1 });
  assert.equal(pieces[2].end - pieces[2].start, 4 * MIB + 1);
  assert.equal(planPieces(16 * MIB, PIECE).length, 2);
  assert.deepEqual(planPieces(1, PIECE), [{ number: 1, start: 0, end: 1 }]);
  assert.deepEqual(planPieces(0, PIECE), []);
});

test("every piece is sent once with its own hash, three at a time, then the upload is completed", async () => {
  const { deps, log } = harness(7);
  const seen = [];
  const item = await uploadVideo(fakeFile(6 * PIECE + 5), deps, (p) => seen.push(p));
  assert.equal(item, DONE);
  assert.deepEqual(log.puts.map((p) => p.number).sort((a, b) => a - b), [1, 2, 3, 4, 5, 6, 7]);
  for (const put of log.puts) {
    assert.equal(put.itemId, "item-1");
    assert.equal(put.sha, `sha-${put.start}`, "the hash is the piece's own");
    assert.equal(put.size, put.number === 7 ? 5 : PIECE, "all but the last piece are full");
  }
  assert.equal(MAX_ACTIVE_PIECES, 3);
  assert.equal(log.peak, 3);
  assert.equal(log.completes, 1);
  assert.deepEqual(seen.map((p) => p.done), [0, 1, 2, 3, 4, 5, 6, 7]);
  assert.ok(seen.every((p) => p.total === 7));
});

test("pieces the server already has are not sent again", async () => {
  const { deps, log } = harness(3);
  deps.start = async () => upload(3, [1, 3]);
  await uploadVideo(fakeFile(2 * PIECE + 9), deps, () => {});
  assert.deepEqual(log.puts.map((p) => p.number), [2]);
});

test("a failed piece is retried alone and the others are not sent again", async () => {
  const { deps, log } = harness(3, {
    put: (number, attempt) => { if (number === 2 && attempt === 1) throw failure(500, "training_storage_unavailable"); },
  });
  await uploadVideo(fakeFile(2 * PIECE + 9), deps, () => {});
  const count = (n) => log.puts.filter((p) => p.number === n).length;
  assert.deepEqual([count(1), count(2), count(3)], [1, 2, 1]);
  assert.deepEqual(log.sleeps, [1000]);
  assert.equal(log.completes, 1);
});

test("a network failure (no status) is retried like a server failure", async () => {
  const { deps, log } = harness(2, {
    put: (number, attempt) => { if (number === 1 && attempt === 1) throw new TypeError("Failed to fetch"); },
  });
  await uploadVideo(fakeFile(PIECE + 1), deps, () => {});
  assert.equal(log.puts.filter((p) => p.number === 1).length, 2);
  assert.deepEqual(log.sleeps, [1000]);
});

test("a corrupt piece (422) is sent again after the backoff", async () => {
  const { deps, log } = harness(2, {
    put: (number, attempt) => { if (number === 2 && attempt === 1) throw failure(422, "training_piece_corrupt"); },
  });
  await uploadVideo(fakeFile(PIECE + 1), deps, () => {});
  assert.equal(log.puts.filter((p) => p.number === 2).length, 2);
  assert.deepEqual(log.sleeps, [1000]);
});

test("503 training_busy waits Retry-After and is not counted as a failed attempt", async () => {
  const { deps, log } = harness(1, {
    put: (number, attempt) => { if (attempt <= 5) throw failure(503, "training_busy", { body: { retry_after: 2 } }); },
  });
  await uploadVideo(fakeFile(10), deps, () => {});
  assert.deepEqual(log.sleeps, [2000, 2000, 2000, 2000, 2000]);
  assert.equal(log.puts.length, 6, "five refusals for room and one success, more than the three retries");
  assert.equal(log.completes, 1);
});

test("a busy answer with no Retry-After waits two seconds", async () => {
  const { deps, log } = harness(1, {
    put: (number, attempt) => { if (attempt === 1) throw failure(503, "training_busy"); },
  });
  await uploadVideo(fakeFile(10), deps, () => {});
  assert.deepEqual(log.sleeps, [2000]);
});

test("three retries fail: UploadStopped names the missing pieces, and resume sends only those", async () => {
  assert.deepEqual([...BACKOFF_MS], [1000, 2000, 4000]);
  let broken = true;
  const { deps, log } = harness(3, {
    put: (number) => { if (number === 2 && broken) throw failure(503, "training_storage_unavailable"); },
    status: () => upload(3, [1, 3]),
  });
  const seen = [];
  const stopped = await uploadVideo(fakeFile(2 * PIECE + 9), deps, (p) => seen.push(p)).then(
    () => null, (error) => error,
  );
  assert.ok(stopped instanceof UploadStopped);
  assert.deepEqual(stopped.missing, [2]);
  assert.deepEqual(stopped.plan, plan(3));
  assert.equal(log.puts.filter((p) => p.number === 2).length, 4, "the try and three retries");
  assert.deepEqual(log.sleeps, [1000, 2000, 4000]);
  assert.equal(log.puts.filter((p) => p.number !== 2).length, 2, "the other pieces went once");
  assert.equal(log.completes, 0, "nothing is completed with a piece missing");

  broken = false;
  log.puts.length = 0;
  log.sleeps.length = 0;
  const item = await resumeVideo(fakeFile(2 * PIECE + 9), stopped.plan, deps, (p) => seen.push(p));
  assert.equal(item, DONE);
  assert.equal(log.statuses, 1, "resume asks what the server has");
  assert.deepEqual(log.puts.map((p) => p.number), [2]);
  assert.equal(log.completes, 1);
  assert.equal(seen.at(-1).done, 3);
});

test("after a piece is out of retries nothing new is started, and the unsent pieces are missing too", async () => {
  const { deps, log } = harness(9, {
    put: (number) => { if (number === 1) throw failure(500, "training_storage_unavailable"); },
  });
  const stopped = await uploadVideo(fakeFile(8 * PIECE + 1), deps, () => {}).then(() => null, (error) => error);
  assert.ok(stopped instanceof UploadStopped);
  const sent = new Set(log.puts.filter((p) => p.number !== 1).map((p) => p.number));
  assert.ok(stopped.missing.includes(1));
  assert.deepEqual(stopped.missing, [...new Set(stopped.missing)].sort((a, b) => a - b));
  for (let n = 1; n <= 9; n += 1) assert.equal(stopped.missing.includes(n), n === 1 || !sent.has(n), `piece ${n}`);
  assert.ok(stopped.missing.length > 1, "pieces that never started are not lost");
});

test("an upload that was removed stops at once with the server's own error", async () => {
  for (const [status, code] of [[409, "training_upload_closed"], [404, "training_not_found"], [403, "training_forbidden"]]) {
    const error = failure(status, code);
    const { deps, log } = harness(3, { put: (number) => { if (number === 2) throw error; } });
    const caught = await uploadVideo(fakeFile(2 * PIECE + 9), deps, () => {}).then(() => null, (cause) => cause);
    assert.equal(caught, error, code);
    assert.equal(log.sleeps.length, 0, `${code}: no retry`);
    assert.equal(log.completes, 0);
  }
});

test("complete's 409 training_piece_missing asks what is stored, sends those, and completes once more", async () => {
  const { deps, log } = harness(3, {
    complete: (n) => { if (n === 1) throw failure(409, "training_piece_missing"); },
    status: () => upload(3, [1]),
  });
  const item = await uploadVideo(fakeFile(2 * PIECE + 9), deps, () => {});
  assert.equal(item, DONE);
  assert.equal(log.completes, 2);
  assert.equal(log.statuses, 1);
  const numbers = log.puts.map((p) => p.number);
  assert.deepEqual(numbers.slice(-2).sort(), [2, 3]);
  assert.equal(numbers.length, 5, "three first, then the two the server said it lacked");
});

test("a second training_piece_missing does not loop: the person is asked to continue", async () => {
  const { deps, log } = harness(2, {
    complete: () => { throw failure(409, "training_piece_missing"); },
    status: () => upload(2, [1]),
  });
  const stopped = await uploadVideo(fakeFile(PIECE + 1), deps, () => {}).then(() => null, (error) => error);
  assert.ok(stopped instanceof UploadStopped);
  assert.equal(log.completes, 2, "one resume, no more");
  assert.deepEqual(stopped.missing, [2]);
});

test("a complete that gets no answer stops the upload with nothing missing, so Continue only completes", async () => {
  const { deps, log } = harness(1, {
    complete: (n) => { if (n === 1) throw new TypeError("Failed to fetch"); },
    status: () => upload(1, [1]),
  });
  const stopped = await uploadVideo(fakeFile(10), deps, () => {}).then(() => null, (error) => error);
  assert.ok(stopped instanceof UploadStopped);
  assert.deepEqual(stopped.missing, []);
  log.puts.length = 0;
  const item = await resumeVideo(fakeFile(10), stopped.plan, deps, () => {});
  assert.equal(item, DONE);
  assert.equal(log.puts.length, 0);
});

test("a complete refused for another reason is the server's error", async () => {
  const error = failure(409, "training_upload_closed");
  const { deps } = harness(1, { complete: () => { throw error; } });
  const caught = await uploadVideo(fakeFile(10), deps, () => {}).then(() => null, (cause) => cause);
  assert.equal(caught, error);
});

test("stamp reads m:ss, and h:mm:ss from an hour", () => {
  assert.equal(stamp(3_723_000), "1:02:03");
  assert.equal(stamp(195_000), "3:15");
  assert.equal(stamp(0), "0:00");
  assert.equal(stamp(999), "0:00");
  assert.equal(stamp(59_999), "0:59");
  assert.equal(stamp(3_600_000), "1:00:00");
  assert.equal(stamp(-5), "0:00");
  assert.equal(stamp(Number.NaN), "0:00");
});

test("secondsFromQuery takes a plain non-negative whole number of seconds and nothing else", () => {
  assert.equal(secondsFromQuery("?t=195"), 195);
  assert.equal(secondsFromQuery("t=195"), 195);
  assert.equal(secondsFromQuery("?client_id=abc&t=7"), 7);
  assert.equal(secondsFromQuery("?t=0"), 0);
  assert.equal(secondsFromQuery("?t=-1"), null);
  assert.equal(secondsFromQuery("?t=1e3"), null);
  assert.equal(secondsFromQuery("?t=1.5"), null);
  assert.equal(secondsFromQuery("?t=+5"), null);
  assert.equal(secondsFromQuery("?t="), null);
  assert.equal(secondsFromQuery("?t=abc"), null);
  assert.equal(secondsFromQuery("?t=99999999999999999999"), null);
  assert.equal(secondsFromQuery(""), null);
  assert.equal(secondsFromQuery("?other=3"), null);
});

test("currentLine is the last line that has started, and -1 before the first", () => {
  const lines = [{ start_ms: 1000 }, { start_ms: 4000 }, { start_ms: 4000 }, { start_ms: 9000 }];
  assert.equal(currentLine(lines, 0), -1);
  assert.equal(currentLine(lines, 0.999), -1);
  assert.equal(currentLine(lines, 1), 0);
  assert.equal(currentLine(lines, 3.9), 0);
  assert.equal(currentLine(lines, 4), 2, "of two lines that start together, the later one");
  assert.equal(currentLine(lines, 8.99), 2);
  assert.equal(currentLine(lines, 9), 3);
  assert.equal(currentLine(lines, 100000), 3);
  assert.equal(currentLine([], 5), -1);
  assert.equal(currentLine(lines, Number.NaN), -1);
});

test("a browser-recorded WebM has no length in its header: that is unknown, never a wrong number or a refusal", () => {
  assert.equal(MAX_VIDEO_SECONDS, 3600);
  assert.equal(usableDuration(Number.POSITIVE_INFINITY), null);
  assert.equal(usableDuration(Number.NaN), null);
  assert.equal(usableDuration(Number.NEGATIVE_INFINITY), null);
  assert.equal(usableDuration(0), null);
  assert.equal(usableDuration(-3), null);
  assert.equal(usableDuration(undefined), null);
  assert.equal(usableDuration(12.5), 12.5);
  assert.equal(usableDuration(3601), 3601);
});

test("sha256Hex is the lower-case hex SHA-256 of the piece, read through crypto.subtle", async () => {
  assert.equal(
    await sha256Hex(new Blob(["abc"])),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
  assert.equal(
    await sha256Hex(new Blob([])),
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  );
});

test("a link's ?t= wins over the position to restore, which wins over nothing", () => {
  const link = { time: 195, play: false };
  const back = { time: 0, play: false };
  assert.equal(chooseSeek(link, back), link, "a pending ?t= beats a 0 restore left by a reload");
  assert.equal(chooseSeek(null, back), back);
  assert.equal(chooseSeek(link, null), link);
  assert.equal(chooseSeek(null, null), null);
});

test("a second media error keeps the position the first one saved, not the 0 left by reloading", () => {
  const first = keepRestore(null, { time: 41.5, play: true });
  assert.deepEqual(first, { time: 41.5, play: true });
  assert.equal(keepRestore(first, { time: 0, play: false }), first);
});

test("a seek past the end stops at the end, and an unknown end is trusted", () => {
  assert.equal(clampSeek(195, 100), 100);
  assert.equal(clampSeek(5, 100), 5);
  assert.equal(clampSeek(195, Number.NaN), 195);
  assert.equal(clampSeek(195, Number.POSITIVE_INFINITY), 195);
});

test("an insecure page cannot hash pieces: it says so in a plain sentence before anything is sent", () => {
  assert.match(SECURE_CONTEXT_TEXT, /secure \(https\) connection/);
  assert.equal(
    SECURE_CONTEXT_TEXT,
    "Uploading a video needs a secure (https) connection. Open the console over https and try again.",
  );
  assert.equal(uploadEnvironmentProblem({ isSecureContext: true, subtle: {} }), null);
  assert.equal(uploadEnvironmentProblem({ isSecureContext: false, subtle: {} }), SECURE_CONTEXT_TEXT);
  assert.equal(uploadEnvironmentProblem({ isSecureContext: true, subtle: undefined }), SECURE_CONTEXT_TEXT);
  assert.equal(uploadEnvironmentProblem({}), SECURE_CONTEXT_TEXT);
});
