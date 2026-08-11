#!/usr/bin/env python3
"""Record the README demo clips by driving the real UI in demo mode.

Optional developer tooling, not part of the app or the test suite: it needs
`playwright` (plus `python3 -m playwright install chromium`) and `ffmpeg`,
neither of which OurCal itself depends on. Nothing here is imported by
ourcal.py, so the project's single-file / standard-library-only constraint
is unaffected.

The point of scripting it rather than screen-recording by hand: a demo of a
calendar app is a demo of somebody's calendar. This runs against a COPY of
ourcal.py in an empty temporary directory, so the app resolves its data dir
there and finds no accounts.json, no credentials.json and no token files.
Every account, address and event on screen is a built-in fixture. Re-record
after a UI change and it stays that way; a hand-recorded clip does not.

    python3 packaging/record-demo.py            # write docs/demo/
    python3 packaging/record-demo.py --check    # screenshots only, no video

Produces, per viewport, a .mp4 (full quality, linked from the README) and a
.gif (inline in the README — GitHub autoplays a GIF but may sanitise a
<video> tag).
"""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESKTOP = {"width": 1280, "height": 860}
PHONE = {"width": 390, "height": 844}
# Trailing frames the browser emits while the recording context closes; they
# are black, and left in they read as the video having broken at the end.
TAIL_TRIM_SECONDS = 2.2


def settle(page, ms=900):
    page.wait_for_timeout(ms)


def desktop_tour(page, url, shot=None):
    """The Mac walkthrough: agenda, range, filter, create, sync, edit, delete."""
    page.goto(url)
    page.wait_for_selector(".ev", timeout=15000)
    settle(page, 1600)
    snap(page, shot, "01-agenda")

    page.select_option("#rangeSel", "90")
    settle(page, 1400)
    snap(page, shot, "02-range")

    chips = page.locator("#chips .chip")
    if chips.count() > 1:
        chips.nth(1).click()
        settle(page, 1100)
        snap(page, shot, "03-filter")
        chips.nth(1).click()
        settle(page, 700)

    page.click("#newBtn")
    page.wait_for_selector("#modal.open", timeout=5000)
    settle(page, 600)
    page.fill("#f-title", "Design review")
    page.fill("#f-start", "14:00")
    page.fill("#f-end", "15:00")
    page.fill("#f-loc", "Meet")
    settle(page, 900)
    snap(page, shot, "04-new-event")
    page.click("#createBtn")
    settle(page, 2000)
    snap(page, shot, "05-created")

    row = page.locator(".ev").first
    row.hover()
    settle(page, 500)
    row.locator('button[data-act="sync"]').click()
    page.wait_for_selector("#modal.open", timeout=5000)
    settle(page, 1500)
    snap(page, shot, "06-sync")
    page.click("#cancelBtn")
    settle(page, 700)

    row.hover()
    settle(page, 400)
    edit = row.locator('button[data-act="edit"]')
    if edit.is_enabled():
        edit.click()
        page.wait_for_selector("#editModal.open", timeout=5000)
        settle(page, 1500)
        snap(page, shot, "07-edit")
        page.click("#editCancelBtn")
        settle(page, 700)

    # Delete shows the blast radius across accounts, then backs out — the
    # store is in-memory, but a demo that deletes something teaches the wrong
    # reflex to anyone following along on their own calendar.
    row.hover()
    settle(page, 400)
    row.locator('button[data-act="del"]').click()
    page.wait_for_selector("#delModal.open", timeout=5000)
    settle(page, 1800)
    snap(page, shot, "08-delete")
    page.click("#delCancelBtn")
    settle(page, 1400)
    snap(page, shot, "09-end")


def phone_tour(page, url, shot=None):
    """The narrow-viewport walkthrough: agenda, setup page, accounts, bundle."""
    page.goto(url)
    page.wait_for_selector("#agenda", timeout=15000)
    settle(page, 2000)
    snap(page, shot, "p1-agenda")

    page.mouse.wheel(0, 500)
    settle(page, 1400)
    snap(page, shot, "p2-scrolled")
    page.mouse.wheel(0, -500)
    settle(page, 800)

    page.click(".setup-link")
    page.wait_for_selector("#accts", timeout=10000)
    # Hidden for the recording only. Under this harness the footer reads
    # "android branch: not active" and shows a temp path — both true of
    # Chromium at phone width, and both misleading beside a caption about
    # phones. Hiding it beats showing a diagnosis of the harness.
    page.add_style_tag(content="#diag{display:none !important}")
    settle(page, 1600)
    snap(page, shot, "p3-setup")

    page.fill("#a-label", "Work")
    settle(page, 500)
    page.fill("#a-email", "you@work.example.com")
    settle(page, 1400)
    snap(page, shot, "p4-add-account")

    page.mouse.wheel(0, 600)
    settle(page, 2200)
    snap(page, shot, "p5-bundle")
    page.mouse.wheel(0, -1200)
    settle(page, 1500)
    snap(page, shot, "p6-end")


def snap(page, shot, name):
    if shot:
        page.screenshot(path=str(shot / f"{name}.png"))


def need(tool):
    if shutil.which(tool) is None:
        sys.exit(f"{tool} not found — required to convert the recordings")


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def convert(webm, out_dir, name, gif_width):
    mp4 = out_dir / f"ourcal-{name}.mp4"
    gif = out_dir / f"ourcal-{name}.gif"
    keep = max(1.0, duration(webm) - TAIL_TRIM_SECONDS)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-t", f"{keep:.2f}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24",
         "-movflags", "+faststart", str(mp4)], check=True)
    palette = (f"fps=10,scale={gif_width}:-1:flags=lanczos,split[s0][s1];"
               "[s0]palettegen=max_colors=128[p];[s1][p]paletteuse="
               "dither=bayer:bayer_scale=3")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4),
         "-vf", palette, str(gif)], check=True)
    return mp4, gif


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="screenshots only — validates the tours without recording")
    ap.add_argument("--out", default=str(ROOT / "docs" / "demo"))
    args = ap.parse_args()

    if not args.check:
        need("ffmpeg")
        need("ffprobe")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright not installed — pip install playwright "
                 "&& python3 -m playwright install chromium")

    out_dir = pathlib.Path(args.out)
    if not args.check:
        out_dir.mkdir(parents=True, exist_ok=True)
    check_dir = pathlib.Path(tempfile.mkdtemp(prefix="ourcal-check-"))

    # An empty directory holding nothing but a copy of the app: this is what
    # keeps the maintainer's real accounts and credentials out of the frame.
    work = pathlib.Path(tempfile.mkdtemp(prefix="ourcal-demo-"))
    shutil.copy2(ROOT / "ourcal.py", work / "ourcal.py")
    os.environ["OURCAL_DEMO"] = "1"
    os.chdir(work)
    sys.path.insert(0, str(work))
    import ourcal  # noqa: E402

    server, url = ourcal.start_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"serving from {work}")

    failed = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for name, size, tour, gif_width in (
                ("desktop", DESKTOP, desktop_tour, 720),
                ("phone", PHONE, phone_tour, 260),
            ):
                kwargs = {"viewport": size, "device_scale_factor": 2}
                shot = None
                if args.check:
                    # Into the temp workspace, not out_dir: --check is a
                    # developer smoke test and must not leave untracked
                    # screenshot directories inside docs/demo/.
                    shot = check_dir / name
                    shot.mkdir(parents=True, exist_ok=True)
                else:
                    kwargs["record_video_dir"] = str(work / f"vid-{name}")
                    kwargs["record_video_size"] = size
                ctx = browser.new_context(**kwargs)
                page = ctx.new_page()
                try:
                    tour(page, url, shot)
                except Exception as exc:
                    failed = True
                    print(f"  {name}: FAILED — {type(exc).__name__}: {exc}")
                    page.screenshot(path=str((check_dir if args.check else out_dir)
                                             / f"FAILURE-{name}.png"))
                ctx.close()
                if args.check:
                    print(f"  {name}: ok")
                elif not failed:
                    webm = next((work / f"vid-{name}").glob("*.webm"))
                    mp4, gif = convert(webm, out_dir, name, gif_width)
                    print(f"  {name}: {mp4.name} ({mp4.stat().st_size // 1024}K), "
                          f"{gif.name} ({gif.stat().st_size // 1024}K)")
            browser.close()
    finally:
        server.shutdown()
        shutil.rmtree(work, ignore_errors=True)

    print("wrote", check_dir if args.check else out_dir)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
