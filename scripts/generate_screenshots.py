# generate_screenshots.py
import os
import socket
import sys
import time
import subprocess
from playwright.sync_api import sync_playwright

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

import _root  # noqa: E402,F401
from tagpup.core import processes  # noqa: E402

# Import environment prep to seed test DB
from prepare_test_environment import main as prepare_env

#: Ports of its own, so a run never answers the apps somebody has open.
TAGPUP_PORT, TUNER_PORT = 8092, 8081

def run_screenshot_flow():
    refresh = os.environ.get("REFRESH_TUTORIAL") == "1" or "--refresh" in sys.argv
    if refresh:
        print("Preparing test environment database and copying pictures...")
        prepare_env()
    else:
        print("Skipping test environment database and picture preparation (run with --refresh or REFRESH_TUTORIAL=1 to refresh).")
        
    print("Skipping Playwright screenshot generation (for now, as requested).")
    return

    server = None
    gui_port, tuner_port = TAGPUP_PORT, TUNER_PORT

    try:
        # One server for both pages (tagpup_web.py), logging where the apps log
        # (data/logs), not into whatever folder this was run from.
        print(f"Starting the server: TagPup on port {gui_port}, TagTuner on port {tuner_port}...")
        server = processes.start(
            [sys.executable, os.path.join(PROJECT_ROOT, "tagpup_web.py"), "--db", "test_photo_index",
             "--tagpup-port", str(gui_port), "--tuner-port", str(tuner_port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(60):
            try:
                with socket.create_connection(("127.0.0.1", tuner_port), timeout=1):
                    break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError("the server exited before it was ready") from None
                time.sleep(0.5)
        else:
            raise RuntimeError("the server never came up")
        time.sleep(1)

        with sync_playwright() as p:
            print("Launching headless Chromium...")
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            
            # --- 1. TagPup GUI Main Workspace Screenshot ---
            # Navigate to the test photo directory New folder in TagPup GUI
            print("Navigating to TagPup GUI...")
            page.goto(f"http://localhost:{gui_port}/?path=c:/src/kingersoll/GitHub/TagPup/data/test_photos/New")
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            
            # Select the puppy2.png photo from the sidebar list
            print("Selecting puppy2.png...")
            page.click("#photo-list .photo-item:not(.folder-header-item)")
            time.sleep(1)
            
            # Click the Suggest Tags button in the details panel
            print("Clicking Get AI Suggestions...")
            page.click("#btn-suggest-tags")
            
            # Wait for AI suggestions to populate (the section removes 'hidden' class)
            print("Waiting for suggestions to load...")
            page.wait_for_selector("#suggestions-section:not(.hidden)", timeout=60000)
            time.sleep(2)
            
            # Capture tagpup main screen
            os.makedirs(os.path.join(PROJECT_ROOT, "docs", "images"), exist_ok=True)
            tagpup_main_path = os.path.join(PROJECT_ROOT, "docs", "images", "tagpup_main_screen.png")
            page.screenshot(path=tagpup_main_path)
            print(f"Captured: {tagpup_main_path}")
            
            # --- 2. Taxonomy Manager Modal Tree Screenshot ---
            # Open the Tags Tree taxonomy manager
            print("Opening Taxonomy Tree Manager...")
            page.click("#btn-gear")
            page.click("#gear-menu [data-action='tag-editor']")
            page.wait_for_selector("#taxonomy-modal.active", timeout=5000)
            time.sleep(1.5)
            
            # Capture the taxonomy tree screenshot showing the new "Face Matching" toggle slider switch
            taxonomy_path = os.path.join(PROJECT_ROOT, "docs", "images", "taxonomy_manager.png")
            page.screenshot(path=taxonomy_path)
            print(f"Captured: {taxonomy_path}")
            
            # Close the Taxonomy Modal
            page.click("#btn-close-taxonomy")
            page.wait_for_selector("#taxonomy-modal:not(.active)", timeout=5000)
            time.sleep(0.5)
            
            # --- 3. Tag Resolution Prompt Screenshot ---
            # Fill the add tag field with a brand new category name
            print("Triggering New Tag Placement Resolution prompt...")
            page.fill("#input-add-tag", "Vacation")
            page.click("#btn-add-tag")
            
            # Wait for placement modal to open
            page.wait_for_selector(".modal-overlay.active h2", timeout=5000)
            time.sleep(1)
            
            # Capture the tag placement resolution dialog
            resolution_path = os.path.join(PROJECT_ROOT, "docs", "images", "tag_resolution_prompt.png")
            page.screenshot(path=resolution_path)
            print(f"Captured: {resolution_path}")
            
            # Cancel the modal
            page.click(".modal-overlay.active .btn-cancel")
            time.sleep(0.5)
            
            # Navigate to TagTuner with show_matched=true enabled and select puppy2.png
            print("Navigating to TagTuner...")
            page.goto(f"http://localhost:{tuner_port}/?mode=folder-match&photo=c:/src/kingersoll/GitHub/TagPup/data/test_photos/New/puppy2.png&show_matched=true")
            page.wait_for_load_state("networkidle")
            
            # Wait for the unmatched puppy face card to render and click it to open edit details
            print("Waiting for unmatched face card in grid...")
            page.wait_for_selector("#faces-grid .face-card", timeout=10000)
            print("Selecting face card to show suggestion options...")
            page.click("#faces-grid .face-card")
            
            # Click the "Puppy" suggestion pill to match it
            print("Waiting for 'Puppy' suggestion pill...")
            page.wait_for_selector(".suggestion-pill:has-text('Puppy')", timeout=5000)
            print("Matching face to 'Puppy' via suggestion pill...")
            page.click(".suggestion-pill:has-text('Puppy')")
            
            # Wait for the match to save and the UI to reload
            time.sleep(2)
            
            # Click the now-matched face card to select it and show the crop overlay on the main image details panel
            print("Selecting matched face card to show bounding box details...")
            page.click("#faces-grid .face-card")
            time.sleep(1.5)
            
            # Capture clean workspace screenshot showing face overlay highlighted on the main image
            tuner_path = os.path.join(PROJECT_ROOT, "docs", "images", "tagtuner_workspace.png")
            page.screenshot(path=tuner_path)
            print(f"Captured: {tuner_path}")
            
            browser.close()
            print("\nSuccessfully regenerated all 4 screenshots for docs/TUTORIAL.md!")
            
    except Exception as e:
        print(f"Error during flow: {e}; the server's log is in data/logs/tagpup_web.log", file=sys.stderr)
    finally:
        print("Cleaning up the server...")
        if server:
            try:
                server.terminate()
                server.wait(timeout=2)
            except Exception:
                try:
                    server.kill()
                except Exception:
                    pass
        print("Done.")

if __name__ == "__main__":
    run_screenshot_flow()
