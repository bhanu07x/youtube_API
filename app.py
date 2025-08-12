from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import requests
import re
import os
import codecs
import time
import random
from urllib.parse import urlparse, parse_qs
import tempfile
from collections import defaultdict
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
CORS(app)

# Production configuration
if os.environ.get('FLASK_ENV') == 'production':
    app.config['DEBUG'] = False
else:
    app.config['DEBUG'] = True

# Get YouTube API key from environment variable
# YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY')
YOUTUBE_API_KEY = "AIzaSyAmrIwg2heqnU9_l7wp9NQCsicEB363Nis"

# Simple rate limiting
request_counts = defaultdict(list)

def rate_limit_check(ip, limit=10, window=60):
    """Rate limiting: 10 requests per minute per IP"""
    now = time.time()
    request_counts[ip] = [req_time for req_time in request_counts[ip] if now - req_time < window]
    
    if len(request_counts[ip]) >= limit:
        return False
    
    request_counts[ip].append(now)
    return True

@app.before_request
def limit_remote_addr():
    """Apply rate limiting to extract endpoint"""
    if request.endpoint == 'extract_video_info':
        client_ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
        if not rate_limit_check(client_ip):
            return jsonify({'error': 'Rate limit exceeded. Try again later.'}), 429

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
        logging.error(f"Error extracting video ID: {str(e)}")
        return None

def get_youtube_info_official_api(video_id):
    """Get video info using official YouTube Data API v3"""
    if not YOUTUBE_API_KEY:
        raise Exception("YouTube API key not configured")
    
    try:
        # YouTube Data API v3 endpoint
        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            'id': video_id,
            'key': YOUTUBE_API_KEY,
            'part': 'snippet,statistics',
            'fields': 'items(snippet(title,description,tags,thumbnails,channelTitle,publishedAt),statistics(viewCount,likeCount))'
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if not data.get('items'):
            return {
                'title': 'Video not found',
                'description': 'Video not found or private',
                'tags': [],
                'channel': 'Unknown',
                'published_at': None,
                'view_count': 0,
                'like_count': 0
            }
        
        video_data = data['items'][0]['snippet']
        stats_data = data['items'][0].get('statistics', {})
        
        # Get best thumbnail URL
        thumbnails = video_data.get('thumbnails', {})
        thumbnail_url = None
        
        # Try to get the best quality thumbnail
        for quality in ['maxres', 'standard', 'high', 'medium', 'default']:
            if quality in thumbnails:
                thumbnail_url = thumbnails[quality]['url']
                break
        
        result = {
            'title': video_data.get('title', 'Title not available'),
            'description': video_data.get('description', 'Description not available'),
            'tags': video_data.get('tags', []),
            'channel': video_data.get('channelTitle', 'Unknown'),
            'published_at': video_data.get('publishedAt'),
            'view_count': int(stats_data.get('viewCount', 0)),
            'like_count': int(stats_data.get('likeCount', 0)),
            'thumbnail_url': thumbnail_url
        }
        
        logging.info(f"Official API success: {result['title'][:50]}...")
        return result
        
    except requests.exceptions.RequestException as e:
        logging.error(f"YouTube API request failed: {str(e)}")
        raise Exception(f"YouTube API request failed: {str(e)}")
    except Exception as e:
        logging.error(f"YouTube API error: {str(e)}")
        raise Exception(f"YouTube API error: {str(e)}")

def get_random_user_agent():
    """Get a random user agent"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ]
    return random.choice(user_agents)

def clean_description(description):
    """Clean description text"""
    if not description or len(description.strip()) == 0:
        return "No description available"
    
    # Limit description length for API response
    if len(description) > 2000:
        description = description[:2000] + "..."
    
    return description.strip()

def get_youtube_info_fallback(url):
    """Fallback method using web scraping (for when API key is not available)"""
    try:
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        
        # Add delay to avoid rate limiting
        time.sleep(random.uniform(1, 2))
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        content = response.text
        
        # Check for blocking
        if len(content) < 1000 or "unusual traffic" in content.lower():
            raise Exception("Request appears to be blocked")
        
        # Extract title
        title = "Title not found"
        title_patterns = [
            r'<title[^>]*>([^<]+?)\s*-\s*YouTube</title>',
            r'<meta property="og:title" content="([^"]*)"',
            r'"videoDetails":\s*{[^}]*?"title":"((?:[^"\\]|\\.)*)(?<!\\)"'
        ]
        
        for pattern in title_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                title = match.group(1).strip()
                if len(title) > 0:
                    break
        
        # Extract description  
        description = "Description not found"
        desc_patterns = [
            r'"videoDetails":\s*{[^}]*?"shortDescription":"((?:[^"\\]|\\.)*)(?<!\\)"',
            r'<meta property="og:description" content="([^"]*)"'
        ]
        
        for pattern in desc_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                desc = match.group(1)
                if len(desc.strip()) > 0:
                    # Clean up escaped characters
                    desc = desc.replace('\\n', '\n').replace('\\"', '"').replace("\\'", "'")
                    description = clean_description(desc)
                    break
        
        # Try to extract basic tags
        tags = []
        tag_match = re.search(r'"keywords":\s*\[(.*?)\]', content)
        if tag_match:
            tags_str = tag_match.group(1)
            tags = [tag.strip('"') for tag in re.findall(r'"([^"]*)"', tags_str)]
            tags = [tag for tag in tags if len(tag.strip()) > 0][:10]  # Limit to 10 tags
        
        return {
            'title': title,
            'description': description,
            'tags': tags,
            'channel': 'Unknown',
            'published_at': None,
            'view_count': 0,
            'like_count': 0,
            'thumbnail_url': None
        }
        
    except Exception as e:
        logging.error(f"Fallback method failed: {str(e)}")
        return {
            'title': 'Extraction failed',
            'description': f'Could not extract video information: {str(e)}',
            'tags': [],
            'channel': 'Unknown',
            'published_at': None,
            'view_count': 0,
            'like_count': 0,
            'thumbnail_url': None
        }

@app.route('/api/extract', methods=['POST'])
def extract_video_info():
    """Extract YouTube video information"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'Invalid JSON data'}), 400
            
        video_url = data.get('url')
        
        if not video_url:
            return jsonify({'error': 'No URL provided'}), 400
        
        if not ('youtube.com' in video_url or 'youtu.be' in video_url):
            return jsonify({'error': 'Invalid YouTube URL'}), 400
        
        # Extract video ID
        video_id = get_video_id_from_url(video_url)
        if not video_id:
            return jsonify({'error': 'Could not extract video ID from URL'}), 400
        
        logging.info(f"Extracting info for video ID: {video_id}")
        
        # Try official API first, fallback to web scraping
        if YOUTUBE_API_KEY:
            try:
                info = get_youtube_info_official_api(video_id)
                method_used = "official_api"
                logging.info("Used official YouTube API")
            except Exception as e:
                logging.warning(f"Official API failed, using fallback: {str(e)}")
                info = get_youtube_info_fallback(video_url)
                method_used = "fallback_scraping"
        else:
            logging.info("No API key configured, using fallback method")
            info = get_youtube_info_fallback(video_url)
            method_used = "fallback_scraping"
        
        # Get thumbnail URL if not already provided
        thumbnail_url = info.get('thumbnail_url')
        if not thumbnail_url and video_id:
            thumbnail_urls = [
                f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
                f"https://img.youtube.com/vi/{video_id}/default.jpg"
            ]
            
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
            'tags': info['tags'],
            'thumbnail': thumbnail_url,
            'video_id': video_id,
            'channel': info.get('channel', 'Unknown'),
            'published_at': info.get('published_at'),
            'view_count': info.get('view_count', 0),
            'like_count': info.get('like_count', 0),
            'method_used': method_used,
            'api_key_configured': YOUTUBE_API_KEY is not None
        }
        
        logging.info(f"Extraction completed using {method_used}")
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"API Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/download-thumbnail/<video_id>')
def download_thumbnail(video_id):
    """Download thumbnail image"""
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
                logging.error(f"Error downloading from {thumbnail_url}: {str(e)}")
                continue
        
        return jsonify({'error': 'Thumbnail not found'}), 404
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'message': 'YouTube Info Extractor API is running',
        'api_key_configured': YOUTUBE_API_KEY is not None,
        'method': 'official_api' if YOUTUBE_API_KEY else 'fallback_scraping'
    })

@app.route('/api/config')
def config_info():
    """Show current configuration"""
    return jsonify({
        'youtube_api_key_configured': YOUTUBE_API_KEY is not None,
        'extraction_method': 'official_api' if YOUTUBE_API_KEY else 'fallback_scraping',
        'rate_limit': '10 requests per minute per IP',
        'supported_urls': [
            'https://www.youtube.com/watch?v=VIDEO_ID',
            'https://youtu.be/VIDEO_ID',
            'https://www.youtube.com/embed/VIDEO_ID'
        ]
    })

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = not os.environ.get('FLASK_ENV') == 'production'
    
    print("🚀 YouTube Info Extractor API Starting...")
    print(f"📍 Port: {port}")
    print(f"🔧 Debug: {debug_mode}")
    print(f"🔑 API Key: {'Configured' if YOUTUBE_API_KEY else 'Not configured'}")
    print(f"📊 Method: {'Official API' if YOUTUBE_API_KEY else 'Fallback scraping'}")
    
    if not YOUTUBE_API_KEY:
        print("\n⚠️  YouTube API Key not found!")
        print("   Set YOUTUBE_API_KEY environment variable for better reliability")
        print("   Get your key at: https://console.developers.google.com/")
    
    print("\n📋 API Endpoints:")
    print("  POST /api/extract - Extract video info")
    print("  GET  /api/download-thumbnail/<video_id> - Download thumbnail")
    print("  GET  /api/config - Show configuration")
    print("  GET  /health - Health check")
    print("=" * 50)
    
    app.run(debug=debug_mode, host='0.0.0.0', port=port)