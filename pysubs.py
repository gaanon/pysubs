import argparse
import hashlib
import logging
import os
import struct
import sys

import requests
import ffmpeg

# --- Constants ---
# IMPORTANT: It's recommended to use an environment variable for your API key.
# You can set it in your shell, for example: export OPENSUBTITLES_API_KEY='your_key'
# The script also requires 'requests' and 'ffmpeg-python':
# pip install requests ffmpeg-python
API_KEY = os.environ.get("OPENSUBTITLES_API_KEY")
API_URL = "https://api.opensubtitles.com/api/v1"
USER_AGENT = "pysubs v1.0"


def setup_logging():
    """Configures the logging format and level."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
        )
    
    
    def has_external_subtitles(video_path):
        """Checks for existing .srt or .sub subtitle files."""
        base, _ = os.path.splitext(video_path)
        for ext in [".srt", ".sub"]:
            sub_path = base + ext
            if os.path.isfile(sub_path):
                logging.info(f"Found existing subtitle file: {os.path.basename(sub_path)}")
                return True
        return False
    
    
    def has_embedded_subtitles(video_path):
        """Checks for embedded subtitle streams using ffprobe."""
        try:
            probe = ffmpeg.probe(video_path)
            subtitle_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "subtitle"]
            if subtitle_streams:
                logging.info(f"Found {len(subtitle_streams)} embedded subtitle stream(s).")
                return True
        except ffmpeg.Error as e:
            logging.warning(f"Could not probe for embedded subtitles. Ensure ffmpeg is installed and in your PATH. Error: {e}")
        return False
    
    
    def generate_opensubtitles_hash(video_path):
        """
        Generates the OpenSubtitles hash for a video file.
        This is a specific algorithm that hashes the first and last 64KB of the file.
        """
        try:
            longlongformat = '<q'  # Little-endian 8-byte long long
            bytesize = struct.calcsize(longlongformat)
            filesize = os.path.getsize(video_path)
            hash_value = filesize
    
            if filesize < 65536 * 2:
                logging.warning("File is too small for standard hash calculation.")
                return None
    
            with open(video_path, 'rb') as f:
                # Read first 64KB
                for _ in range(65536 // bytesize):
                    buffer = f.read(bytesize)
                    (l_value,) = struct.unpack(longlongformat, buffer)
                    hash_value += l_value
                    hash_value &= 0xFFFFFFFFFFFFFFFF  # To remain as 64-bit int
    
                # Seek to last 64KB
                f.seek(max(0, filesize - 65536), 0)
                for _ in range(65536 // bytesize):
                    buffer = f.read(bytesize)
                    (l_value,) = struct.unpack(longlongformat, buffer)
                    hash_value += l_value
                    hash_value &= 0xFFFFFFFFFFFFFFFF
    
            returnedhash = "%016x" % hash_value
            return returnedhash
        except Exception as e:
            logging.error(f"Error generating hash: {e}")
            return None
    
    
    def search_subtitles(params):
        """Searches for subtitles on OpenSubtitles.com."""
        headers = {"Api-Key": API_KEY, "User-Agent": USER_AGENT, "Content-Type": "application/json"}
        try:
            response = requests.get(f"{API_URL}/subtitles", headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("data", [])
        except requests.exceptions.RequestException as e:
            logging.error(f"API request failed: {e}")
            return []
    
    
    def get_best_subtitle(subtitles):
        """Selects the best subtitle from a list based on download count."""
        if not subtitles:
            return None
        # Sort by download count in descending order
        return max(subtitles, key=lambda s: s.get("attributes", {}).get("download_count", 0))
    
    
    def download_and_save_subtitle(subtitle_data, video_path):
        """Requests download link, downloads, and saves the subtitle file."""
        files = subtitle_data.get("attributes", {}).get("files")
        if not files or not isinstance(files, list) or 'file_id' not in files[0]:
            logging.error("Could not find a valid file_id for download in the subtitle data.")
            return
    
        file_id = files[0]['file_id']
    
        headers = {"Api-Key": API_KEY, "User-Agent": USER_AGENT, "Content-Type": "application/json"}
        payload = {"file_id": file_id}
    
        try:
            # 1. Get the download link
            logging.info("Requesting download link...")
            resp = requests.post(f"{API_URL}/download", headers=headers, json=payload)
            resp.raise_for_status()
            download_info = resp.json()
            download_link = download_info.get("link")
    
            if not download_link:
                logging.error("Failed to get download link from API.")
                return
    
            # 2. Download the subtitle content
            logging.info(f"Downloading subtitle...")
            sub_resp = requests.get(download_link)
            sub_resp.raise_for_status()
            sub_content = sub_resp.text # requests handles gzip decompression automatically
    
            # 3. Save the file
            subtitle_path = os.path.splitext(video_path)[0] + ".srt"
            with open(subtitle_path, "w", encoding="utf-8") as f:
                f.write(sub_content)
    
            logging.info(f"Subtitle saved successfully to: {os.path.basename(subtitle_path)}")
    
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to download subtitle: {e}")
        except Exception as e:
            logging.error(f"An error occurred during subtitle download/save: {e}")
    
    
    def main():
        """Main script logic."""
        setup_logging()
    
        if not API_KEY:
            logging.error("OpenSubtitles API key not found. Please set the OPENSUBTITLES_API_KEY environment variable.")
            sys.exit(1)
    
        parser = argparse.ArgumentParser(description="Download English subtitles for a video file.")
        parser.add_argument("video_path", help="The full path to the video file.")
        args = parser.parse_args()
    
        video_path = args.video_path
        filename = os.path.basename(video_path)
    
        if not os.path.isfile(video_path):
            logging.error(f"File not found: {video_path}")
            sys.exit(1)
    
        logging.info(f"Processing: {filename}")
    
        if has_external_subtitles(video_path) or has_embedded_subtitles(video_path):
            logging.info("Subtitles already exist. Skipping.")
            sys.exit(0)
    
        subtitle_data = None
        search_method = None
    
        # 1. Try searching by hash
        movie_hash = generate_opensubtitles_hash(video_path)
        if movie_hash:
            logging.info(f"Generated OpenSubtitles hash: {movie_hash}")
            search_params = {"moviehash": movie_hash, "languages": "en"}
            results = search_subtitles(search_params)
            if results:
                subtitle_data = get_best_subtitle(results)
                search_method = "hash"
                logging.info(f"Found {len(results)} subtitle(s) by hash.")
    
        # 2. If not found, try searching by filename
        if not subtitle_data:
            logging.info("No subtitles found by hash, searching by filename.")
            search_params = {"query": os.path.splitext(filename)[0], "languages": "en"}
            results = search_subtitles(search_params)
            if results:
                subtitle_data = get_best_subtitle(results)
                search_method = "filename"
                logging.info(f"Found {len(results)} subtitle(s) by filename.")
    
        if subtitle_data:
            logging.info(f"Best match found via {search_method}. Proceeding to download.")
            download_and_save_subtitle(subtitle_data, video_path)
        else:
            logging.info("No English subtitles found on OpenSubtitles.")
    
    
        logging.info("Script finished.")
    
    
    if __name__ == "__main__":
        main()