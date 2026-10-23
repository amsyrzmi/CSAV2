"""Real browser voice loop with synthetic microphone, speech and HTTP only."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "CSA_WebConsole/csa-console/web"


@pytest.mark.asyncio
async def test_handsfree_two_turns_recovery_and_stop(tmp_path: Path) -> None:
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
import { useState } from "react";
import { VoiceConversation } from "@/components/console/VoiceConversation";
export default function Harness() {
 const [busy,setBusy]=useState(false); const [draft,setDraft]=useState("");
 return <><VoiceConversation conversation="synthetic" query="" disabled={false}
 onBusy={setBusy} onDraft={setDraft}/><p data-testid="busy">{String(busy)}</p><p>{draft}</p></>;
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
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.add_init_script("""
window.voiceTest = { microphones: 0, spoken: [], leaks: 0 };
Object.defineProperty(navigator,"mediaDevices",{value:{getUserMedia:async()=>{
 window.voiceTest.microphones++; let stopped=false;
 return {getTracks:()=>[{stop(){if(!stopped){stopped=true;window.voiceTest.microphones--;}}}]};
}}});
window.AudioContext=class {
 async resume(){} async close(){}
 createMediaStreamSource(){return {connect(){}};}
 createAnalyser(){let frame=0;return {fftSize:2048,
 getFloatTimeDomainData(data){data.fill(frame++<4 ? 0.1 : 0);}};}
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
 {localService:true,lang:"en-MY"},{localService:true,lang:"ms-MY"}],cancel(){},
 speak(value){if(window.voiceTest.microphones)window.voiceTest.leaks++;
 window.voiceTest.spoken.push(value.text);setTimeout(()=>value.onend?.(),10);}
}});
""")
            jobs: dict[str, dict[str, Any]] = {}
            turns: dict[str, dict[str, Any]] = {}
            attempts: list[str] = []
            lost = False

            async def route(request_route: Any) -> None:
                nonlocal lost
                request = request_route.request
                path = request.url.split("/console/v1/")[1].split("?")[0]
                if path == "auth/csrf":
                    data: Any = {"csrf_token": "synthetic-csrf"}
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
                        key = body["request_key"]
                        attempts.append(key)
                        turns[key] = {
                            "request_key": key,
                            "status": "available",
                            "replies": [{"id": key, "body": "Kami buka pada pukul sembilan pagi."}],
                        }
                        if not lost:
                            lost = True
                            await request_route.abort()
                            return
                        data = {"duplicate": attempts.count(key) > 1}
                    else:
                        data = {"items": list(turns.values()), "next_cursor": None}
                else:
                    raise AssertionError(path)
                await request_route.fulfill(json=data)

            await page.route("**/console/v1/**", route)
            await page.goto(f"http://127.0.0.1:{port}/login")
            await page.get_by_role("combobox").select_option("ms")
            await page.get_by_role("checkbox").check()
            await page.get_by_role("button", name="Start voice chat").click()
            await page.get_by_role("button", name="Resume same request").wait_for(timeout=30000)
            assert len(turns) == 1 and len(attempts) == 1
            await page.get_by_role("button", name="Resume same request").click()
            await page.wait_for_function("window.voiceTest.spoken.length >= 2", timeout=30000)
            await page.get_by_role("button", name="Stop voice chat / mute").click()
            await page.wait_for_function("window.voiceTest.microphones === 0")
            assert len(turns) == 2
            assert attempts[0] == attempts[1], "lost acknowledgement resumes the same key"
            assert await page.evaluate("window.voiceTest.leaks") == 0
            assert await page.get_by_test_id("busy").inner_text() == "false"
            count = len(attempts)
            await page.wait_for_timeout(1800)
            assert len(attempts) == count, "Stop prevents the next automatic submission"
            await browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
