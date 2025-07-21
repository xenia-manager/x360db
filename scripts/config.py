"""
Holds all of the global variables used for the archiver
"""

# Libraries
from dotenv import load_dotenv
import os

# Load environment variables from the .env file
load_dotenv()

# URL of the JSON data - Provide your own data, below is an example of how it should look like
#BASE_GAMES_LIST_JSON_URL = "https://gist.githubusercontent.com/shazzaam7/f5d16a46a0c16dd1b926af2ace3b9155/raw/e0d10bce99784a56a505d8914a151e806fbdfd77/test.json"
BASE_GAMES_LIST_JSON_URL = os.getenv('GAMES_LIST_URL')

# URL template - Used for scraping data from Xbox Marketplace
URL_TEMPLATE = "http://marketplace-xb.xboxlive.com/marketplacecatalog/v1/product/en-US/66ACD000-77FE-1000-9115-D802{id}?bodytypes=1.3&detailview=detaillevel5&pagenum=1&pagesize=1&stores=1&tiers=2.3&offerfilter=1&producttypes=1.5.18.19.20.21.22.23.30.34.37.46.47.61"

# Constants for retrying
MAX_RETRIES = 5  # Number of retry attempts
RETRY_DELAY = 5  # Delay in seconds before retrying

MIME_TYPE_TO_EXTENSION = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/bmp': '.bmp',
    'image/webp': '.webp',
    'image/tiff': '.tiff',
    'image/vnd.microsoft.icon': '.ico'
}

DOWNLOAD_ARTWORK = os.getenv('DOWNLOAD_ARTWORK', False) == "True"
UPDATE_DATA = os.getenv('UPDATE_DATA', False) == "True"