import os
import subprocess
from datetime import datetime
from mutagen.id3 import ID3
import shutil
import xml.etree.ElementTree as ET
from xml.dom import minidom
import re
from pathlib import Path
from urllib.parse import quote

class EconomistPodcastMaster:
    """
    End-to-end Economist podcast processor (local):

    - Splits a downloaded Economist MP3 into chapter MP3s (ffmpeg copy)
    - Applies your custom chapter ordering
    - Skips chapters < 60s
    - Stores original in Archive/ (ignored by git)
    - Generates feed.xml for Overcast (GitHub Pages URLs)
    - Commits & pushes to GitHub (safe push by default)

    IMPORTANT:
    - This runs locally on your laptop. GitHub Actions is separate.
    """

    def __init__(self, base_folder: str, github_username: str, github_repo: str, branch: str = "main"):
        self.base_folder = os.path.abspath(base_folder)
        self.github_username = github_username
        self.github_repo = github_repo
        self.branch = branch

        self.archive_folder = os.path.join(self.base_folder, "Archive")
        os.makedirs(self.archive_folder, exist_ok=True)

        # GitHub Pages base + feed URL (this is what Overcast subscribes to)
        self.site_base = f"https://{github_username}.github.io/{github_repo}"
        self.feed_url = f"{self.site_base}/feed.xml"

        # Create/merge .gitignore so Archive isn't committed
        self.ensure_gitignore()

    # ---------------------------
    # Setup helpers
    # ---------------------------
    def ensure_gitignore(self):
        """
        Ensure Archive/ is ignored.
        Does NOT overwrite an existing .gitignore (your previous version overwrote every run).
        """
        gitignore_path = os.path.join(self.base_folder, ".gitignore")
        required_lines = [
            "# --- Economist podcast automation ---",
            "Archive/",
            "temp_original_*.mp3",
            "original*.mp3",
            "*.DS_Store",
        ]

        if os.path.exists(gitignore_path):
            existing = Path(gitignore_path).read_text(encoding="utf-8", errors="ignore").splitlines()
            existing_set = set(line.strip() for line in existing)
            to_add = [line for line in required_lines if line not in existing_set]
            if to_add:
                with open(gitignore_path, "a", encoding="utf-8") as f:
                    f.write("\n\n" + "\n".join(to_add) + "\n")
        else:
            with open(gitignore_path, "w", encoding="utf-8") as f:
                f.write("\n".join(required_lines) + "\n")

    # ---------------------------
    # Main workflow
    # ---------------------------
    def run_complete_workflow(self):
        print(f"""
{'='*70}
🎙️  ECONOMIST PODCAST AUTOMATION
{'='*70}

📂 Folder: {self.base_folder}
🌐 GitHub Pages base: {self.site_base}
📡 Feed URL (Overcast): {self.feed_url}

Starting workflow...
{'='*70}
""")

        # Step 0: Cleanup existing episodes if needed
        self.cleanup_existing_episodes()

        # Step 1: Find and process MP3 files in base folder (new downloads)
        mp3_files = self.find_new_mp3_files()

        if mp3_files:
            print(f"\n✓ Found {len(mp3_files)} MP3 file(s) to process\n")
            for mp3_file in mp3_files:
                self.split_economist_file(mp3_file)
        else:
            print("\n✓ No new MP3 files to process")

        # Step 2: Generate RSS feed from repo-visible episode folders
        self.generate_rss_feed()

        # Step 3: Git push
        self.git_push()

        print(f"""
{'='*70}
✅ COMPLETE! Your podcast is live (or updated)
{'='*70}

📡 Feed URL: {self.feed_url}
🎧 Overcast may cache—pull to refresh or re-add the feed URL if needed.

{'='*70}
""")

    # ---------------------------
    # Discovery / cleanup
    # ---------------------------
    def find_new_mp3_files(self):
        """
        Find MP3 files in the base folder that are NOT:
        - temp_* files
        - already inside an Economist_YYYY-MM-DD folder
        - inside Archive
        """
        mp3_files = []
        for file in os.listdir(self.base_folder):
            full_path = os.path.join(self.base_folder, file)
            if not (os.path.isfile(full_path) and file.lower().endswith(".mp3")):
                continue
            if file.startswith("temp_"):
                continue
            # ignore if user accidentally drops files in Archive
            if os.path.commonpath([full_path, self.archive_folder]) == self.archive_folder:
                continue
            mp3_files.append(full_path)
        return mp3_files

    def cleanup_existing_episodes(self):
        """
        Reprocess existing episodes to fix filenames if they contain old numbering patterns like:
        "01 - 002 The world..."
        """
        print(f"\n{'='*70}")
        print("🔧 Checking for episodes that need filename cleanup...")
        print(f"{'='*70}\n")

        episode_folders = []
        for item in os.listdir(self.base_folder):
            item_path = os.path.join(self.base_folder, item)
            if os.path.isdir(item_path) and item.startswith("Economist_"):
                episode_folders.append(item)

        if not episode_folders:
            print("  ✓ No existing episodes to check\n")
            return

        for folder in sorted(episode_folders):
            folder_path = os.path.join(self.base_folder, folder)

            needs_cleanup = False
            for file in os.listdir(folder_path):
                if file.endswith(".mp3") and re.search(r"\d{2}\s+-\s+\d{3}", file):
                    needs_cleanup = True
                    break

            if not needs_cleanup:
                print(f"  ✓ Already clean: {folder}")
                continue

            print(f"  🔧 Cleaning up: {folder}")

            date_part = folder.replace("Economist_", "")
            archive_original = os.path.join(self.archive_folder, f"original_{date_part}.mp3")

            if not os.path.exists(archive_original):
                print(f"  ⚠️  Cannot clean {folder} - original not found in Archive\n")
                continue

            # Move original back temporarily
            temp_original = os.path.join(self.base_folder, f"temp_original_{date_part}.mp3")
            shutil.copy2(archive_original, temp_original)

            # Delete old episode folder
            shutil.rmtree(folder_path)

            # Reprocess with clean filenames
            self.split_economist_file(temp_original)

            # Cleanup temp original if split moved it
            if os.path.exists(temp_original):
                try:
                    os.remove(temp_original)
                except OSError:
                    pass

            print(f"  ✅ Cleaned up: {folder}\n")

        print()

    # ---------------------------
    # Splitting logic
    # ---------------------------
    def split_economist_file(self, input_file: str):
        """
        Split MP3 file by chapters using ffmpeg.

        Folder naming:
        - If input filename contains YYYY-MM-DD, we use that date.
        - Else we use today's date.
        """
        try:
            input_basename = os.path.basename(input_file)

            # Try to detect a date in the filename
            m = re.search(r"(\d{4}-\d{2}-\d{2})", input_basename)
            date_str = m.group(1) if m else datetime.now().strftime("%Y-%m-%d")

            output_folder = os.path.join(self.base_folder, f"Economist_{date_str}")
            os.makedirs(output_folder, exist_ok=True)

            print(f"{'='*70}")
            print(f"🎧 Processing: {input_basename}")
            print(f"📁 Output: Economist_{date_str}")
            print(f"{'='*70}\n")

            file_size_mb = os.path.getsize(input_file) / (1024 * 1024)
            print(f"📊 File size: {file_size_mb:.1f} MB")

            # Move the input MP3 into the output folder as a temp original
            temp_file = os.path.join(output_folder, "original.mp3")
            print("📦 Moving file to output folder...")
            shutil.move(input_file, temp_file)

            print("🔍 Reading chapter information...")
            tags = ID3(temp_file)
            chapters = [tag for tag in tags.keys() if tag.startswith("CHAP")]

            if not chapters:
                print("⚠️  No chapters found - keeping original file as-is")
                final_name = os.path.join(output_folder, input_basename)
                shutil.move(temp_file, final_name)

                # Also archive that "original" copy (optional) — here we keep only the final in folder
                return

            print(f"✓ Found {len(chapters)} chapters\n")

            # Extract chapter info
            chapter_info = []
            for chap_id in chapters:
                chap = tags[chap_id]
                start_time = chap.start_time / 1000
                end_time = chap.end_time / 1000
                duration = end_time - start_time

                title = f"Chapter_{len(chapter_info) + 1}"
                if hasattr(chap, "sub_frames"):
                    for frame in chap.sub_frames.values():
                        if hasattr(frame, "text") and frame.text:
                            title = str(frame.text[0])
                            # keep basic safe filename characters (you can broaden if you want)
                            title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_", "&", "'", ",")).strip()
                            title = title[:80]
                            break

                chapter_info.append(
                    {"start_time": start_time, "duration": duration, "title": title}
                )

            # Your custom sorting logic
            def get_sort_priority(chapter):
                title_lower = chapter["title"].lower()
                duration = chapter["duration"]

                if "world this week" in title_lower:
                    return (1, 0)
                if "letter" in title_lower:
                    return (2, 0)
                if "business" in title_lower:
                    return (3, duration)
                if "finance" in title_lower or "economic" in title_lower:
                    return (4, duration)
                if "briefing" in title_lower:
                    return (6, 999999)
                return (5, duration)

            chapter_info.sort(key=get_sort_priority)

            print("📊 Custom sort order applied:")
            print("   1. The World This Week (first)")
            print("   2. Letters (second)")
            print("   3. Business (shortest first)")
            print("   4. Finance & Economics (shortest first)")
            print("   5. Everything Else (shortest first)")
            print("   6. Briefing (last)\n")

            chapter_files = []
            skipped_count = 0

            for i, chapter in enumerate(chapter_info, 1):
                if chapter["duration"] < 60:
                    print(f"  {i:02d}. {chapter['title']} ({chapter['duration']:.1f}s) ⏭️  SKIPPED (too short)")
                    skipped_count += 1
                    continue

                # Remove leading numbers from chapter title for clean filename
                clean_title = re.sub(r"^\d+\s+", "", chapter["title"]).strip()

                output_file = os.path.join(output_folder, f"{i:02d} - {clean_title}.mp3")

                cmd = [
                    "ffmpeg",
                    "-i", temp_file,
                    "-ss", str(chapter["start_time"]),
                    "-t", str(chapter["duration"]),
                    "-acodec", "copy",
                    "-loglevel", "error",
                    "-y", output_file
                ]

                subprocess.run(cmd, capture_output=True)

                if os.path.exists(output_file):
                    chapter_size_mb = os.path.getsize(output_file) / (1024 * 1024)
                    chapter_files.append(output_file)
                    print(f"  {i:02d}. {clean_title} ({chapter['duration']/60:.1f} min, {chapter_size_mb:.1f} MB) ✓")

            # Chapter list
            summary_file = os.path.join(output_folder, "00 - Chapter List.txt")
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write("The Economist Weekly Edition\n")
                f.write(f"Processed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 60 + "\n\n")
                for file in chapter_files:
                    f.write(f"{os.path.basename(file)}\n")

            # Archive original MP3 immediately (kept out of git)
            archive_original = os.path.join(self.archive_folder, f"original_{date_str}.mp3")
            shutil.move(temp_file, archive_original)
            print(f"\n📦 Moved original MP3 to Archive: original_{date_str}.mp3")
            print(f"💾 Original size: {file_size_mb:.1f} MB (excluded from GitHub)")

            print(f"\n✅ Created {len(chapter_files)} chapter files")
            if skipped_count:
                print(f"⏭️  Skipped {skipped_count} chapter(s) shorter than 1 minute")
            print()

        except Exception as e:
            print(f"\n❌ Error processing file: {e}")
            import traceback
            traceback.print_exc()

    # ---------------------------
    # RSS generation (FIXED)
    # ---------------------------
    def generate_rss_feed(self):
        """
        Generate feed.xml from all Economist_YYYY-MM-DD folders IN THE REPO.
        - Excludes Archive/
        - Uses GitHub Pages URLs (cleaner than raw.githubusercontent.com)
        - Adds guid + pubDate
        - Orders newest date first; within date, orders by filename (01..)
        """
        print(f"{'='*70}")
        print("📡 Generating RSS feed...")
        print(f"{'='*70}\n")

        rss = ET.Element("rss", {
            "version": "2.0",
            "xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        })
        channel = ET.SubElement(rss, "channel")

        ET.SubElement(channel, "title").text = "My Economist Chapters"
        ET.SubElement(channel, "description").text = "Personal custom-sorted chapters from The Economist Weekly Edition"
        ET.SubElement(channel, "language").text = "en-us"
        ET.SubElement(channel, "link").text = self.feed_url
        ET.SubElement(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}author").text = "Personal Feed"
        ET.SubElement(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit").text = "no"

        # Find episode folders
        episode_folders = []
        for item in os.listdir(self.base_folder):
            item_path = os.path.join(self.base_folder, item)
            if os.path.isdir(item_path) and item.startswith("Economist_") and item != "Archive":
                episode_folders.append(item)

        # newest first
        episode_folders.sort(reverse=True)

        item_count = 0
        for folder in episode_folders:
            folder_path = os.path.join(self.base_folder, folder)

            # Only include mp3s, sorted (01..)
            mp3_files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith(".mp3")])

            date_part = folder.replace("Economist_", "")
            # pubDate based on folder date (fallback to now)
            try:
                folder_date = datetime.strptime(date_part, "%Y-%m-%d")
                pub_date = folder_date.strftime("%a, %d %b %Y 12:00:00 GMT")
            except Exception:
                pub_date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")

            for mp3_file in mp3_files:
                mp3_path = os.path.join(folder_path, mp3_file)
                file_size = os.path.getsize(mp3_path)

                title_part = os.path.splitext(mp3_file)[0].split(" - ", 1)[-1]
                full_title = f"{date_part} - {title_part}"

                # GitHub Pages URL: encode each path component safely
                file_url = f"{self.site_base}/{quote(folder)}/{quote(mp3_file)}"

                item = ET.SubElement(channel, "item")
                ET.SubElement(item, "title").text = full_title
                ET.SubElement(item, "description").text = f"The Economist Weekly Edition - {title_part}"
                ET.SubElement(item, "guid").text = file_url
                ET.SubElement(item, "pubDate").text = pub_date
                ET.SubElement(item, "enclosure", {
                    "url": file_url,
                    "length": str(file_size),
                    "type": "audio/mpeg",
                })

                item_count += 1
                print(f"  ✓ Added: {full_title}")

        xml_str = minidom.parseString(ET.tostring(rss)).toprettyxml(indent="  ")
        feed_path = os.path.join(self.base_folder, "feed.xml")
        with open(feed_path, "w", encoding="utf-8") as f:
            f.write(xml_str)

        print(f"\n✅ RSS feed created: {feed_path}")
        print(f"📊 Total items in feed: {item_count}\n")

    # ---------------------------
    # Git push (FIXED / SAFER)
    # ---------------------------
    def git_push(self):
        """
        Commit & push changes.

        FIXES vs your old version:
        - Does NOT delete/rewire git history by default (no -f push).
        - Does NOT try to 'git rm --cached' every episode folder every time.
        - Only stages normal changes and pushes.

        If you previously force-pushed and want to keep doing that, you can set force=True below.
        """
        self._git_push_impl(force=False)

    def _git_push_impl(self, force: bool = False):
        print(f"{'='*70}")
        print("📤 Pushing to GitHub...")
        print(f"{'='*70}\n")

        try:
            os.chdir(self.base_folder)

            # Basic sanity: ensure it's a git repo
            subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], check=True, capture_output=True)

            # Stage everything except ignored (Archive)
            print("  📝 Staging changes...")
            subprocess.run(["git", "add", "-A"], check=True, capture_output=True)

            # Commit if needed
            commit_msg = f"Update Economist chapters {datetime.now().strftime('%Y-%m-%d')}"
            result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True)
            if not result.stdout.strip():
                print("  ✓ No changes to commit")
            else:
                print(f"  💾 Committing: {commit_msg}")
                subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
                print("  ✓ Committed successfully")

            # Push
            print("  🚀 Pushing...")
            if force:
                subprocess.run(["git", "push", "-f"], check=True, capture_output=True)
            else:
                subprocess.run(["git", "push"], check=True, capture_output=True)
            print("  ✅ Pushed successfully!\n")

        except subprocess.CalledProcessError as e:
            print("  ❌ Git command failed.")
            # Show stderr if available
            try:
                stderr = e.stderr.decode("utf-8", errors="ignore") if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or "")
                stdout = e.stdout.decode("utf-8", errors="ignore") if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or "")
                if stdout:
                    print("\n--- stdout ---\n" + stdout)
                if stderr:
                    print("\n--- stderr ---\n" + stderr)
            except Exception:
                pass
            print("\nCommon fix:")
            print("  git pull --rebase")
            print("  git push\n")
        except Exception as e:
            print(f"  ❌ Error: {e}\n")


def main():
    """
    ✅ Update these two values to match your current repo.
    Your screenshots show the repo is: economist-podcast
    """
    BASE_FOLDER = os.path.dirname(os.path.abspath(__file__))

    GITHUB_USERNAME = "mnyamukondiwa"
    GITHUB_REPO = "economist-podcast"   # <-- FIXED to match your actual repo
    BRANCH = "main"

    processor = EconomistPodcastMaster(BASE_FOLDER, GITHUB_USERNAME, GITHUB_REPO, branch=BRANCH)
    processor.run_complete_workflow()


if __name__ == "__main__":
    main()
