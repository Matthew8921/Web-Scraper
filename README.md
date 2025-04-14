# Web-Scraper
# Async URL Fetcher with Retry Logic

This project is an asynchronous Python script that fetches web page content using `aiohttp`, with built-in retry logic and exponential backoff for handling temporary failures like timeouts or bad HTTP status codes.

## 🔧 Features

- Asynchronous HTTP requests with `aiohttp`
- Retry logic with customizable max retries
- Exponential backoff between retries
- Logs errors and warnings for failed attempts
- Gracefully returns `None` if all retries fail

## 🧪 Example Usage

```python
import aiohttp
import asyncio
from your_module import fetch_with_retries  # Replace with actual function name/path

async def main():
    url = "https://example.com"
    async with aiohttp.ClientSession() as session:
        content = await fetch_with_retries(session, url)
        if content:
            print("Page content received!")
        else:
            print("Failed to fetch the page after retries.")

asyncio.run(main())
📝 Function Overview
python
Copy
Edit
async def fetch_with_retries(session, url):
    ...
session: An aiohttp.ClientSession object

url: The URL to fetch

Returns the page content (str) or None if all attempts fail

📦 Requirements
Python 3.7+

aiohttp

asyncio (standard in Python 3.7+)

Install dependencies:

bash
Copy
Edit
pip install aiohttp
📁 File Structure
bash
Copy
Edit
/project-root
│
├── fetcher.py           # Contains the async retry logic
├── main.py              # Example usage
├── README.md            # You're here!
└── requirements.txt     # Optional: add aiohttp here
✅ To Do
 Add unit tests

 Allow custom backoff strategy

 Support other HTTP methods (POST, PUT, etc.)

💡 Notes
Retry delays increase with each attempt (1s, 2s, etc.)

On HTTP error or exception, it retries up to 3 times before giving u
