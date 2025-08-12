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

# Simple rate limiting
request_counts = defaultdict(list)

def rate_limit_check(ip, limit=5, window=60):
    """Reduced rate limiting for production: 5 requests per minute per IP"""
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

def get_random_user_agent():
    """Get a random user agent to avoid detection"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0'
    ]
    return random.choice(user_agents)

def get_enhanced_headers():
    """Get enhanced headers to appear more like a real browser"""
    return {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Charset': 'utf-8, iso-8859-1;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'DNT': '1'
    }

def clean_description(raw_description):
    """Clean and format the YouTube description text for better readability"""
    try:
        if not raw_description or raw_description in ['Description not found', 'Error']:
            return raw_description
            
        description = raw_description
        
        # Handle JSON-escaped unicode sequences
        try:
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
        
        # Clean up whitespace while preserving formatting
        lines = description.split('\n')
        cleaned_lines = [line.strip() for line in lines]
        description = '\n'.join(cleaned_lines)
        description = re.sub(r'\n{3,}', '\n\n', description)
        
        # Final UTF-8 cleanup
        try:
            description = description.encode('utf-8').decode('utf-8')
        except UnicodeError:
            description = description.encode('utf-8', errors='replace').decode('utf-8')
        
        # Trim if too long
        if len(description) > 2000:
            description = description[:2000] + "..."
        
        return description.strip()
        
    except Exception as e:
        logging.error(f"Error in clean_description: {str(e)}")
        return "Error processing description"

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

def make_request_with_retry(url, headers=None, max_retries=3):
    """Make HTTP request with retry logic and enhanced error handling"""
    session = requests.Session()
    
    for attempt in range(max_retries):
        try:
            # Add random delay between attempts
            if attempt > 0:
                delay = random.uniform(2, 5) * (attempt + 1)
                logging.info(f"Retrying in {delay:.1f} seconds (attempt {attempt + 1})")
                time.sleep(delay)
            
            # Use enhanced headers if none provided
            if not headers:
                headers = get_enhanced_headers()
            
            # Add random delay before request
            time.sleep(random.uniform(1, 3))
            
            response = session.get(url, headers=headers, timeout=20)
            
            # Check if we got a valid response
            if response.status_code == 200:
                content = response.text
                
                # Check for common blocking indicators
                blocking_indicators = [
                    "unusual traffic",
                    "captcha",
                    "blocked",
                    "access denied",
                    "too many requests"
                ]
                
                content_lower = content.lower()
                if any(indicator in content_lower for indicator in blocking_indicators):
                    logging.warning(f"Detected blocking on attempt {attempt + 1}")
                    if attempt == max_retries - 1:
                        raise Exception("Request appears to be blocked after all retries")
                    continue
                
                if len(content) < 1000:
                    logging.warning(f"Suspiciously short content on attempt {attempt + 1}: {len(content)} chars")
                    if attempt == max_retries - 1:
                        raise Exception("Received unusually short response")
                    continue
                
                logging.info(f"Successfully retrieved content on attempt {attempt + 1}")
                return response
                
            elif response.status_code == 429:
                logging.warning(f"Rate limited on attempt {attempt + 1}")
                if attempt == max_retries - 1:
                    raise Exception("Rate limited after all retries")
                continue
            else:
                response.raise_for_status()
                
        except requests.exceptions.RequestException as e:
            logging.error(f"Request failed on attempt {attempt + 1}: {str(e)}")
            if attempt == max_retries - 1:
                raise e
            continue
    
    raise Exception("All retry attempts failed")

def get_youtube_info_oembed(url):
    """Try oEmbed endpoint first - less likely to be blocked"""
    try:
        oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'application/json'
        }
        
        response = requests.get(oembed_url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            title = data.get('title', 'Title not found')
            logging.info(f"oEmbed success: {title[:50]}...")
            return {'title': title, 'description': 'Description not available from oEmbed'}
            
    except Exception as e:
        logging.error(f"oEmbed method failed: {str(e)}")
        
    return {'title': 'Title not found', 'description': 'Description not found'}

def get_youtube_info_standard(url):
    """Standard web scraping with enhanced anti-detection"""
    try:
        response = make_request_with_retry(url)
        content = response.text
        
        logging.info(f"Standard method - Content length: {len(content)}")
        
        # Extract title with multiple patterns
        title_patterns = [
            r'"videoDetails":\s*{[^}]*?"title":"((?:[^"\\]|\\.)*)(?<!\\)"',
            r'<title[^>]*>([^<]+?)\s*-\s*YouTube</title>',
            r'<meta property="og:title" content="([^"]*)"',
            r'"title":"((?:[^"\\]|\\.)*?)(?<!\\)"[^}]*?"lengthSeconds"',
            r'<meta name="title" content="([^"]*)"'
        ]
        
        title = "Title not found"
        for pattern in title_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                extracted_title = clean_description(match.group(1))
                if len(extracted_title.strip()) > 0 and extracted_title != "YouTube":
                    title = extracted_title
                    break
        
        # Extract description with multiple patterns
        desc_patterns = [
            r'"videoDetails":\s*{[^}]*?"shortDescription":"((?:[^"\\]|\\.)*)(?<!\\)"',
            r'"shortDescription":"((?:[^"\\]|\\.)*)(?<!\\)"',
            r'<meta property="og:description" content="([^"]*)"',
            r'"description":\s*{"simpleText":"((?:[^"\\]|\\.)*)(?<!\\)"}',
            r'<meta name="description" content="([^"]*)"'
        ]
        
        description = "Description not found"
        for pattern in desc_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                extracted_desc = clean_description(match.group(1))
                if len(extracted_desc.strip()) > 0:
                    description = extracted_desc
                    break
        
        return {'title': title, 'description': description}
        
    except Exception as e:
        logging.error(f"Standard method failed: {str(e)}")
        return {'title': 'Title not found', 'description': 'Description not found'}

def get_youtube_info_mobile(url):
    """Mobile approach with enhanced headers"""
    try:
        mobile_url = url.replace('www.youtube.com', 'm.youtube.com')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        }
        
        response = make_request_with_retry(mobile_url, headers)
        content = response.text
        
        logging.info(f"Mobile method - Content length: {len(content)}")
        
        # Mobile-specific patterns
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
                extracted_title = clean_description(raw_title.replace(' - YouTube', ''))
                if len(extracted_title.strip()) > 0:
                    title = extracted_title
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
                extracted_desc = clean_description(match.group(1))
                if len(extracted_desc.strip()) > 0:
                    description = extracted_desc
                    break
        
        return {'title': title, 'description': description}
        
    except Exception as e:
        logging.error(f"Mobile method failed: {str(e)}")
        return {'title': 'Title not found', 'description': 'Description not found'}

def get_youtube_info(url):
    """Get YouTube video info using multiple methods with fallbacks"""
    try:
        # Method priority: oEmbed (least likely blocked) -> Mobile -> Standard
        methods = [
            ('oEmbed', lambda: get_youtube_info_oembed(url)),
            ('Mobile', lambda: get_youtube_info_mobile(url)),
            ('Standard', lambda: get_youtube_info_standard(url))
        ]
        
        best_result = {'title': 'All methods failed', 'description': 'Could not extract information'}
        
        for method_name, method in methods:
            try:
                result = method()
                
                # Check if we got useful information
                title_success = result['title'] not in ['Title not found', 'Error', 'All methods failed']
                desc_success = result['description'] not in ['Description not found', 'Error', 'Description not available from oEmbed']
                
                if title_success and desc_success:
                    logging.info(f"Full success with {method_name} method")
                    return result
                elif title_success:
                    logging.info(f"Partial success with {method_name} method (title only)")
                    best_result = result
                    # Continue trying other methods for description
                    
            except Exception as e:
                logging.error(f"{method_name} method failed: {str(e)}")
                continue
        
        return best_result
        
    except Exception as e:
        logging.error(f"Error in get_youtube_info: {str(e)}")
        return {
            'title': f"Error: {str(e)}",
            'description': f"Error: {str(e)}"
        }

def get_youtube_tags(video_url):
    """Extract YouTube video tags with enhanced error handling"""
    try:
        headers = get_enhanced_headers()
        response = make_request_with_retry(video_url, headers, max_retries=2)
        
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
        
        # Remove duplicates
        seen = set()
        unique_tags = []
        for tag in all_tags:
            if tag not in seen and len(tag.strip()) > 0:
                seen.add(tag)
                unique_tags.append(tag.strip())
        
        logging.info(f"Extracted {len(unique_tags)} tags")
        return unique_tags[:20]
        
    except Exception as e:
        logging.error(f"Error extracting tags: {str(e)}")
        return []

@app.route('/api/extract', methods=['POST'])
def extract_video_info():
    """Extract YouTube video information with enhanced error handling"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'Invalid JSON data'}), 400
            
        video_url = data.get('url')
        
        if not video_url:
            return jsonify({'error': 'No URL provided'}), 400
        
        if not ('youtube.com' in video_url or 'youtu.be' in video_url):
            return jsonify({'error': 'Invalid YouTube URL'}), 400
        
        logging.info(f"Extracting info for URL: {video_url}")
        
        # Get video information
        info = get_youtube_info(video_url)
        video_id = get_video_id_from_url(video_url)
        
        # Try to get tags (less critical, so don't fail if this doesn't work)
        tags = []
        try:
            tags = get_youtube_tags(video_url)
        except Exception as e:
            logging.warning(f"Could not extract tags: {str(e)}")
        
        # Get thumbnail URL
        thumbnail_url = None
        if video_id:
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
            'tags': tags,
            'thumbnail': thumbnail_url,
            'video_id': video_id,
            'extraction_success': {
                'title': info['title'] not in ['Title not found', 'Error', 'All methods failed'],
                'description': info['description'] not in ['Description not found', 'Error', 'Description not available from oEmbed'],
                'tags': len(tags) > 0,
                'thumbnail': thumbnail_url is not None
            }
        }
        
        success_count = sum(result['extraction_success'].values())
        logging.info(f"Extraction completed. Success rate: {success_count}/4 components")
        
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
        'message': 'Enhanced YouTube Info Extractor API is running',
        'features': [
            'Anti-detection headers',
            'Multiple extraction methods',
            'Retry logic with backoff',
            'Rate limiting',
            'Enhanced error handling'
        ]
    })

@app.route('/api/test-extraction')
def test_extraction():
    """Test endpoint to check if extraction is working"""
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    try:
        info = get_youtube_info(test_url)
        return jsonify({
            'test_url': test_url,
            'result': info,
            'status': 'Test completed'
        })
    except Exception as e:
        return jsonify({
            'test_url': test_url,
            'error': str(e),
            'status': 'Test failed'
        }), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = not os.environ.get('FLASK_ENV') == 'production'
    
    print("🚀 Enhanced YouTube Info Extractor API Starting...")
    print(f"📍 Port: {port}")
    print(f"🔧 Debug: {debug_mode}")
    print("\n✨ Enhanced Features:")
    print("  • Anti-detection user agents")
    print("  • Multiple extraction methods")
    print("  • Retry logic with exponential backoff")
    print("  • Enhanced error handling")
    print("  • Reduced rate limiting for production")
    print("\n📋 API Endpoints:")
    print("  POST /api/extract - Extract video info")
    print("  GET  /api/download-thumbnail/<video_id> - Download thumbnail")
    print("  GET  /api/test-extraction - Test extraction")
    print("  GET  /health - Health check")
    print("=" * 50)
    
    app.run(debug=debug_mode, host='0.0.0.0', port=port)