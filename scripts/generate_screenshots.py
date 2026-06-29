# generate_screenshots.py
import os
import sys
import time
import subprocess
from playwright.sync_api import sync_playwright

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

# Import environment prep to seed test DB
from prepare_test_environment import main as prepare_env

def run_screenshot_flow():
    refresh = os.environ.get("REFRESH_TUTORIAL") == "1" or "--refresh" in sys.argv
    if refresh:
        print("Preparing test environment database and copying pictures...")
        prepare_env()
    else:
        print("Skipping test environment database and picture preparation (run with --refresh or REFRESH_TUTORIAL=1 to refresh).")
        
    print("Skipping Playwright screenshot generation (for now, as requested).")
    return

    gui_proc = None
    tuner_proc = None
    
    gui_log = open("gui_server.log", "w", encoding="utf-8")
    tuner_log = open("tuner_server.log", "w", encoding="utf-8")
    
    try:
        print("Starting TagPup GUI on port 8092...")
        gui_proc = subprocess.Popen(
            [sys.executable, "tagpup_gui.py", "test_photo_index.db"],
            env=dict(os.environ, TAGPUP_RELOADED="1"),
            stdout=gui_log,
            stderr=gui_log
        )
        
        print("Starting TagTuner on port 8081...")
        tuner_proc = subprocess.Popen(
            [sys.executable, "tagtuner.py", "test_photo_index.db"],
            env=dict(os.environ, TAGTUNER_RELOADED="1"),
            stdout=tuner_log,
            stderr=tuner_log
        )
        
        print("Waiting for servers to initialize and detecting ports...")
        gui_port = 8090
        tuner_port = 8081
        
        for _ in range(30):
            time.sleep(0.5)
            # Check GUI port
            if os.path.exists("gui_server.log"):
                with open("gui_server.log", "r", encoding="utf-8") as f:
                    content = f.read()
                    if "TagPup server started on port" in content:
                        parts = content.split("TagPup server started on port ")
                        if len(parts) > 1:
                            try:
                                gui_port = int(parts[1].split()[0])
                            except Exception:
                                pass
            # Check Tuner port
            if os.path.exists("tuner_server.log"):
                with open("tuner_server.log", "r", encoding="utf-8") as f:
                    content = f.read()
                    if "TagTuner server started on port" in content:
                        parts = content.split("TagTuner server started on port ")
                        if len(parts) > 1:
                            try:
                                tuner_port = int(parts[1].split()[0])
                            except Exception:
                                pass
        
        print(f"Detected TagPup GUI port: {gui_port}")
        print(f"Detected TagTuner port: {tuner_port}")
        time.sleep(1)
        
        with sync_playwright() as p:
            print("Launching headless Chromium...")
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            
            # --- 1. TagPup GUI Main Workspace Screenshot ---
            # Navigate to the test photo directory New folder in TagPup GUI
            print("Navigating to TagPup GUI...")
            page.goto(f"http://localhost:{gui_port}/?path=c:/src/kidzi/GitHub/TagPup/data/test_photos/New")
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
            page.click("#btn-manage-taxonomy")
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
            page.goto(f"http://localhost:{tuner_port}/?mode=folder-match&photo=c:/src/kidzi/GitHub/TagPup/data/test_photos/New/puppy2.png&show_matched=true")
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
            print("\nSuccessfully regenerated all 4 screenshots for TUTORIAL.md!")
            
    except Exception as e:
        print(f"Error during flow: {e}", file=sys.stderr)
        print("\n=== GUI SERVER LOG ===")
        try:
            if os.path.exists("gui_server.log"):
                with open("gui_server.log", "r", encoding="utf-8") as f:
                    print(f.read())
        except Exception as log_err:
            print(f"Could not read GUI log: {log_err}")
        print("\n=== TUNER SERVER LOG ===")
        try:
            if os.path.exists("tuner_server.log"):
                with open("tuner_server.log", "r", encoding="utf-8") as f:
                    print(f.read())
        except Exception as log_err:
            print(f"Could not read Tuner log: {log_err}")
    finally:
        print("Cleaning up backend servers...")
        for proc in [gui_proc, tuner_proc]:
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        print("Done.")

if __name__ == "__main__":
    run_screenshot_flow()
