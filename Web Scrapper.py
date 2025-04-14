import os
import json
import asyncio
import aiohttp
import logging
from typing import Dict, List, Optional, Any, Union
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException, Depends, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from bs4 import BeautifulSoup
import anthropic
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Web Scraper with AI Design Suggestions",
    description="Analyzes websites and provides design/SEO recommendations using Claude AI",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/webscraper")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Initialize Anthropic client
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
if not ANTHROPIC_API_KEY:
    logger.warning("ANTHROPIC_API_KEY not set. AI features will not work.")
else:
    anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# Database models
class Website(Base):
    __tablename__ = "websites"
    
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True)
    last_analyzed = Column(DateTime, default=datetime.utcnow)
    html = Column(Text)
    css = Column(Text)
    meta_tags = Column(JSON)
    performance_score = Column(Float, nullable=True)
    
class Analysis(Base):
    __tablename__ = "analyses"
    
    id = Column(Integer, primary_key=True, index=True)
    website_id = Column(Integer, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    raw_ai_response = Column(Text)
    structured_recommendations = Column(JSON)

# Create tables
Base.metadata.create_all(bind=engine)

# Helper function to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Pydantic models for request/response
class UrlInput(BaseModel):
    url: HttpUrl

class AnalysisResponse(BaseModel):
    url: str
    analysis_id: int
    recommendations: Dict[str, List[Dict[str, Any]]]
    performance_score: Optional[float] = None

class HistoryItem(BaseModel):
    url: str
    analysis_id: int
    analyzed_at: datetime
    performance_score: Optional[float] = None

# Web scraping functionality
async def fetch_url(url: str, session: aiohttp.ClientSession) -> Optional[str]:
    """Fetch URL content with error handling and retries"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with session.get(url, timeout=30) as response:
                if response.status == 200:
                    return await response.text()
                logger.warning(f"Failed to fetch {url}, status code: {response.status}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))  # Exponential backoff
                else:
                    return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))
            else:
                return None
    return None

async def fetch_css(css_url: str, session: aiohttp.ClientSession) -> Optional[str]:
    """Fetch CSS content"""
    content = await fetch_url(css_url, session)
    return content

async def scrape_website(url: str) -> Dict[str, Any]:
    """Main scraping function that extracts HTML, CSS, and metadata"""
    async with aiohttp.ClientSession() as session:
        html = await fetch_url(url, session)
        if not html:
            raise ValueError(f"Failed to fetch content from {url}")
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Extract metadata
        meta_tags = {}
        for tag in soup.find_all('meta'):
            name = tag.get('name') or tag.get('property')
            content = tag.get('content')
            if name and content:
                meta_tags[name] = content
        
        # Extract CSS links
        css_links = []
        css_content = []
        for link in soup.find_all('link', rel='stylesheet'):
            href = link.get('href')
            if href:
                if href.startswith('http'):
                    css_url = href
                elif href.startswith('//'):
                    css_url = f"https:{href}"
                else:
                    if url.endswith('/'):
                        css_url = f"{url}{href.lstrip('/')}"
                    else:
                        base_url = url.rsplit('/', 1)[0]
                        css_url = f"{base_url}/{href.lstrip('/')}"
                css_links.append(css_url)
        
        # Fetch CSS content in parallel
        css_tasks = [fetch_css(css_url, session) for css_url in css_links]
        css_results = await asyncio.gather(*css_tasks)
        
        for content in css_results:
            if content:
                css_content.append(content)
        
        return {
            'url': url,
            'html': html,
            'css': '\n'.join(css_content),
            'meta_tags': meta_tags,
            'structure': {
                'heading_count': len(soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])),
                'image_count': len(soup.find_all('img')),
                'link_count': len(soup.find_all('a')),
                'paragraph_count': len(soup.find_all('p')),
                'list_count': len(soup.find_all(['ul', 'ol'])),
                'form_count': len(soup.find_all('form')),
                'table_count': len(soup.find_all('table')),
            }
        }

# Data processing functions
def extract_design_elements(scrape_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and process design elements from scraped data"""
    soup = BeautifulSoup(scrape_data['html'], 'html.parser')
    
    # Basic color extraction (simplified - in practice, use CSS parsing libraries)
    colors = []
    css = scrape_data['css']
    color_defs = [line for line in css.split('\n') if 'color:' in line]
    for color_def in color_defs[:20]:  # Limit to first 20 to avoid overwhelming
        color_parts = color_def.split('color:', 1)
        if len(color_parts) > 1:
            color_value = color_parts[1].split(';')[0].strip()
            if color_value and color_value != 'inherit' and color_value not in colors:
                colors.append(color_value)
    
    # Analyze headings
    headings = {}
    for level in range(1, 7):
        h_tags = soup.find_all(f'h{level}')
        if h_tags:
            headings[f'h{level}'] = {
                'count': len(h_tags),
                'examples': [h.get_text()[:50] for h in h_tags[:3]]  # First 3 examples
            }
    
    # Extract images and check for alt text
    images = soup.find_all('img')
    images_without_alt = [img['src'] for img in images if not img.get('alt')]
    
    # Check for responsive design indicators
    responsive_indicators = {
        'has_viewport_meta': bool(soup.find('meta', attrs={'name': 'viewport'})),
        'has_media_queries': 'media' in scrape_data['css'],
        'has_picture_tag': bool(soup.find('picture')),
        'has_srcset': bool(soup.find('img', srcset=True)),
    }
    
    # SEO elements
    seo_elements = {
        'has_title': bool(soup.find('title')),
        'title_content': soup.find('title').get_text() if soup.find('title') else None,
        'has_description': bool(soup.find('meta', attrs={'name': 'description'})),
        'description_content': soup.find('meta', attrs={'name': 'description'})['content'] 
                              if soup.find('meta', attrs={'name': 'description'}) else None,
        'has_canonical': bool(soup.find('link', attrs={'rel': 'canonical'})),
        'canonical_url': soup.find('link', attrs={'rel': 'canonical'})['href'] 
                        if soup.find('link', attrs={'rel': 'canonical'}) else None,
        'has_og_tags': bool(soup.find('meta', attrs={'property': lambda x: x and x.startswith('og:')})),
        'has_schema_markup': 'application/ld+json' in scrape_data['html'],
    }
    
    return {
        'colors': colors[:10],  # Limit to top 10 colors
        'headings': headings,
        'images_without_alt': images_without_alt[:10],  # Limit to first 10
        'responsive_indicators': responsive_indicators,
        'seo_elements': seo_elements,
        'structure': scrape_data['structure'],
    }

# AI integration functions
def create_design_analysis_prompt(data: Dict[str, Any]) -> str:
    """Create a structured prompt for Claude to analyze website design"""
    design_data = extract_design_elements(data)
    
    prompt = f"""
    Please analyze this website data and provide design and SEO recommendations:
    
    URL: {data['url']}
    
    META INFORMATION:
    {json.dumps(data['meta_tags'], indent=2)}
    
    DESIGN ELEMENTS:
    - Color scheme detected: {', '.join(design_data['colors']) if design_data['colors'] else 'No colors detected'}
    - Heading structure: {json.dumps(design_data['headings'], indent=2)}
    - Images without alt text: {len(design_data['images_without_alt'])}
    
    RESPONSIVE DESIGN:
    - Has viewport meta tag: {design_data['responsive_indicators']['has_viewport_meta']}
    - Uses media queries: {design_data['responsive_indicators']['has_media_queries']}
    - Uses picture tag: {design_data['responsive_indicators']['has_picture_tag']}
    - Uses srcset attribute: {design_data['responsive_indicators']['has_srcset']}
    
    SEO ELEMENTS:
    - Has title: {design_data['seo_elements']['has_title']}
    - Title content: {design_data['seo_elements']['title_content']}
    - Has meta description: {design_data['seo_elements']['has_description']}
    - Description content: {design_data['seo_elements']['description_content']}
    - Has canonical URL: {design_data['seo_elements']['has_canonical']}
    - Has Open Graph tags: {design_data['seo_elements']['has_og_tags']}
    - Has schema markup: {design_data['seo_elements']['has_schema_markup']}
    
    STRUCTURE STATISTICS:
    - Heading count: {design_data['structure']['heading_count']}
    - Image count: {design_data['structure']['image_count']}
    - Link count: {design_data['structure']['link_count']}
    - Paragraph count: {design_data['structure']['paragraph_count']}
    - List count: {design_data['structure']['list_count']}
    - Form count: {design_data['structure']['form_count']}
    - Table count: {design_data['structure']['table_count']}
    
    Please provide detailed recommendations in these categories:
    1. Visual Design (color scheme, typography, layout, whitespace)
    2. Content Structure (headings, readability, information hierarchy)
    3. Responsive Design (mobile-friendliness, adaptability)
    4. SEO Optimization (metadata, keywords, structured data)
    5. Accessibility (alt text, ARIA, keyboard navigation potential issues)
    6. Performance Optimization (potential issues based on structure)
    
    For each recommendation, categorize as:
    - CRITICAL: Must fix, significant impact on usability/performance
    - IMPORTANT: Should fix, moderate impact
    - MINOR: Consider fixing, small impact
    
    Format your response as JSON with this structure:
    {
      "visual_design": [{"severity": "CRITICAL/IMPORTANT/MINOR", "issue": "description", "recommendation": "action"}],
      "content_structure": [{"severity": "CRITICAL/IMPORTANT/MINOR", "issue": "description", "recommendation": "action"}],
      "responsive_design": [{"severity": "CRITICAL/IMPORTANT/MINOR", "issue": "description", "recommendation": "action"}],
      "seo_optimization": [{"severity": "CRITICAL/IMPORTANT/MINOR", "issue": "description", "recommendation": "action"}],
      "accessibility": [{"severity": "CRITICAL/IMPORTANT/MINOR", "issue": "description", "recommendation": "action"}],
      "performance_optimization": [{"severity": "CRITICAL/IMPORTANT/MINOR", "issue": "description", "recommendation": "action"}],
      "overall_score": 0-100,
      "summary": "brief overall assessment"
    }
    """
    return prompt

async def get_ai_recommendations(data: Dict[str, Any]) -> Dict[str, Any]:
    """Get design and SEO recommendations from Claude API"""
    if not ANTHROPIC_API_KEY:
        # Return mock data for testing when API key is not available
        return create_mock_ai_response()
    
    prompt = create_design_analysis_prompt(data)
    
    try:
        message = anthropic_client.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=4000,
            temperature=0.2,
            system="You are an expert web designer and SEO specialist analyzing websites. Provide detailed, actionable recommendations based on best practices.",
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        response_text = message.content[0].text
        
        # Extract JSON from response
        try:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                recommendations = json.loads(json_str)
                return recommendations
            else:
                logger.error("Could not find JSON in AI response")
                return create_mock_ai_response()
        except json.JSONDecodeError:
            logger.error("Failed to parse JSON from AI response")
            return create_mock_ai_response()
            
    except Exception as e:
        logger.error(f"Error getting AI recommendations: {e}")
        return create_mock_ai_response()

def create_mock_ai_response() -> Dict[str, Any]:
    """Create mock AI response for testing"""
    return {
        "visual_design": [
            {"severity": "IMPORTANT", "issue": "Limited color palette", "recommendation": "Expand color scheme for better visual hierarchy"}
        ],
        "content_structure": [
            {"severity": "MINOR", "issue": "Heading structure not optimal", "recommendation": "Ensure proper H1-H6 hierarchy"}
        ],
        "responsive_design": [
            {"severity": "CRITICAL", "issue": "No viewport meta tag", "recommendation": "Add viewport meta tag for mobile responsiveness"}
        ],
        "seo_optimization": [
            {"severity": "IMPORTANT", "issue": "Missing meta descriptions", "recommendation": "Add descriptive meta tags for better SEO"}
        ],
        "accessibility": [
            {"severity": "CRITICAL", "issue": "Images missing alt text", "recommendation": "Add descriptive alt text to all images"}
        ],
        "performance_optimization": [
            {"severity": "MINOR", "issue": "Potential for large images", "recommendation": "Optimize image sizes for faster loading"}
        ],
        "overall_score": 65,
        "summary": "This website needs improvements in mobile responsiveness and accessibility. The visual design and content structure are adequate but could be enhanced."
    }

# API route implementations
@app.post("/analyze", response_model=AnalysisResponse)
async def analyze_website(url_input: UrlInput, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Submit a URL for analysis"""
    # Check if website was recently analyzed (within 24 hours)
    existing_website = db.query(Website).filter(Website.url == str(url_input.url)).first()
    
    if existing_website and (datetime.utcnow() - existing_website.last_analyzed).total_seconds() < 86400:
        # Get the most recent analysis
        latest_analysis = db.query(Analysis).filter(Analysis.website_id == existing_website.id).order_by(Analysis.created_at.desc()).first()
        
        if latest_analysis:
            return AnalysisResponse(
                url=str(url_input.url),
                analysis_id=latest_analysis.id,
                recommendations=latest_analysis.structured_recommendations,
                performance_score=existing_website.performance_score
            )
    
    # Start analysis in background to avoid timeout for long-running operations
    background_tasks.add_task(perform_analysis, str(url_input.url), db)
    
    # Return immediate response
    return AnalysisResponse(
        url=str(url_input.url),
        analysis_id=-1,  # Placeholder, actual ID will be generated during background processing
        recommendations={"status": [{"severity": "INFO", "issue": "Analysis in progress", "recommendation": "Check back in a few minutes"}]},
        performance_score=None
    )

async def perform_analysis(url: str, db: Session):
    """Perform the actual analysis in the background"""
    try:
        # Scrape website
        scrape_data = await scrape_website(url)
        
        # Check if website exists in database
        website = db.query(Website).filter(Website.url == url).first()
        
        if not website:
            # Create new website entry
            website = Website(
                url=url,
                html=scrape_data['html'],
                css=scrape_data['css'],
                meta_tags=scrape_data['meta_tags']
            )
            db.add(website)
            db.commit()
            db.refresh(website)
        else:
            # Update existing website entry
            website.html = scrape_data['html']
            website.css = scrape_data['css']
            website.meta_tags = scrape_data['meta_tags']
            website.last_analyzed = datetime.utcnow()
            db.commit()
            db.refresh(website)
        
        # Get AI recommendations
        recommendations = await get_ai_recommendations(scrape_data)
        
        # Save analysis
        performance_score = recommendations.get('overall_score')
        
        analysis = Analysis(
            website_i
