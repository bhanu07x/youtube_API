from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
import requests
import re
import json
import os
import codecs
import time
import random
from urllib.parse import urlparse, parse_qs
import tempfile
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

def clean_description(raw_description):
    """
    Clean and format the YouTube description text for better readability
    """
    try:
        description = raw_description
        
        # Handle JSON-escaped unicode sequences first
        try:
            # Decode JSON escape sequences like \u0000
            description = codecs.decode(description, 'unicode_escape')
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        
        # Handle common escape sequences
        description = description.replace('\\n', '\n')
        description = description.replace('\\r', '\r')
        description = description.replace('\\t', '\t')
        description = description.replace('\\"', '"')
        description = description.replace("\\'", "'")
        description = description.replace('\\\\', '\\')
        
        # Try to fix garbled Unicode characters
        try:
            if 'ð' in description or any(ord(c) > 255 for c in description if isinstance(c, str)):
                description = description.encode('latin-1').decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError, AttributeError):
            try:
                description = description.encode('utf-8', errors='ignore').decode('utf-8')
            except (UnicodeDecodeError, UnicodeEncodeError):
                description = ''.join(char for char in description if ord(char) < 128)
        
        # Clean up whitespace while preserving intentional formatting
        lines = description.split('\n')
        cleaned_lines = []
        
        for line in lines:
            cleaned_line = line.strip()
            cleaned_lines.append(cleaned_line)
        
        description = '\n'.join(cleaned_lines)
        
        # Remove excessive consecutive newlines (more than 2)
        description = re.sub(r'\n{3,}', '\n\n', description)
        
        # Final cleanup - ensure it's valid UTF-8
        try:
            description = description.encode('utf-8').decode('utf-8')
        except UnicodeError:
            description = description.encode('utf-8', errors='replace').decode('utf-8')
        
        # Trim if too long
        if len(description) > 2000:
            description = description[:2000] + "..."
        
        return description.strip()
        
    except Exception as e:
        print(f"Error in clean_description: {str(e)}")
        return f"Error cleaning description: {str(e)}"

def get_youtube_info(url):
    """
    Get YouTube video title and description from any YouTube URL
    Enhanced version with better detection and alternative methods
    """
    try:
        # Try multiple approaches in order
        methods = [
            lambda: get_youtube_info_method1(url),
            lambda: get_youtube_info_method2(url),
            lambda: get_youtube_info_method3(url)
        ]
        
        for i, method in enumerate(methods):
            try:
                result = method()
                if (result['title'] not in ['Title not found', 'Error'] and 
                    result['description'] not in ['Description not found', 'Error']):
                    print(f"Success with method {i+1}")
                    return result
                elif result['title'] not in ['Title not found', 'Error']:
                    print(f"Partial success with method {i+1} (title only)")
                    for j, method2 in enumerate(methods[i+1:], i+1):
                        try:
                            result2 = method2()
                            if result2['description'] not in ['Description not found', 'Error']:
                                return {
                                    'title': result['title'],
                                    'description': result2['description']
                                }
                        except:
                            continue
                    return result
            except Exception as e:
                print(f"Method {i+1} failed: {str(e)}")
                continue
        
        return {
            'title': "All extraction methods failed",
            'description': "Could not extract video information"
        }
        
    except Exception as e:
        print(f"Error in get_youtube_info: {str(e)}")
        return {
            'title': f"Error: {str(e)}",
            'description': f"Error: {str(e)}"
        }

def get_youtube_info_method1(url):
    """Method 1: Standard web scraping with enhanced patterns"""
    time.sleep(random.uniform(0.5, 2))
    
    session = requests.Session()
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'DNT': '1'
    }
    
    response = session.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    
    content = response.text
    print(f"Method 1 - Content length: {len(content)}")
    
    if "unusual traffic" in content.lower() or len(content) < 1000:
        raise Exception("Request appears to be blocked")
    
    # Extract title
    title_patterns = [
        r'"videoDetails":\s*{[^}]*?"title":"((?:[^"\\]|\\.)*)(?<!\\)"',
        r'<title[^>]*>([^<]+?)\s*-\s*YouTube</title>',
        r'<meta property="og:title" content="([^"]*)"',
        r'"title":"((?:[^"\\]|\\.)*?)(?<!\\)"[^}]*?"lengthSeconds"',
    ]
    
    title = "Title not found"
    for pattern in title_patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            title = clean_description(match.group(1))
            if len(title.strip()) > 0:
                break
    
    # Extract description
    desc_patterns = [
        r'"videoDetails":\s*{[^}]*?"shortDescription":"((?:[^"\\]|\\.)*)(?<!\\)"',
        r'"shortDescription":"((?:[^"\\]|\\.)*)(?<!\\)"',
        r'<meta property="og:description" content="([^"]*)"',
        r'"description":\s*{"simpleText":"((?:[^"\\]|\\.)*)(?<!\\)"}',
    ]
    
    description = "Description not found"
    for pattern in desc_patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            description = clean_description(match.group(1))
            if len(description.strip()) > 0:
                break
    
    return {'title': title, 'description': description}

def get_youtube_info_method2(url):
    """Method 2: Mobile YouTube approach"""
    time.sleep(random.uniform(0.5, 2))
    
    mobile_url = url.replace('www.youtube.com', 'm.youtube.com')
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
    }
    
    response = requests.get(mobile_url, headers=headers, timeout=15)
    response.raise_for_status()
    
    content = response.text
    print(f"Method 2 - Mobile content length: {len(content)}")
    
    title_patterns = [
        r'<title[^>]*>([^<]+)</title>',
        r'"title":"([^"]*)"',
        r'<meta property="og:title" content="([^"]*)"'
    ]
    
    title = "Title not found"
    for pattern in title_patterns:
        match = re.search(pattern, content)
        if match:
            raw_title = match.group(1)
            title = clean_description(raw_title.replace(' - YouTube', ''))
            if len(title.strip()) > 0:
                break
    
    desc_patterns = [
        r'<meta property="og:description" content="([^"]*)"',
        r'"description":"([^"]*)"',
        r'<meta name="description" content="([^"]*)"'
    ]
    
    description = "Description not found"
    for pattern in desc_patterns:
        match = re.search(pattern, content)
        if match:
            description = clean_description(match.group(1))
            if len(description.strip()) > 0:
                break
    
    return {'title': title, 'description': description}

def get_youtube_info_method3(url):
    """Method 3: Alternative approach using different endpoints"""
    try:
        video_id = get_video_id_from_url(url)
        if not video_id:
            raise Exception("Could not extract video ID")
        
        time.sleep(random.uniform(0.5, 2))
        
        oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; YTInfoExtractor/1.0)'
        }
        
        try:
            response = requests.get(oembed_url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                title = data.get('title', 'Title not found')
                
                main_response = requests.get(url, headers=headers, timeout=10)
                desc_match = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)(?<!\\)"', main_response.text)
                description = "Description not found"
                if desc_match:
                    description = clean_description(desc_match.group(1))
                
                print(f"Method 3 - oEmbed success")
                return {'title': title, 'description': description}
        except:
            pass
        
        simple_headers = {'User-Agent': 'curl/7.68.0'}
        response = requests.get(url, headers=simple_headers, timeout=15)
        content = response.text
        
        title_match = re.search(r'<title[^>]*>([^<]+)</title>', content)
        title = "Title not found"
        if title_match:
            title = clean_description(title_match.group(1).replace(' - YouTube', ''))
        
        desc_match = re.search(r'<meta property="og:description" content="([^"]*)"', content)
        description = "Description not found"
        if desc_match:
            description = clean_description(desc_match.group(1))
        
        print(f"Method 3 - Simple extraction")
        return {'title': title, 'description': description}
        
    except Exception as e:
        print(f"Method 3 error: {str(e)}")
        return {'title': "Title not found", 'description': "Description not found"}

def get_youtube_tags(video_url):
    """
    Extract YouTube video tags from the video page
    """
    try:
        time.sleep(random.uniform(0.5, 1.5))
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        response = requests.get(video_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        tag_patterns = [
            r'"keywords":\s*\[(.*?)\]',
            r'"tags":\s*\[(.*?)\]',
            r'"hashtags":\s*\[(.*?)\]'
        ]
        
        all_tags = []
        
        for pattern in tag_patterns:
            keywords_match = re.search(pattern, response.text)
            if keywords_match:
                keywords_str = keywords_match.group(1)
                tags = re.findall(r'"([^"]*)"', keywords_str)
                all_tags.extend(tags)
        
        seen = set()
        unique_tags = []
        for tag in all_tags:
            if tag not in seen and len(tag.strip()) > 0:
                seen.add(tag)
                unique_tags.append(tag.strip())
        
        return unique_tags[:20]
        
    except Exception as e:
        print(f"Error extracting tags: {str(e)}")
        return []

def get_video_id_from_url(video_url):
    """Extract video ID from YouTube URL"""
    try:
        parsed = urlparse(video_url)
        video_id = None

        if parsed.hostname in ("youtu.be",):
            video_id = parsed.path[1:]
        elif parsed.hostname in ("www.youtube.com", "youtube.com"):
            if "/watch" in parsed.path:
                qs = parse_qs(parsed.query)
                video_id = qs.get("v", [None])[0]
            elif "/embed/" in parsed.path:
                video_id = parsed.path.split("/embed/")[1].split("?")[0]
            elif "/v/" in parsed.path:
                video_id = parsed.path.split("/v/")[1].split("?")[0]

        if video_id:
            video_id = video_id.split("&")[0].split("?")[0]
            
        return video_id
    except Exception as e:
        print(f"Error extracting video ID: {str(e)}")
        return None

@app.route('/')
def index():
    """Serve the main HTML page"""
    return render_template('index.html')

@app.route('/api/extract', methods=['POST'])
def extract_video_info():
    """API endpoint to extract YouTube video information"""
    try:
        data = request.get_json()
        video_url = data.get('url')
        
        if not video_url:
            return jsonify({'error': 'No URL provided'}), 400
        
        if not ('youtube.com' in video_url or 'youtu.be' in video_url):
            return jsonify({'error': 'Invalid YouTube URL'}), 400
        
        print(f"Extracting info for URL: {video_url}")
        
        info = get_youtube_info(video_url)
        tags = get_youtube_tags(video_url)
        
        video_id = get_video_id_from_url(video_url)
        
        thumbnail_urls = [
            f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/default.jpg"
        ] if video_id else []
        
        thumbnail_url = None
        for url in thumbnail_urls:
            try:
                response = requests.head(url, timeout=5)
                if response.status_code == 200:
                    thumbnail_url = url
                    break
            except:
                continue
        
        result = {
            'title': info['title'],
            'description': info['description'],
            'tags': tags,
            'thumbnail': thumbnail_url,
            'video_id': video_id
        }
        
        print(f"Extraction completed. Title: {info['title'][:50]}...")
        return jsonify(result)
        
    except Exception as e:
        print(f"API Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/download-thumbnail/<video_id>')
def download_thumbnail(video_id):
    """API endpoint to download thumbnail"""
    try:
        thumbnail_urls = [
            f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/default.jpg"
        ]
        
        for thumbnail_url in thumbnail_urls:
            try:
                response = requests.get(thumbnail_url, timeout=10)
                if response.status_code == 200:
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
                    temp_file.write(response.content)
                    temp_file.close()
                    
                    return send_file(
                        temp_file.name,
                        as_attachment=True,
                        download_name=f'{video_id}_thumbnail.jpg',
                        mimetype='image/jpeg'
                    )
            except Exception as e:
                print(f"Error downloading thumbnail from {thumbnail_url}: {str(e)}")
                continue
        
        return jsonify({'error': 'Thumbnail not found'}), 404
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'message': 'YouTube Info Extractor is running'})

# Get port from environment variable or default to 5000
port = int(os.environ.get('PORT', 5000))

if __name__ == '__main__':
    print("Starting YouTube Info Extractor...")
    print("Available endpoints:")
    print("  GET  / - Main interface")
    print("  POST /api/extract - Extract video info")
    print("  GET  /api/download-thumbnail/<video_id> - Download thumbnail")
    print("  GET  /health - Health check")
    
    app.run(debug=False, host='0.0.0.0', port=port)