import os
import subprocess
from datetime import datetime
from mutagen.id3 import ID3
import shutil
import xml.etree.ElementTree as ET
from xml.dom import minidom

class EconomistPodcastMaster:
    """Complete end-to-end Economist podcast processor"""
    
    def __init__(self, base_folder, github_username, github_repo):
        self.base_folder = base_folder
        self.github_username = github_username
        self.github_repo = github_repo
        self.archive_folder = os.path.join(base_folder, 'Archive')
        self.feed_url = f"https://{github_username}.github.io/{github_repo}/feed.xml"
        
        os.makedirs(self.archive_folder, exist_ok=True)
        
        # Create .gitignore on startup to exclude Archive
        self.create_gitignore()
    
    def create_gitignore(self):
        """Create .gitignore to exclude Archive folder and large files"""
        gitignore_path = os.path.join(self.base_folder, '.gitignore')
        gitignore_content = """# Exclude Archive folder (contains large original MP3s)
Archive/

# Exclude any original MP3 files
original*.mp3
"""
        with open(gitignore_path, 'w', encoding='utf-8') as f:
            f.write(gitignore_content)
    
    def run_complete_workflow(self):
        """Main workflow: Split → Generate RSS → Git Push"""
        
        print(f"""
{'='*70}
🎙️  ECONOMIST PODCAST AUTOMATION
{'='*70}

📂 Folder: {self.base_folder}
📡 Feed: {self.feed_url}

Starting complete workflow...
{'='*70}
""")
        
        # Step 1: Find and process MP3 files
        mp3_files = self.find_mp3_files()
        
        if mp3_files:
            print(f"\n✓ Found {len(mp3_files)} MP3 file(s) to process\n")
            for mp3_file in mp3_files:
                self.split_economist_file(mp3_file)
            
            # Archive old episodes
            self.archive_old_episodes()
        else:
            print("\n✓ No new MP3 files to process")
        
        # Step 2: Generate RSS feed
        self.generate_rss_feed()
        
        # Step 3: Git push
        self.git_push()
        
        print(f"""
{'='*70}
✅ COMPLETE! Your podcast is live!
{'='*70}

📡 Feed URL: {self.feed_url}
🎧 New episodes will appear in Overcast within 30 minutes

{'='*70}
""")
    
    def find_mp3_files(self):
        """Find all MP3 files in the base folder"""
        mp3_files = []
        for file in os.listdir(self.base_folder):
            full_path = os.path.join(self.base_folder, file)
            if file.lower().endswith('.mp3') and os.path.isfile(full_path):
                mp3_files.append(full_path)
        return mp3_files
    
    def split_economist_file(self, input_file):
        """Split MP3 file by chapters"""
        
        try:
            date_str = datetime.now().strftime("%Y-%m-%d")
            output_folder = os.path.join(self.base_folder, f"Economist_{date_str}")
            os.makedirs(output_folder, exist_ok=True)
            
            print(f"{'='*70}")
            print(f"🎧 Processing: {os.path.basename(input_file)}")
            print(f"📁 Output: Economist_{date_str}")
            print(f"{'='*70}\n")
            
            file_size = os.path.getsize(input_file) / (1024*1024)
            print(f"📊 File size: {file_size:.1f} MB")
            
            # Move to output folder
            temp_file = os.path.join(output_folder, "original.mp3")
            print(f"📦 Moving file to output folder...")
            shutil.move(input_file, temp_file)
            
            # Read chapters
            print(f"🔍 Reading chapter information...")
            tags = ID3(temp_file)
            chapters = [tag for tag in tags.keys() if tag.startswith('CHAP')]
            
            if not chapters:
                print(f"⚠️  No chapters found - keeping original file")
                final_name = os.path.join(output_folder, os.path.basename(input_file))
                shutil.move(temp_file, final_name)
                return
            
            print(f"✓ Found {len(chapters)} chapters\n")
            
            # Extract and sort chapter info
            chapter_info = []
            for chap_id in chapters:
                chap = tags[chap_id]
                start_time = chap.start_time / 1000
                end_time = chap.end_time / 1000
                duration = end_time - start_time
                
                title = f"Chapter_{len(chapter_info) + 1}"
                if hasattr(chap, 'sub_frames'):
                    for frame in chap.sub_frames.values():
                        if hasattr(frame, 'text'):
                            title = str(frame.text[0])
                            title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()[:50]
                            break
                
                chapter_info.append({
                    'start_time': start_time,
                    'duration': duration,
                    'title': title
                })
            
            # Custom sorting logic
            def get_sort_priority(chapter):
                """Determine sort priority and duration for sorting"""
                title_lower = chapter['title'].lower()
                duration = chapter['duration']
                
                # Priority 1: The World This Week (always first)
                if 'world this week' in title_lower:
                    return (1, 0)
                
                # Priority 2: Letters (always second)
                if 'letter' in title_lower:
                    return (2, 0)
                
                # Priority 3: Business (shortest first within Business)
                if 'business' in title_lower:
                    return (3, duration)
                
                # Priority 4: Finance & Economics (shortest first within Finance)
                if 'finance' in title_lower or 'economic' in title_lower:
                    return (4, duration)
                
                # Priority 6: Briefing (always last)
                if 'briefing' in title_lower:
                    return (6, 999999)
                
                # Priority 5: Everything else (shortest first)
                return (5, duration)
            
            chapter_info.sort(key=get_sort_priority)
            
            print(f"📊 Custom sort order applied:")
            print(f"   1. The World This Week (first)")
            print(f"   2. Letters (second)")
            print(f"   3. Business (shortest first)")
            print(f"   4. Finance & Economics (shortest first)")
            print(f"   5. Everything Else (shortest first)")
            print(f"   6. Briefing (last)\n")
            
            # Split chapters
            chapter_files = []
            deleted_count = 0
            
            for i, chapter in enumerate(chapter_info, 1):
                # Skip chapters shorter than 1 minute
                if chapter['duration'] < 60:
                    print(f"  {i:02d}. {chapter['title']} ({chapter['duration']:.1f}s) ⏭️  SKIPPED (too short)")
                    deleted_count += 1
                    continue
                
                output_file = os.path.join(output_folder, f"{i:02d} - {chapter['title']}.mp3")
                
                cmd = [
                    'ffmpeg', '-i', temp_file,
                    '-ss', str(chapter['start_time']),
                    '-t', str(chapter['duration']),
                    '-acodec', 'copy',
                    '-loglevel', 'quiet',
                    '-y', output_file
                ]
                
                subprocess.run(cmd, capture_output=True)
                
                if os.path.exists(output_file):
                    chapter_size = os.path.getsize(output_file) / (1024*1024)
                    chapter_files.append(output_file)
                    print(f"  {i:02d}. {chapter['title']} ({chapter['duration']/60:.1f} min, {chapter_size:.1f} MB) ✓")
            
            # Create chapter list
            summary_file = os.path.join(output_folder, "00 - Chapter List.txt")
            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write(f"The Economist Weekly Edition\n")
                f.write(f"Processed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"="*60 + "\n\n")
                for file in chapter_files:
                    f.write(f"{os.path.basename(file)}\n")
            
            # Move original MP3 to Archive immediately
            archive_original = os.path.join(self.archive_folder, f"original_{date_str}.mp3")
            shutil.move(temp_file, archive_original)
            print(f"\n📦 Moved original MP3 to Archive: original_{date_str}.mp3")
            print(f"💾 Original size: {file_size:.1f} MB (excluded from GitHub)")
            
            print(f"\n✅ Created {len(chapter_files)} chapter files")
            if deleted_count > 0:
                print(f"⏭️  Skipped {deleted_count} chapter(s) shorter than 1 minute")
            print()
            
        except Exception as e:
            print(f"\n❌ Error processing file: {e}")
            import traceback
            traceback.print_exc()
    
    def archive_old_episodes(self):
        """Archive old episode folders, keeping only the newest"""
        print(f"{'='*70}")
        print(f"🗂️  Archiving old episodes...")
        print(f"{'='*70}\n")
        
        episode_folders = []
        for item in os.listdir(self.base_folder):
            item_path = os.path.join(self.base_folder, item)
            if os.path.isdir(item_path) and item.startswith('Economist_') and item != 'Archive':
                episode_folders.append(item)
        
        episode_folders.sort(reverse=True)
        
        if len(episode_folders) > 1:
            for folder in episode_folders[1:]:
                src = os.path.join(self.base_folder, folder)
                dst = os.path.join(self.archive_folder, folder)
                
                try:
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.move(src, dst)
                    print(f"  📦 Archived: {folder}")
                except Exception as e:
                    print(f"  ⚠️  Could not archive {folder}: {e}")
            print()
        else:
            print(f"  ✓ No old episodes to archive\n")
    
    def generate_rss_feed(self):
        """Generate podcast RSS feed"""
        
        print(f"{'='*70}")
        print(f"📡 Generating RSS feed...")
        print(f"{'='*70}\n")
        
        # Create RSS structure
        rss = ET.Element('rss', {
            'version': '2.0',
            'xmlns:itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd',
            'xmlns:content': 'http://purl.org/rss/1.0/modules/content/'
        })
        
        channel = ET.SubElement(rss, 'channel')
        
        # Podcast metadata
        ET.SubElement(channel, 'title').text = 'The Economist Weekly Edition (Chapters)'
        ET.SubElement(channel, 'description').text = 'The Economist Weekly Edition split into chapters for easier listening'
        ET.SubElement(channel, 'language').text = 'en-us'
        ET.SubElement(channel, 'link').text = self.feed_url
        
        # iTunes metadata
        ET.SubElement(channel, '{http://www.itunes.com/dtds/podcast-1.0.dtd}author').text = 'The Economist (Processed)'
        ET.SubElement(channel, '{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit').text = 'no'
        
        # Find all episode folders
        episode_folders = []
        for item in os.listdir(self.base_folder):
            item_path = os.path.join(self.base_folder, item)
            if os.path.isdir(item_path) and item.startswith('Economist_'):
                episode_folders.append(item)
        
        episode_folders.sort(reverse=True)
        
        # Add each chapter as an item
        item_count = 0
        for folder in episode_folders:
            folder_path = os.path.join(self.base_folder, folder)
            
            mp3_files = [f for f in os.listdir(folder_path) if f.endswith('.mp3')]
            mp3_files.sort()
            
            for mp3_file in mp3_files:
                mp3_path = os.path.join(folder_path, mp3_file)
                file_size = os.path.getsize(mp3_path)
                
                item = ET.SubElement(channel, 'item')
                
                date_part = folder.replace('Economist_', '')
                title_part = mp3_file.replace('.mp3', '').split(' - ', 1)[-1]
                full_title = f"{date_part} - {title_part}"
                
                ET.SubElement(item, 'title').text = full_title
                ET.SubElement(item, 'description').text = f"The Economist Weekly Edition - {title_part}"
                
                safe_folder = folder.replace(' ', '%20')
                safe_file = mp3_file.replace(' ', '%20')
                file_url = f"https://{self.github_username}.github.io/{self.github_repo}/{safe_folder}/{safe_file}"
                
                ET.SubElement(item, 'enclosure', {
                    'url': file_url,
                    'length': str(file_size),
                    'type': 'audio/mpeg'
                })
                
                ET.SubElement(item, 'guid').text = file_url
                
                try:
                    folder_date = datetime.strptime(date_part, "%Y-%m-%d")
                    pub_date = folder_date.strftime('%a, %d %b %Y 12:00:00 GMT')
                except:
                    pub_date = datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT')
                
                ET.SubElement(item, 'pubDate').text = pub_date
                
                item_count += 1
                print(f"  ✓ Added: {full_title}")
        
        # Write XML
        xml_str = minidom.parseString(ET.tostring(rss)).toprettyxml(indent="  ")
        feed_path = os.path.join(self.base_folder, 'feed.xml')
        
        with open(feed_path, 'w', encoding='utf-8') as f:
            f.write(xml_str)
        
        print(f"\n✅ RSS feed created: feed.xml")
        print(f"📊 Total items in feed: {item_count}\n")
    
    def git_push(self):
        """Commit and push to GitHub"""
        
        print(f"{'='*70}")
        print(f"📤 Pushing to GitHub...")
        print(f"{'='*70}\n")
        
        try:
            os.chdir(self.base_folder)
            
            # First, remove Archive from tracking if it exists (silently)
            print("  🧹 Ensuring Archive is not tracked...")
            subprocess.run(['git', 'rm', '-r', '--cached', 'Archive'], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Git add
            print("  📝 Staging files...")
            subprocess.run(['git', 'add', '.'], check=True, capture_output=True)
            
            # Git commit
            commit_msg = f"Economist episode {datetime.now().strftime('%Y-%m-%d')}"
            print(f"  💾 Committing: {commit_msg}")
            result = subprocess.run(['git', 'commit', '-m', commit_msg], 
                                  capture_output=True, text=True)
            
            if "nothing to commit" in result.stdout:
                print("  ✓ No changes to commit")
            else:
                print("  ✓ Committed successfully")
            
            # Git push
            print("  🚀 Pushing to GitHub...")
            subprocess.run(['git', 'push'], check=True, capture_output=True)
            print("  ✅ Pushed successfully!\n")
            
        except subprocess.CalledProcessError as e:
            print(f"  ⚠️  Git error: {e}")
            print(f"\nIf this is your first push, run these commands manually:")
            print(f"  git remote add origin https://github.com/{self.github_username}/{self.github_repo}.git")
            print(f"  git push -u origin main\n")
        except Exception as e:
            print(f"  ❌ Error: {e}\n")

def main():
    """Main entry point"""
    
    BASE_FOLDER = os.path.dirname(os.path.abspath(__file__))
    GITHUB_USERNAME = "mnyamukondiwa"
    GITHUB_REPO = "economist-podcast"
    
    processor = EconomistPodcastMaster(BASE_FOLDER, GITHUB_USERNAME, GITHUB_REPO)
    processor.run_complete_workflow()
    
    input("\nPress Enter to exit...")

if __name__ == "__main__":
    main()
