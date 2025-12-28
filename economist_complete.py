import os
import subprocess
from datetime import datetime, timedelta
from mutagen.id3 import ID3
import shutil
import xml.etree.ElementTree as ET
from xml.dom import minidom
import re
from pathlib import Path
from urllib.parse import quote

class EconomistPodcastMaster:
    """
    Local workflow:
    - Split Economist MP3 into chapters (ffmpeg copy)
    - Apply your custom ordering
    - Skip < 60s
    - Archive original (ignored by git)
    - Generate feed.xml (Overcast ordered by your 01/02/03 numbering)
    - Commit & push
    """

    def __init__(self, base_folder: str, github_username: str, github_repo: str, branch: str = "main"):
        self.base_folder = os.path.abspath(base_folder)
        self.github_username = github_username
        self.github_repo = github_repo
        self.branch = branch

        self.archive_folder = os.path.join(self.base_folder, "Archive")
        os.makedirs(self.archive_folder, exist_ok=True)

        self.site_base = f"https://{github_username}.github.io/{github_repo}"
        self.feed_url = f"{self.site_base}/feed.xml"

        self.ensure_gitignore()

    def ensure_gitignore(self):
        """
        Merge required lines into .gitignore (do NOT overwrite).
        """
        gitignore_path = os.path.join(self.base_folder, ".gitignore")
        required_lines = [
            "# --- Economist podcast automation ---",
            "Archive/",
            "temp_original_*.mp3",
            "original*.mp3",
            "*.DS_Store",
            "",
            "# Do not publish text notes / chapter lists",
            "**/*.txt",
            "**/*Chapter List*.txt",
        ]

        if os.path.exists(gitignore_path):
            existing = Path(gitignore_path).read_text(encoding="utf-8", errors="ignore").splitlines()
            existing_set = set(line.rstrip() for line in existing)
            to_add = [line for line in required_lines if line not in existing_set]
            if to_add:
                with open(gitignore_path, "a", encoding="utf-8") as f:
                    f.write("\n\n" + "\n".join(to_add) + "\n")
        else:
            with open(gitignore_path, "w", encoding="utf-8") as f:
                f.write("\n".join(required_lines) + "\n")

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

        self.cleanup_existing_episodes()

        mp3_files = self.find_new_mp3_files()
        if mp3_files:
            print(f"\n✓ Found {len(mp3_files)} MP3 file(s) to process\n")
            for mp3_file in mp3_files:
                self.split_economist_file(mp3_file)
        else:
            print("\n✓ No new MP3 files to process")

        self.generate_rss_feed()
        self.git_push()

        print(f"""
{'='*70}
✅ COMPLETE! Your podcast is live (or updated)
{'='*70}

📡 Feed URL: {self.feed_url}

Tip: Overcast caches. If order doesn’t change immediately:
- pull to refresh inside the podcast, or
- remove + re-add the feed.

{'='*70}
""")

    def find_new_mp3_files(self):
        mp3_files = []
        for file in os.listdir(self.base_folder):
            full_path = os.path.join(self.base_folder, file)
            if os.path.isfile(full_path) and file.lower().endswith(".mp3") and not file.startswith("temp_"):
                # ignore if in Archive
                if os.path.commonpath([full_path, self.archive_folder]) == self.archive_folder:
                    continue
                mp3_files.append(full_path)
        return mp3_files

    def cleanup_existing_episodes(self):
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

            temp_original = os.path.join(self.base_folder, f"temp_original_{date_part}.mp3")
            shutil.copy2(archive_original, temp_original)

            shutil.rmtree(folder_path)
            self.split_economist_file(temp_original)

            if os.path.exists(temp_original):
                try:
                    os.remove(temp_original)
                except OSError:
                    pass

            print(f"  ✅ Cleaned up: {folder}\n")

        print()

    def split_economist_file(self, input_file: str):
        try:
            input_basename = os.path.basename(input_file)

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
                return

            print(f"✓ Found {len(chapters)} chapters\n")

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
                            title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_", "&", "'", ",")).strip()
                            title = title[:80]
                            break

                chapter_info.append({"start_time": start_time, "duration": duration, "title": title})

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

            chapter_files = []
            skipped_count = 0

            for i, chapter in enumerate(chapter_info, 1):
                if chapter["duration"] < 60:
                    skipped_count += 1
                    continue

                clean_title = re.sub(r"^\d+\s+", "", chapter["title"]).strip()
                output_file = os.path.join(output_folder, f"{i:02d} - {clean_title}.mp3")

                cmd = [
                    "ffmpeg", "-i", temp_file,
                    "-ss", str(chapter["start_time"]),
                    "-t", str(chapter["duration"]),
                    "-acodec", "copy",
                    "-loglevel", "error",
                    "-y", output_file
                ]
                subprocess.run(cmd, capture_output=True)

                if os.path.exists(output_file):
                    chapter_files.append(output_file)

            # ✅ NO MORE "00 - Chapter List.txt" (removed on purpose)

            archive_original = os.path.join(self.archive_folder, f"original_{date_str}.mp3")
            shutil.move(temp_file, archive_original)

            print(f"\n✅ Created {len(chapter_files)} chapter files (skipped {skipped_count} <60s)")
            print(f"📦 Archived original to: {archive_original}\n")

        except Exception as e:
            print(f"\n❌ Error processing file: {e}")
            import traceback
            traceback.print_exc()

    def generate_rss_feed(self):
        """
        FIXED for Overcast ordering:

        - Titles begin with "01/02/03 ..." so title-sorting keeps your order.
        - pubDate is unique per track so date-sorting ALSO keeps your order.
          We set 01 = 23:59, 02 = 23:58, etc (same day).
          Overcast typically shows newest first => 01 appears first.
        - Excludes any .txt files (we only include .mp3 anyway).
        - Uses GitHub Pages URLs.
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

        episode_folders = []
        for item in os.listdir(self.base_folder):
            item_path = os.path.join(self.base_folder, item)
            if os.path.isdir(item_path) and item.startswith("Economist_") and item != "Archive":
                episode_folders.append(item)

        episode_folders.sort(reverse=True)  # newest issue first

        item_count = 0
        for folder in episode_folders:
            folder_path = os.path.join(self.base_folder, folder)
            date_part = folder.replace("Economist_", "")

            # Parse folder date
            try:
                base_date = datetime.strptime(date_part, "%Y-%m-%d")
            except Exception:
                base_date = datetime.utcnow()

            mp3_files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith(".mp3")])

            # We set a "base time" at 23:59 then subtract minutes by track index
            base_time = base_date.replace(hour=23, minute=59, second=0)

            for idx, mp3_file in enumerate(mp3_files):
                mp3_path = os.path.join(folder_path, mp3_file)
                file_size = os.path.getsize(mp3_path)

                # Extract track number from filename prefix "01 - ..."
                m = re.match(r"^(\d{2})\s+-\s+(.*)\.mp3$", mp3_file, re.IGNORECASE)
                if m:
                    track = m.group(1)
                    title_part = m.group(2)
                else:
                    track = "00"
                    title_part = os.path.splitext(mp3_file)[0]

                # ✅ Title begins with track number to preserve ordering
                full_title = f"{date_part} - {track} - {title_part}"

                file_url = f"{self.site_base}/{quote(folder)}/{quote(mp3_file)}"

                # ✅ pubDate: 01 newest, 02 next, etc (so Overcast keeps your order)
                pub_dt = base_time - timedelta(minutes=idx)
                pub_date = pub_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")

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

        xml_str = minidom.parseString(ET.tostring(rss)).toprettyxml(indent="  ")
        feed_path = os.path.join(self.base_folder, "feed.xml")
        with open(feed_path, "w", encoding="utf-8") as f:
            f.write(xml_str)

        print(f"\n✅ RSS feed updated: {feed_path}")
        print(f"📊 Total items in feed: {item_count}\n")

    def git_push(self):
        print(f"{'='*70}")
        print("📤 Pushing to GitHub...")
        print(f"{'='*70}\n")

        try:
            os.chdir(self.base_folder)
            subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], check=True, capture_output=True)

            subprocess.run(["git", "add", "-A"], check=True, capture_output=True)

            status = subprocess.run(["git", "status", "--porcelain"], check=True, capture_output=True, text=True)
            if not status.stdout.strip():
                print("  ✓ No changes to commit")
                return

            commit_msg = f"Update Economist chapters {datetime.now().strftime('%Y-%m-%d')}"
            subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
            subprocess.run(["git", "push"], check=True, capture_output=True)
            print("  ✅ Pushed successfully!\n")

        except subprocess.CalledProcessError as e:
            print("  ❌ Git command failed.")
            print("  Try: git pull --rebase && git push")
            try:
                stderr = e.stderr.decode("utf-8", errors="ignore") if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or "")
                if stderr:
                    print("\n--- stderr ---\n" + stderr)
            except Exception:
                pass
        except Exception as e:
            print(f"  ❌ Error: {e}\n")


def main():
    BASE_FOLDER = os.path.dirname(os.path.abspath(__file__))
    GITHUB_USERNAME = "mnyamukondiwa"
    GITHUB_REPO = "economist-podcast"   # must match your repo name
    BRANCH = "main"

    processor = EconomistPodcastMaster(BASE_FOLDER, GITHUB_USERNAME, GITHUB_REPO, branch=BRANCH)
    processor.run_complete_workflow()


if __name__ == "__main__":
    main()
