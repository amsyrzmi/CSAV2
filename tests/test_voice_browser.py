"""Real browser voice loop with synthetic microphone, speech and HTTP only."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "CSA_WebConsole/csa-console/web"


@pytest.mark.asyncio
async def test_handsfree_two_turns_recovery_and_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = shutil.which("node")
    assert node, "Node is required for browser voice acceptance"
    stage = tmp_path / "web"
    environment = {
        "PATH": os.environ["PATH"],
        "NEXT_TELEMETRY_DISABLED": "1",
        "CSA_CONSOLE_API_ORIGIN": "http://127.0.0.1:1",
    }
    subprocess.run(
        [node, str(WEB / "scripts/check-isolated.mjs"), "--context", str(stage)],
        check=True,
        cwd=ROOT,
        env=environment,
        capture_output=True,
    )
    (stage / "node_modules").symlink_to(WEB / "node_modules", target_is_directory=True)
    # Test-only login page in a disposable source copy. Production routing/auth is unchanged.
    next((stage / "app").glob("**/login/page.tsx")).write_text(""""use client";
import { Suspense } from "react";
import { SandboxWorkspace } from "@/components/console/SandboxWorkspace";
import type { Me } from "@/lib/identity-contracts";
export default function Harness() {
 return <Suspense><SandboxWorkspace me={{role:"tenant_staff",
 active_client_id:"synthetic-tenant"} as Me}/></Suspense>;
}""")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = (tmp_path / "frontend.log").open("w")
    process = subprocess.Popen(
        [
            node,
            str(WEB / "node_modules/next/dist/bin/next"),
            "dev",
            "--webpack",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=stage,
        env=environment,
        stdout=log,
        stderr=log,
    )
    try:
        async with httpx.AsyncClient(trust_env=False) as http:
            for _ in range(120):
                assert process.poll() is None, (tmp_path / "frontend.log").read_text()
                try:
                    if (
                        await http.get(f"http://127.0.0.1:{port}/login", timeout=2)
                    ).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            else:
                pytest.fail("Synthetic voice frontend did not start")
        monkeypatch.setenv("DEBUG", "pw:browser")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                for scenario in ("lost_ack", "stop_ack", "timeout", "discard"):
                    await exercise_sandbox(browser, port, scenario, tmp_path)
            finally:
                await browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


async def exercise_sandbox(browser: Any, port: int, scenario: str, tmp_path: Path) -> None:
    context = await browser.new_context()
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()
    diagnostics: list[str] = []
    page.on("crash", lambda: diagnostics.append("Chromium page crashed"))
    page.on("pageerror", lambda error: diagnostics.append(str(error)))
    page.on("console", lambda message: diagnostics.append(f"{message.type}: {message.text}"))
    await page.add_init_script("""
window.voiceTest = { microphones: 0, spoken: [], speakingWithMic: 0,
 interruptions: 0, captures: 0, playback: null };
Object.defineProperty(navigator,"mediaDevices",{value:{getUserMedia:async()=>{
 window.voiceTest.captures++;
 window.voiceTest.microphones++; let stopped=false;
 return {getTracks:()=>[{stop(){if(!stopped){stopped=true;window.voiceTest.microphones--;}}}]};
}}});
window.AudioContext=class {
 async resume(){} async close(){}
 createMediaStreamSource(){return {connect(){}};}
 createAnalyser(){let frame=0;return {fftSize:2048,
 getFloatTimeDomainData(data){data.fill(window.voiceTest.captures<=2 && frame++<4 ? 0.1 : 0);}};}
};
window.MediaRecorder=class {
 static isTypeSupported(){return true;}
 constructor(stream){this.stream=stream;this.state="inactive";}
 start(){this.state="recording";}
 stop(){this.state="inactive";queueMicrotask(()=>{
 this.ondataavailable?.({data:new Blob(["synthetic"])});this.onstop?.();});}
};
window.SpeechSynthesisUtterance=class {constructor(text){this.text=text;}};
Object.defineProperty(window,"speechSynthesis",{value:{getVoices:()=>[
 {localService:true,lang:"en-MY"},{localService:true,lang:"ms-MY"}],cancel(){
 if(window.voiceTest.playback){clearTimeout(window.voiceTest.playback);
 window.voiceTest.playback=null;window.voiceTest.interruptions++;}},
 speak(value){if(window.voiceTest.microphones)window.voiceTest.speakingWithMic++;
 window.voiceTest.spoken.push(value.text);window.voiceTest.playback=setTimeout(()=>{
 window.voiceTest.playback=null;value.onend?.();},800);}
}});
""")
    jobs: dict[str, dict[str, Any]] = {}
    turns: dict[str, dict[str, Any]] = {}
    attempts: list[str] = []
    accepted, release = asyncio.Event(), asyncio.Event()
    history_reads = 0

    async def route(request_route: Any) -> None:
        nonlocal history_reads
        request = request_route.request
        path = request.url.split("/console/v1/")[1].split("?")[0]
        if path == "auth/csrf":
            data: Any = {"csrf_token": "synthetic-csrf"}
        elif path == "chat/conversations":
            data = {
                "items": [
                    {
                        "id": "synthetic",
                        "sequence": 1,
                        "created_at": "2026-10-24T00:00:00Z",
                        "archived_at": None,
                    }
                ],
                "next_cursor": None,
            }
        elif path == "chat/voice":
            data = {"enabled": True}
        elif "/voice/" in path:
            key = path.rsplit("/", 1)[1]
            if request.method == "POST":
                jobs[key] = {
                    "status": "completed",
                    "transcript": "Apakah waktu operasi?",
                    "error_code": None,
                }
            if key not in jobs:
                await request_route.fulfill(status=404, json={"detail": "not found"})
                return
            data = jobs[key]
        elif path.endswith("/turns"):
            if request.method == "POST":
                body = request.post_data_json
                assert set(body) == {"request_key", "body"}
                key = body["request_key"]
                attempts.append(key)
                turns.setdefault(
                    key,
                    {
                        "id": len(turns) + 1,
                        "request_key": key,
                        "body": body["body"],
                        "created_at": "2026-10-24T00:00:00Z",
                        "status": "available",
                        "replies": [{"id": key, "body": "Kami buka pada pukul sembilan pagi."}],
                    },
                )
                if len(attempts) == 1:
                    accepted.set()
                    if scenario in {"stop_ack", "timeout"}:
                        await release.wait()
                    await request_route.abort()
                    return
                data = {"duplicate": attempts.count(key) > 1}
            else:
                history_reads += 1
                data = {"items": list(turns.values()), "next_cursor": None}
        else:
            raise AssertionError(path)
        await request_route.fulfill(json=data)

    try:
        await page.route("**/console/v1/**", route)
        await page.goto(f"http://127.0.0.1:{port}/login")
        await page.get_by_label("Conversation", exact=True).select_option("synthetic")
        await page.get_by_label("Text question", exact=False).fill("Preserved typed draft")
        voice = page.get_by_role("region", name="Hands-free voice chat")
        await voice.get_by_role("combobox").select_option("ms")
        await voice.get_by_role("checkbox").check()
        await voice.get_by_role("button", name="Start voice chat").click()
        await asyncio.wait_for(accepted.wait(), timeout=30)
        if scenario == "stop_ack":
            await voice.get_by_role("button", name="Stop voice chat / mute").click()
            release.set()
        await voice.get_by_role("button", name="Resume same request").wait_for(timeout=30000)
        release.set()
        assert len(turns) == 1 and len(attempts) == 1
        # Background polling must not unlock a voice request even after acceptance.
        previous_reads = history_reads
        for _ in range(60):
            if history_reads > previous_reads:
                break
            await asyncio.sleep(0.1)
        assert history_reads > previous_reads
        await expect(
            page.get_by_role("button", name="Send to sandbox", exact=True)
        ).to_be_disabled()
        await expect(page.get_by_label("Conversation", exact=True)).to_be_disabled()
        await expect(page.get_by_label("Text question", exact=False)).to_have_value(
            "Apakah waktu operasi?"
        )
        if scenario == "discard":
            await voice.get_by_role("button", name="Discard local recovery").click()
            await expect(page.get_by_label("Text question", exact=False)).to_have_value(
                "Preserved typed draft"
            )
            await page.get_by_role("button", name="Send to sandbox", exact=True).click()
            await expect(page.get_by_label("Text question", exact=False)).to_have_value("")
            assert len(turns) == 2
            assert list(turns.values())[-1]["body"] == "Preserved typed draft"
            return
        await voice.get_by_role("button", name="Resume same request").click()
        await page.wait_for_function("window.voiceTest.spoken.length >= 2", timeout=30000)
        assert await page.evaluate("window.voiceTest.interruptions") >= 1
        await voice.get_by_role("button", name="Stop voice chat / mute").click()
        await page.wait_for_function("window.voiceTest.microphones === 0")
        assert len(turns) == 2
        assert attempts[0] == attempts[1], "uncertain acceptance resumes the same key"
        assert await page.evaluate("window.voiceTest.speakingWithMic") >= 1
        await expect(page.get_by_label("Text question", exact=False)).to_have_value(
            "Preserved typed draft"
        )
        await expect(page.get_by_role("button", name="Send to sandbox", exact=True)).to_be_enabled()
        count = len(attempts)
        await page.wait_for_timeout(1800)
        assert len(attempts) == count, "Stop prevents the next automatic submission"
    except Exception as exc:
        exc.add_note(f"Voice scenario: {scenario}; browser diagnostics: {diagnostics}")
        raise
    finally:
        release.set()
        (tmp_path / f"{scenario}-browser.log").write_text("\n".join(diagnostics))
        with contextlib.suppress(Exception):  # Preserve the original failure if Chromium crashed.
            await context.tracing.stop(path=tmp_path / f"{scenario}-trace.zip")
        await context.close()
