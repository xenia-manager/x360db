"""
Main script used to scrape game data
"""

# Libraries
import json
import requests
import xml.etree.ElementTree as ET
import re
import time
from datetime import datetime
from config import BASE_GAMES_LIST_JSON_URL, URL_TEMPLATE, MAX_RETRIES, RETRY_DELAY, MIME_TYPE_TO_EXTENSION, UPDATE_DATA, DOWNLOAD_ARTWORK
import os
import sys
from PIL import Image
from io import BytesIO
sys.stdout.reconfigure(encoding='utf-8')

# Fetch base games list from the provided URL
def fetch_base_games_list():
    base_games_list = []
    response = requests.get(BASE_GAMES_LIST_JSON_URL)
    if response.status_code == 200:
        base_games_list = response.json()
    else:
        print(f"Failed to fetch JSON data from the URL, status code: {response.status_code}")
        base_games_list = []
    return base_games_list

# Function to extract numbers from the URL for proper sorting
def extract_number(url):
    # Search for numbers in the URL (e.g., 'screenlg1', 'screenlg12')
    match = re.search(r'screenlg(\d+)', url)
    return int(match.group(1)) if match else 0

# Function to extract the required data from the XML
def extract_game_data(xml_content, titleid, media):
    ns = {
        'a': 'http://www.w3.org/2005/Atom',
        '': 'http://marketplace.xboxlive.com/resource/product/v1'
    }
    
    # Parse the XML content
    root = ET.fromstring(xml_content)

    # Find entry element
    entry = root.find('.//a:entry', namespaces=ns)
    if entry is None:
        print(f"No game found with titleid: {titleid}")
        return None

    # Element for storing game_data
    game_data = {
        'id': titleid,
        'title': {
            'full': None,
            'reduced': None
        },
        'genre': [],
        'developer': None,
        'publisher': None,
        'release_date': None,
        'user_rating': None,
        'description': {
            'full': None,
            'short': None
        },
        'media': media,
        'artwork': {
            'background': None,
            'banner': None,
            'boxart': None,
            'icon': None,
            'gallery': []
        },
        'products': {
            'parent': [],
            'related': []
        }
    }
    
    # Parse full title of the game
    full_title_element = entry.find('.//fullTitle', namespaces=ns)
    full_title = full_title_element.text if full_title_element is not None else None

    # Remove the "Full Game - " prefix from the title if it exists
    if full_title and full_title.startswith("Full Game - "):
        full_title = full_title.replace("Full Game - ", "", 1)
    if full_title:
        full_title = re.sub(r'[^\w\s-]', '', full_title)  # Keep alphanumeric characters, spaces, underscores, and hyphens
        game_data['title']['full'] = full_title.rstrip() # Add full title to game_data

    # Parse reduced title of the game
    reduced_title_element = entry.find('.//reducedTitle', namespaces=ns)
    reduced_title = reduced_title_element.text if reduced_title_element is not None else None
    if reduced_title:
        reduced_title = re.sub(r'[^\w\s-]', '', reduced_title)  # Keep alphanumeric characters, spaces, underscores, and hyphens
        game_data['title']['reduced'] = reduced_title.rstrip()

    # Parse Genre
    excluded_ids = ['3027', '3000'] # Filtered ID's
    genres = entry.findall('.//categories/category', namespaces=ns)
    genreList = []
    for genre in genres:
        genre_id = genre.find('categoryId', namespaces=ns).text
        genre_category_id = genre.find('categorySystemId', namespaces=ns).text
        if genre_category_id == '3000' and genre_id not in excluded_ids: # Check if category ID is 3000 and genre id is not excluded
            genre_name = genre.find('categoryName', namespaces=ns).text
            genreList.append(genre_name)

    game_data['genre'] = sorted(genreList) # Sorted genres alphabetically

    # Parse developer
    developer_element = entry.find('.//developerName', namespaces=ns)
    game_data['developer'] = developer_element.text if developer_element is not None else None

    # Parse publisher
    publisher_element = entry.find('.//publisherName', namespaces=ns)
    game_data['publisher'] = publisher_element.text if publisher_element is not None else None

    # Parse release date
    release_date_element = entry.find('.//globalOriginalReleaseDate', namespaces=ns)
    game_data['release_date'] = datetime.strptime(release_date_element.text, "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%d") if release_date_element is not None else None

    # Parse user rating
    user_rating_element = entry.find('.//userRating', namespaces=ns)
    game_data['user_rating'] = user_rating_element.text if user_rating_element is not None else None
    
    # Parse full description
    full_description_element = entry.find('.//fullDescription', namespaces=ns)
    game_data['description']['full'] = full_description_element.text if full_description_element is not None else None

    # Parse short description
    short_description_element = entry.find('.//reducedDescription', namespaces=ns)
    game_data['description']['short'] = short_description_element.text if short_description_element is not None else None

    # Parse Artwork
    images = entry.findall('.//image', namespaces=ns)
    for image in images:
        fileUrl = image.find('fileUrl', namespaces=ns).text
        imageMediaType = image.find('imageMediaType', namespaces=ns).text
        if imageMediaType == '14':
            relationshipType = image.find('size', namespaces=ns).text
            if relationshipType == '15':
                game_data['artwork']['banner'] = fileUrl
            elif relationshipType == '22':
                game_data['artwork']['background'] = fileUrl
            elif relationshipType == '23':
                game_data['artwork']['boxart'] = fileUrl
            elif relationshipType == '14':
                game_data['artwork']['icon'] = fileUrl
    
    # Checking if the game has artwork, if it doesn't, don't add it to the list of games
    """if game_data['artwork']['boxart'] is None:
        print(f"{game_data['id']} has no boxart. Skipping it")
        return None"""
    
    # Parse Slideshow
    slideshow = entry.find('.//slideShows/slideShow', namespaces=ns)
    if slideshow is not None:
        slideshow_images = slideshow.findall('.//image', namespaces=ns)
        for image in slideshow_images:
            imageUrl = image.find('fileUrl', namespaces=ns).text
            game_data['artwork']['gallery'].append(imageUrl)
    game_data['artwork']['gallery'] = sorted(game_data['artwork']['gallery'], key=extract_number) # Sort the URLs of images for Slideshow
    
    # Parse parent products
    parent_products_element = entry.find('.//parentProducts', namespaces=ns)
    if parent_products_element is not None:
        elements = parent_products_element.findall('.//parentProduct', namespaces=ns)
        if elements is not None:
            for parent_product in elements:
                parent = {
                    'id': None,
                    'title': None,
                }
                parent_id = parent_product.find('parentProductId', namespaces=ns)
                if parent_id is not None:
                    parent['id'] = parent_id.text[-8:].upper() if parent_id is not None else None
                
                parent_title = parent_product.find('parentReducedTitle', namespaces=ns)
                if parent_title is not None:
                    parent['title'] = parent_title.text if parent_title is not None else None

                if parent['id'] != game_data['id']: # Checking if Parent_ID and Game_ID match and if they do, don't add Parent
                    game_data['products']['parent'].append(parent)

    # Parse related products
    related_products_element = entry.find('.//relatedUrls', namespaces=ns)
    if related_products_element is not None:
        elements = related_products_element.findall('.//relatedUrl', namespaces=ns)
        if elements is not None:
            for related_product in elements:
                related_product_url = related_product.find('relatedUrl', namespaces=ns)
                if related_product_url is not None:
                    game_data['products']['related'].append(related_product_url.text)

    return game_data # Returns the parsed game data

# Checks if the image already exists
def find_image(image_name, search_dir):
    # Define the common image formats/extensions you want to check
    image_formats = ['.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp']

    # Loop through all possible formats and check if the image exists
    for fmt in image_formats:
        image_path = os.path.join(search_dir, image_name + fmt)
        if os.path.isfile(image_path):
            return True  # Return the path if the image exists
    return False

# Fallback check for extension of the image
def fallback_with_pil(image_content, image_name):
    """
    Fallback to detect the image format using PIL if MIME type detection fails.
    """
    try:
        image = Image.open(BytesIO(image_content))
        image_format = image.format.lower()  # Convert format to lowercase for consistency
        extension = f'.{image_format}'
        print(f"Using PIL fallback: Detected image format is {image_format}.")
        return extension
    except Exception as e:
        print(f"Failed to detect image format for {image_name} using PIL. Error: {e}")
        return ''  # If detection fails, return an empty extension

# Saves image
def save_image(url, image_name,target_path):
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'})
            content_type = response.headers.get('Content-Type', '')
            if "image" in content_type:
                extension = MIME_TYPE_TO_EXTENSION.get(content_type, '')
                if not extension:
                    extension = fallback_with_pil(response.content, image_name)

                if not extension:
                    print(f"Skipping saving {image_name} as no valid image format was detected.")
                    break

                with open(target_path + extension, 'wb') as f:
                    f.write(response.content)
            else:
                print(f"Failed to fetch the {image_name}")
            break
        except requests.exceptions.RequestException as e:
            print(f"Failed to fetch the {image_name}, status code: {response.status_code} (Attempt {attempt}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES - 1:
                print(f'Retrying in {RETRY_DELAY} seconds...')
                time.sleep(RETRY_DELAY)
    return

# Function that saves game data to it's respective folder
def save_game_data(game_data, json_filename, titleid):
    # Create directory for the game itself
    os.makedirs(f'Database/Xbox Marketplace/{titleid}', exist_ok=True) # Creates a directory for the game

    # Check if the game data has already been scraped
    if not os.path.exists(json_filename):   
        # Save the XML of game as JSON
        with open(f'Database/Xbox Marketplace/{titleid}/{titleid}.json', 'w', encoding='utf-8') as f:
            json.dump(game_data, f, ensure_ascii=False, indent=4)

    # Save necessary Artwork
    if DOWNLOAD_ARTWORK == True:   
        # Background
        if game_data['artwork']['background'] is not None and not find_image("background", f'Database/Xbox Marketplace/{titleid}/'):
            save_image(game_data['artwork']['background'], 'background',f'Database/Xbox Marketplace/{titleid}/background')
        # Banner
        if game_data['artwork']['banner'] is not None and not find_image("banner", f'Database/Xbox Marketplace/{titleid}/'):
            save_image(game_data['artwork']['banner'], 'banner',f'Database/Xbox Marketplace/{titleid}/banner')
        # Boxart
        if game_data['artwork']['boxart'] is not None and not find_image("boxart", f'Database/Xbox Marketplace/{titleid}/'):
            save_image(game_data['artwork']['boxart'], 'boxart',f'Database/Xbox Marketplace/{titleid}/boxart')
        # Icon
        if game_data['artwork']['icon'] is not None and not find_image("icon", f'Database/Xbox Marketplace/{titleid}/'):
            save_image(game_data['artwork']['icon'], 'icon',f'Database/Xbox Marketplace/{titleid}/icon')
        # Slideshow/Gallery
        """if game_data['artwork']['gallery'] is not None and len(game_data['artwork']['gallery']) > 0:
            os.makedirs(f'Database/Xbox Marketplace/{titleid}/Gallery', exist_ok=True) # Creates a directory for the game's slideshow
            for slideshow_image in game_data['artwork']['gallery']:
                if not os.path.exists(f'Database/Xbox Marketplace/{titleid}/Gallery/{os.path.basename(slideshow_image)}'):
                    save_image(slideshow_image, f'Database/Xbox Marketplace/{titleid}/Gallery/{os.path.basename(slideshow_image)}')"""

    # Create smaller entry for game
    small_game_data = {
        'id': game_data['id'],
        'title': None,
        'boxart': None,
        'media_id': []
    }

    # Add title
    if game_data['title']['full'] is not None:
        small_game_data['title'] = game_data['title']['full']
    elif game_data['title']['reduced'] is not None:
        small_game_data['title'] = game_data['title']['reduced']
    
    small_game_data['boxart'] = game_data['artwork']['boxart'] # Boxart URL

    # Adding all of the Media ID's to the entry
    for media in game_data['media']:
        small_game_data['media_id'].append(media['media_id'])
    
    return small_game_data

# Function that goes through "base_games_list" and scrapes data for games
def scrape_game_data(base_games_list):
    output_data = []
    for game in base_games_list:
        titleid = game['titleid']
        #titleid = "4C5307D3" # Test for an entry without boxart
        json_filename = f"Database/Xbox Marketplace/{titleid}/{titleid}.json"

        # Check if the game data has already been scraped
        if os.path.exists(json_filename) and UPDATE_DATA == False:
            print(f"{game['title']} ({titleid}) has already been scraped. Reading data from the file.")
            with open(json_filename, 'r', encoding='utf-8') as file:
                 game_data = json.load(file)
            output_data.append(save_game_data(game_data, json_filename, titleid))
            continue # Continue on with the for loop since we already have the data for this entry
        
        url = URL_TEMPLATE.format(id=titleid) # Replaces the 'id' with the actual id in the API URL
        success = False # This is just to check if it successfuly scraped the data for the game

        # 5 attempts to scrape everything if it fails move onto the next entry
        for attempt in range(MAX_RETRIES): 
            response = requests.get(url)
            if response.status_code == 200:
                success = True
                game_data = extract_game_data(response.content, titleid, game['media'])
                if game_data:
                    print(f"Processing {game_data['title']['full']} ({game_data['id']})")
                    output_data.append(save_game_data(game_data, json_filename, titleid))
                #time.sleep(1)
                break # Exit loop after successful request
            else:
                print(f"Failed to fetch data for titleid: {titleid}, status code: {response.status_code} (Attempt {attempt}/{MAX_RETRIES})")
                if attempt < MAX_RETRIES - 1:  # Avoid sleeping on the last attempt
                    print(f"Retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
        if success == False:
            print(f"Failed to fetch data for titleid: {titleid} after retrying 5 times (status code: {response.status_code})")

    return output_data

# Starting function
if __name__ == "__main__":
    games_list = fetch_base_games_list()
    with open(f'Database/xbox_marketplace_games.json', 'w', encoding='utf-8') as f:
        json.dump(scrape_game_data(games_list), f, ensure_ascii=False, indent=4)