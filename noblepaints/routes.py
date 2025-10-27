from noblepaints import app,mail,db
from datetime import datetime
import time
from io import BytesIO
import os
from threading import RLock
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Tuple
from sqlalchemy import insert, event
from sqlalchemy.orm import noload
import pathlib
import requests
from flask import render_template,request,jsonify,send_from_directory,redirect,url_for,flash,session,abort,make_response,Response, send_file,json
from noblepaints.models import Category,Product,Catalog,TechnicalDatasheet,Post,Certificate,Approval,Upload,SocialSchema,Social
from sqlalchemy import desc
from urllib.parse import urlencode
from flask_httpauth import HTTPBasicAuth
from flask_mail import Message

# Initialize database tables once at startup
with app.app_context():
    db.create_all()
    
    # Add database indexes for better performance
    try:
        # Create indexes if they don't exist (SQLite compatible)
        db.engine.execute('CREATE INDEX IF NOT EXISTS idx_category_id ON category(id)')
        db.engine.execute('CREATE INDEX IF NOT EXISTS idx_product_category ON product(category)')
        db.engine.execute('CREATE INDEX IF NOT EXISTS idx_product_lang ON product(lang)')
        db.engine.execute('CREATE INDEX IF NOT EXISTS idx_catalog_lang ON catalog(lang)')
        db.engine.execute('CREATE INDEX IF NOT EXISTS idx_catalog_category ON catalog(category)')
        print("Database indexes created/verified for better performance")
    except Exception as e:
        print(f"Note: Could not create indexes (may already exist): {e}")

    # Seed default approvals logos if none exist to keep legacy content visible
    try:
        if db.session.query(Approval).count() == 0:
            default_approvals = [
                {'title': 'Noble Paints Logo', 'img': '/static/images/logo1.png'},
                {'title': 'Zamil Group', 'img': '/static/images/24b0ee83-fea6-44ad-9ed4-1a2bb3543c9d.png'},
                {'title': 'Royal Commission', 'img': '/static/images/65793.jpg'},
                {'title': 'IQT Hub', 'img': '/static/images/IQT__HUB_400x4001.png'},
                {'title': 'Ministry of Housing', 'img': '/static/images/--.png'},
                {'title': 'Saudi Railways', 'img': '/static/images/Client-logo-14-.png'},
                {'title': 'SABIC', 'img': '/static/images/Logo_of_Sabic.svg.png'},
                {'title': 'Ministry of Health', 'img': '/static/images/images.jpg'},
            ]
            for item in default_approvals:
                db.session.add(Approval(
                    title=item['title'],
                    description='',
                    img=item['img'],
                    link=''
                ))
            db.session.commit()
            print("Seeded default approvals data")
    except Exception as seed_error:
        print(f"Could not seed default approvals: {seed_error}")
    # Seed default certificates images if none exist
    try:
        if db.session.query(Certificate).count() == 0:
            default_certificates = [
                '/static/images/Screenshot41.png',
                '/static/images/Screenshot42.png',
                '/static/images/Screenshot43.png',
                '/static/images/Screenshot44.png',
                '/static/images/Screenshot45.png',
                '/static/images/Screenshot46.png',
                '/static/images/Screenshot47.png',
                '/static/images/Screenshot48.png',
                '/static/images/Screenshot49.png',
                '/static/images/Screenshot50.png',
                '/static/images/Screenshot51.png',
                '/static/images/Screenshot52.png',
                '/static/images/Screenshot53.png',
                '/static/images/Screenshot54.png',
                '/static/images/Screenshot55.png',
                '/static/images/Screenshot56.png',
                '/static/images/Screenshot57.png',
                '/static/images/Screenshot58.png',
                '/static/images/Screenshot59.png',
                '/static/images/Screenshot60.png',
                '/static/images/Screenshot61.png',
                '/static/images/Screenshot62.png',
                '/static/images/Screenshot63.png',
                '/static/images/Screenshot64.png',
            ]
            for path in default_certificates:
                db.session.add(Certificate(
                    title='',
                    description='',
                    img=path,
                    link=''
                ))
            db.session.commit()
            print("Seeded default certificates data")
    except Exception as cert_seed_error:
        print(f"Could not seed default certificates: {cert_seed_error}")

# Cache will be pre-warmed after function definitions

#pip install Flask-HTTPAuth
#pip install email_validator
#pip install flask_bcrypt
#pip install flask_login
#pip install flask-mail
#pip install itsdangerous==2.0.1
#Authlib==0.14.3

#os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
app.secret_key = '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'

# ----------------------------------------------------------------------------
# Simple in-memory caching helpers
# ----------------------------------------------------------------------------

_data_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = RLock()


def _cache_get(key: str) -> Tuple[Any, float]:
    """Return cached data and timestamp if still valid, otherwise (None, 0)."""
    with _cache_lock:
        entry = _data_cache.get(key)
        if not entry:
            return None, 0.0

        ttl = entry.get('ttl', 0)
        timestamp = entry.get('timestamp', 0.0)
        if ttl and (time.time() - timestamp) > ttl:
            _data_cache.pop(key, None)
            return None, 0.0

        return entry.get('data'), timestamp


def _cache_set(key: str, data: Any, ttl: int) -> Tuple[Any, float]:
    """Store data in cache with TTL and return (data, timestamp)."""
    now = time.time()
    with _cache_lock:
        _data_cache[key] = {'data': data, 'timestamp': now, 'ttl': ttl}
    return data, now


def _cache_invalidate(*prefixes: str) -> None:
    """Invalidate cache entries. If no prefixes provided clear everything."""
    with _cache_lock:
        if not prefixes:
            _data_cache.clear()
            return

        keys = list(_data_cache.keys())
        for key in keys:
            if any(key.startswith(prefix) for prefix in prefixes):
                _data_cache.pop(key, None)


def _to_namespace(data: Iterable[Dict[str, Any]]) -> List[SimpleNamespace]:
    """Convert dictionaries to SimpleNamespace for template attribute access."""
    return [SimpleNamespace(**item) for item in data]


# ----------------------------------------------------------------------------
# Model specific caching helpers
# ----------------------------------------------------------------------------

PRODUCT_CACHE_TTL = 600  # 10 minutes keeps responses fresh while reducing DB hits
SOCIAL_CACHE_TTL = 900   # 15 minutes for rarely-changing social icons


def _serialize_product(product: Product) -> Dict[str, Any]:
    return {
        'id': product.id,
        'img': product.img or '',
        'name': product.name or '',
        'desc': product.desc or '',
        'country': product.country or '',
        'category': product.category or '',
        'lang': product.lang or '',
        'datasheet': product.datasheet or ''
    }


def _get_products_payload(lang: str) -> Tuple[List[Dict[str, Any]], float]:
    """Return serialized products for the requested language with sensible fallbacks."""

    normalized_lang = (lang or 'en').lower()
    if normalized_lang not in {'en', 'ar'}:
        normalized_lang = 'en'

    cache_key = f'products:{normalized_lang}'
    cached, timestamp = _cache_get(cache_key)
    if cached is not None:
        return cached, timestamp

    base_query = db.session.query(Product).options(noload('*'))

    if normalized_lang == 'ar':
        arabic_products = base_query.filter(Product.lang == 'ar').all()
        fallback_products = base_query.filter(Product.lang == 'en').all()

        # Keep Arabic entries first and append English ones that do not already exist.
        seen_ids = {product.id for product in arabic_products}
        combined_products = list(arabic_products)
        combined_products.extend(
            product for product in fallback_products
            if product.id not in seen_ids
        )
        products = combined_products
    else:
        products = base_query.filter(Product.lang == normalized_lang).all()

    serialized = [_serialize_product(product) for product in products]
    return _cache_set(cache_key, serialized, PRODUCT_CACHE_TTL)


def get_cached_products(lang: str, as_objects: bool = False) -> Tuple[List[Any], float]:
    """Return cached products in dict or namespace form with timestamp."""
    data, timestamp = _get_products_payload(lang)
    if as_objects:
        return _to_namespace(data), timestamp
    return data, timestamp


def get_cached_social_icons(as_objects: bool = False) -> Tuple[List[Any], float]:
    cache_key = 'social-icons'
    cached, timestamp = _cache_get(cache_key)
    if cached is None:
        icons = db.session.query(Social).options(noload('*')).all()
        schema = SocialSchema(many=True)
        serialized = schema.dump(icons)
        cached, timestamp = _cache_set(cache_key, serialized, SOCIAL_CACHE_TTL)

    if as_objects:
        return _to_namespace(cached), timestamp
    return cached, timestamp


# ----------------------------------------------------------------------------
# Automatic cache invalidation hooks
# ----------------------------------------------------------------------------


@event.listens_for(Product, 'after_insert')
@event.listens_for(Product, 'after_update')
@event.listens_for(Product, 'after_delete')
def _clear_product_cache(*_) -> None:
    _cache_invalidate('products:')


@event.listens_for(Category, 'after_insert')
@event.listens_for(Category, 'after_update')
@event.listens_for(Category, 'after_delete')
def _clear_category_cache(*_) -> None:
    invalidate_categories_cache()


@event.listens_for(Social, 'after_insert')
@event.listens_for(Social, 'after_update')
@event.listens_for(Social, 'after_delete')
def _clear_social_cache(*_) -> None:
    _cache_invalidate('social-icons')

auth = HTTPBasicAuth()

@auth.verify_password
def verify_password(username,password):
    if password == '526af4fbd93bc393a6392db7' and username == 'admin':
        return True
 
# create download function for download files
@app.route('/download/<upload_id>')
def download(upload_id):
    upload = Upload.query.filter_by(id=upload_id).first()
    return send_file(BytesIO(upload.data),
                     download_name=upload.filename, as_attachment=True)

@app.route('/show/<upload_id>/')
def show_static_pdf(upload_id):
    upload = Upload.query.filter_by(id=upload_id).first()
    pdf = BytesIO()
    pdf.write(upload.data)
    pdf.seek(0)
    return send_file(pdf, as_attachment=False, mimetype='application/pdf')

@app.route('/home')
@app.route('/en/')
@app.route('/ar/')
@app.route('/')
def home_page():
    lang = 'ar' if request.path.startswith('/ar') else 'en'
    products, _ = get_cached_products(lang, as_objects=True)
    featured_products = products[:10]
    return render_template('index.html', products=featured_products)

@app.route('/ral-colors/')
def ralColors():
    return render_template('RalColors.html')

@app.route('/video')
def get_video():  
    def generate():
        with open("/flask/noblepaints/static/videos/1.mp4", "rb") as f:
            data = f.read(1024)
            while data:
                yield data
                data = f.read(1024)

    return Response(generate(), mimetype="video/mp4", headers={"Accept-Ranges": "bytes"})

@app.route('/sendC/',methods=["POST","GET"])
def sendC():
    data = request.get_json()  
    print(data["type"],data["name"],data["phone"])
    msg = Message(data["type"],sender = "noreply@demo.com",recipients = ["info@noblepaints.com.sa"])
    msg.body = f'''
    :Noble Paints Customers:\n
    Name: {data["name"]}\n
    Company Name: {data["comp"]}\n
    Phone: {data["phone"]}\n
    Message: {data["message"]}\n
    '''
    mail.send(msg)

@app.route('/about/')
def about_page():  
        return render_template('about.html')

@app.route('/calculator/')
def calculator_page():  
        return render_template('calculator.html')

@app.route('/socialMedia/')
def socialMedia_page():  
        return render_template('social.html')

@app.route('/products/')
def products_page():  
        return render_template('products.html')

@app.route('/product/')
def product_page():
        id = request.args.get('id')  
        product = db.session.query(Product).filter(Product.id==id).first()
        if not product:
            abort(404)
        # Get similar products in same category, excluding current product
        similar = db.session.query(Product).filter(
            Product.category==product.category,
            Product.id != id
        ).limit(6).all()
        return render_template('product.html', product=product, similar=similar)

@app.route('/FindStore/')
def locations_page():  
        return render_template('locations.html')

@app.route('/colors/')
def colors_page():  
        return render_template('colors.html', template='colors')

@app.route('/contact/')
def contact_page():  
        return render_template('contact.html')

# Categories cache - simple in-memory cache for categories
categories_cache = {
    'data': None,
    'timestamp': 0,  # Force refresh on next load
    'ttl': 300  # Cache for 5 minutes
}


def invalidate_categories_cache() -> None:
    categories_cache['data'] = None
    categories_cache['timestamp'] = 0

def get_cached_categories():
    """Get categories with caching to reduce database load - OPTIMIZED VERSION"""
    current_time = time.time()
    
    # Check if cache is valid
    if (categories_cache['data'] is not None and 
        current_time - categories_cache['timestamp'] < categories_cache['ttl']):
        print(f"Returning cached categories: {len(categories_cache['data'])} items")
        return categories_cache['data']
    
    try:
        print("Fetching categories from database...")
        # SUPER OPTIMIZED query: include img field for proper image loading
        categories = db.session.query(
            Category.id, 
            Category.name, 
            Category.nameArabic, 
            Category.desc,
            Category.img
        ).filter(
            Category.id != 29
        ).order_by(Category.id).all()
        
        print(f"Database returned {len(categories)} categories")
        
        # Convert to lightweight dictionary format
        categories_list = []
        for cat in categories:
            # Use img directly from query result - debug what we're getting
            img_url = cat.img if cat.img else '/static/images/default.png'
            print(f"Category {cat.id} ({cat.name}): img = '{cat.img}'")  # Debug line
                
            categories_list.append({
                'id': cat.id,
                'name': cat.name or 'Untitled',
                'nameArabic': cat.nameArabic or cat.name or 'غير محدد',
                'desc': (cat.desc or 'No description')[:200] + ('...' if len(cat.desc or '') > 200 else ''),  # Truncate long descriptions
                'img': img_url  # Use actual image URL
            })
        
        # Update cache
        categories_cache['data'] = categories_list
        categories_cache['timestamp'] = current_time
        
        print(f"Cached {len(categories_list)} categories with optimized data")
        return categories_list
        
    except Exception as e:
        print(f"Database error in get_cached_categories: {e}")
        # Return cached data if available, even if stale
        if categories_cache['data'] is not None:
            print(f"Returning stale cached data: {len(categories_cache['data'])} items")
            return categories_cache['data']
        
        # Last resort: return minimal structure
        print("No cached data available, returning minimal fallback")
        return [
            {'id': 0, 'name': 'Loading...', 'nameArabic': 'جاري التحميل...', 'desc': 'Please wait', 'img': '/static/images/loading.gif'}
        ]


def get_categories_for_template() -> List[SimpleNamespace]:
    return _to_namespace(get_cached_categories())

@app.route('/categories/')
def categories_page():
    """Categories page - optimized hybrid approach with fallback"""
    try:
        # Get categories with minimal data first for fast page load
        categories = get_cached_categories()
        print(f"Categories page: got {len(categories)} categories for fallback")
        
        # Return page immediately with cached data - AJAX will enhance if needed
        response = make_response(render_template('categories.html', categories=categories, template='cats'))
        
        # Add caching headers for better performance
        response.headers['Cache-Control'] = 'public, max-age=300'  # Cache for 5 minutes
        response.headers['ETag'] = f'categories-{len(categories)}-{int(categories_cache["timestamp"])}'
        
        return response
        
    except Exception as e:
        print(f"Error in categories_page: {e}")
        # Return minimal page structure if there's an error
        response = make_response(render_template('categories.html', categories=[], template='cats'))
        response.headers['Cache-Control'] = 'public, max-age=60'  # Shorter cache for errors
        return response

@app.route('/api/categories/')
def api_categories():
    """API endpoint for loading categories asynchronously - OPTIMIZED"""
    try:
        # Check if client has cached version using ETag
        if_none_match = request.headers.get('If-None-Match')
        current_etag = f'api-categories-{int(categories_cache.get("timestamp", 0))}'
        
        if if_none_match == current_etag:
            # Client has current version, return 304 Not Modified
            return '', 304
        
        categories = get_cached_categories()
        
        # For API, include actual image URLs for better UX
        enhanced_categories = []
        for cat in categories:
            enhanced_cat = cat.copy()
            # Only load actual images via API to avoid blocking initial page load
            if cat['id'] != 0:  # Skip loading indicator
                try:
                    # Get actual image from database only when requested via API
                    actual_cat = db.session.query(Category.img).filter(Category.id == cat['id']).first()
                    enhanced_cat['img'] = actual_cat.img if actual_cat and actual_cat.img else '/static/images/default-category.jpg'
                except:
                    enhanced_cat['img'] = '/static/images/default-category.jpg'
            enhanced_categories.append(enhanced_cat)
        
        response_data = {
            'categories': enhanced_categories,
            'count': len(enhanced_categories),
            'cached': categories_cache['timestamp'] > 0,
            'success': True,
            'timestamp': int(time.time())
        }
        
        response = jsonify(response_data)
        # Add caching headers
        response.headers['Cache-Control'] = 'public, max-age=300'
        response.headers['ETag'] = current_etag
        
        print(f"API Categories: Returning {len(enhanced_categories)} categories with images")
        return response
        
    except Exception as e:
        print(f"Error in api_categories: {e}")
        return jsonify({
            'categories': [],
            'count': 0,
            'cached': False,
            'success': False,
            'error': 'Failed to load categories',
            'timestamp': int(time.time())
        }), 500

@app.route('/news/')
def news_page():  
    page = request.args.get('page')
    type = request.args.get('type') 
    lang = request.args.get('lang') 
    if(page !='' and page !='undefined' and page != None):
        if(type !='' and type !='undefined' and type != None):
            return render_template('news.html',news = db.session.query(Post).filter(Post.type==type,Post.lang==lang),page=page,type=type)
        else:
            return render_template('news.html',news = db.session.query(Post).filter(Post.lang==lang).all(),page=page,type=type)
    else:
        if(type !='' and type !='undefined' and type != None):
            return render_template('news.html',news = db.session.query(Post).filter(Post.type==type,Post.lang==lang),page='1',type=type)
        else:
            return render_template('news.html',news = db.session.query(Post).filter(Post.lang==lang).all(),page='1',type=type)

@app.route('/certificates/')
def certificates_page():  
    try:
        lang = (request.args.get('lang') or session.get('certificates_lang') or 'en').lower()
        if lang not in {'en', 'ar'}:
            lang = 'en'
        session['certificates_lang'] = lang

        items = db.session.query(Certificate).order_by(desc(Certificate.id)).all()

        certificate_cards = []
        for item in items:
            certificate_cards.append({
                'id': item.id,
                'title': item.title or '',
                'description': item.description or '',
                'image_url': item.img or '/static/images/default.png',
                'link': item.link or ''
            })

        return render_template(
            'certificates.html',
            items=certificate_cards,
            lang=lang,
            template='certificates'
        )
    except Exception as e:
        print(f"Error in certificates page: {e}")
        return "Internal server error in certificates page", 500

@app.route('/approvals/')
def approvals_page():  
    try:
        lang = (request.args.get('lang') or session.get('approvals_lang') or 'en').lower()
        if lang not in {'en', 'ar'}:
            lang = 'en'
        session['approvals_lang'] = lang

        items = db.session.query(Approval).order_by(desc(Approval.id)).all()

        approval_cards = []
        for item in items:
            approval_cards.append({
                'id': item.id,
                'title': item.title or '',
                'description': item.description or '',
                'image_url': item.img or '/static/images/default.png',
                'link': item.link or ''
            })

        return render_template(
            'approvals.html',
            items=approval_cards,
            lang=lang,
            template='approvals'
        )
    except Exception as e:
        print(f"Error in approvals page: {e}")
        return "Internal server error in approvals page", 500

@app.route('/news/<id>/')
def news_page_details(id):  
    lang = request.args.get('lang') 
    # Single query to get the post
    post = db.session.query(Post).filter(Post.id==id).first()
    if not post:
        abort(404)
    
    # Update views efficiently
    if post.views:
        post.views = int(post.views) + 1
    else:
        post.views = '1'
    db.session.commit()
    
    # Optimize queries with limits
    latest = db.session.query(Post).filter(Post.lang==lang).limit(5).all()
    allNews = Post.query.order_by(desc(Post.views)).filter(Post.lang==lang).limit(10).all()
    
    return render_template('news_details.html',
        post=post,
        latest=latest,
        allNews=allNews
    )

@app.route('/products/<cat>/')
def products_cat_page(cat):
        category = db.session.query(Product).filter(Product.category==cat)
        if category:
            return render_template('products_cat.html',
            items=category.all(),
            title=cat,                   
            )
        else:
            return render_template('products_cat.html',
            title="kids",                    
            )

@app.route('/productsSearch/')
def productsSearch_page_filter_none():
    category = request.args.get('category')
    page = request.args.get('page')
    search = request.args.get('search')
    country = request.args.get('country')
    lang = request.args.get('lang', 'en')

    if lang not in {'en', 'ar'}:
        lang = 'en'

    def _normalize(value: Any, default: str, *, treat_as_empty: bool = False) -> str:
        """Normalize filter values that may come from JS as "null"/"undefined"."""
        if value is None:
            return default

        value_str = str(value).strip()
        lowered = value_str.lower()

        invalid_values = {'', 'null', 'none', 'undefined'}
        if lowered in invalid_values:
            return '' if treat_as_empty else default

        return value_str

    normalized_category = _normalize(category, 'All')
    normalized_country = _normalize(country, 'All')
    normalized_search = _normalize(search, '', treat_as_empty=True)

    try:
        current_page = max(int(page), 1)
    except (TypeError, ValueError):
        current_page = 1
    page_value = str(current_page)

    products, _ = get_cached_products(lang, as_objects=True)

    search_lower = normalized_search.lower()
    results = []
    for product in products:
        product_category = (product.category or '').strip()
        product_country = (product.country or '').strip()
        product_name = (product.name or '').strip()

        if normalized_category not in {'All', '', 'null'} and product_category != normalized_category:
            continue
        if normalized_country not in {'All', '', 'null'} and product_country != normalized_country:
            continue
        if search_lower and search_lower not in product_name.lower():
            continue
        results.append(product)

    categories = get_categories_for_template()

    return render_template(
        'productsSearch.html',
        items=results,
        page=page_value,
        category=normalized_category if normalized_category else 'All',
        search=normalized_search,
        country=normalized_country if normalized_country else 'All',
        categories=categories,
        template='products'
    )


@app.route('/catalogs/')
def catalogs_page_filter_none():  
    try:
        # Resolve filters with sane defaults
        lang = (request.args.get('lang') or session.get('preferred_lang') or 'en').lower()
        if lang not in {'en', 'ar'}:
            lang = 'en'
        session['preferred_lang'] = lang

        cat = request.args.get('category', 'All')
        search = (request.args.get('search') or '').strip()
        country = request.args.get('country', 'All')

        try:
            page = max(int(request.args.get('page', 1)), 1)
        except (ValueError, TypeError):
            page = 1

        items_per_page = 12

        query = db.session.query(Catalog).filter(Catalog.lang == lang)

        if cat and cat not in {'All', 'null', 'None'}:
            query = query.filter(Catalog.category == str(cat))

        if country and country not in {'All', 'null', 'None'}:
            query = query.filter(Catalog.country == country)

        if search:
            query = query.filter(Catalog.name.ilike(f'%{search}%'))

        total_items = query.count()
        offset = (page - 1) * items_per_page
        items = query.order_by(desc(Catalog.id)).offset(offset).limit(items_per_page).all()

        categories = db.session.query(Category).order_by(Category.id).all()
        category_map = {str(c.id): c for c in categories}

        upload_ids = {
            str(item.link)
            for item in items
            if item.link and str(item.link).isdigit()
        }
        uploads = {}
        if upload_ids:
            uploads = {
                str(u.id): u
                for u in db.session.query(Upload).filter(Upload.id.in_(upload_ids)).all()
            }

        catalog_cards = []
        for item in items:
            link_value = item.link or ''
            download_url = None
            download_label = None
            if link_value:
                if str(link_value).isdigit() and str(link_value) in uploads:
                    download_url = url_for('download', upload_id=link_value)
                    download_label = uploads[str(link_value)].filename or item.name
                else:
                    download_url = link_value
                    download_label = item.name

            category_obj = category_map.get(str(item.category)) if item.category is not None else None

            catalog_cards.append({
                'id': item.id,
                'name': item.name or 'Untitled catalog',
                'image_url': item.img or '/static/images/default-catalog.jpg',
                'category_id': str(item.category) if item.category is not None else '',
                'category_name': category_obj.name if category_obj else (item.category or 'Uncategorized'),
                'country': item.country or 'All',
                'lang': item.lang,
                'download_url': download_url,
                'download_label': download_label,
            })

        distinct_countries = {
            value for (value,) in db.session.query(Catalog.country)
            .filter(Catalog.lang == lang).filter(Catalog.country.isnot(None)).distinct().all()
            if value
        }
        country_options = ['All'] + sorted(distinct_countries - {'All'}) if distinct_countries else ['All']

        total_pages = max((total_items + items_per_page - 1) // items_per_page, 1)

        base_params = {
            'category': cat or 'All',
            'search': search,
            'country': country or 'All',
            'lang': lang
        }
        query_without_page = urlencode(base_params)

        return render_template(
            'catalogs.html',
            items=catalog_cards,
            total_items=total_items,
            page=page,
            total_pages=total_pages,
            category=cat,
            search=search,
            country=country,
            lang=lang,
            categories=categories,
            country_options=country_options,
            template='catalogs',
            items_per_page=items_per_page,
            query_without_page=query_without_page
        )
    except Exception as e:
        print(f"Error in catalogs route: {e}")
        return "Internal server error in catalogs page", 500


@app.route('/TechnicalDatasheets/')
def TechnicalDatasheets_page_filter_none():  
    try:
        # Get filter parameters with proper defaults and validation
        cat = request.args.get('category', 'All')
        search = request.args.get('search', '').strip()
        country = request.args.get('country', 'All')
        lang = 'en'
        
        # Handle page parameter safely
        try:
            page = int(request.args.get('page', 1))
            if page < 1:
                page = 1
        except (ValueError, TypeError):
            page = 1
        
        # Build base query for Products (TechnicalDatasheets uses Product model)
        query = db.session.query(Product).filter(Product.lang == lang)
        
        # Apply filters conditionally
        if cat and cat != 'All' and cat != 'null':
            query = query.filter(Product.category == cat)
        
        if country and country != 'All' and country != 'null':
            query = query.filter(Product.country == country)
            
        if search:
            query = query.filter(Product.name.contains(search))
        
        # Get total count for pagination
        total_items = query.count()
        
        # Apply pagination - only get items for current page
        items_per_page = 12
        offset = (page - 1) * items_per_page
        items = query.offset(offset).limit(items_per_page).all()
        
        # Get categories once
        categories = db.session.query(Category).all()
        
        return render_template('TechnicalDatasheets.html',
            items=items,
            total_items=total_items,
            page=str(page),
            category=cat,
            search=search,
            country=country,
            categories=categories,
            items_per_page=items_per_page,
            template='technical'
        )
    except Exception as e:
        # Fallback to basic functionality if something goes wrong
        print(f"Error in TechnicalDatasheets route: {e}")
        try:
            # Simple fallback query
            items = db.session.query(Product).filter(Product.lang == 'en').limit(12).all()
            categories = db.session.query(Category).all()
            return render_template('TechnicalDatasheets.html',
                items=items,
                total_items=len(items),
                page="1",
                category="All", 
                search="",
                country="All",
                categories=categories,
                items_per_page=12,
                template='technical'
            )
        except Exception as fallback_error:
            print(f"TechnicalDatasheets fallback error: {fallback_error}")
            return "Internal server error in TechnicalDatasheets page", 500


################################################


@app.route('/ControlPanel/socialIcons/')
@auth.login_required
def cpanel_socialIcons():
    page = request.args.get('page')
    show = request.args.get('show')
    if show:
        return render_template('cpanel_socialIcons.html',socialIcons = db.session.query(Social).all(),page=page,show=show)
    else:
        return render_template('cpanel_socialIcons.html',socialIcons = db.session.query(Social).all(),page=page,show='10')

@app.route('/ControlPanel/socialIcons/add/',methods=['POST','GET'])
@auth.login_required
def socialIcons_add():
    data = request.get_json()
    link = data['link']
    icon = data['icon']
    s1 = Social(link=link,icon=icon)
    db.session.add(s1)
    db.session.commit()
    return 'True'


@app.route('/ControlPanel/socialIcons/edit/<id>/',methods=['POST','GET'])
@auth.login_required
def socialIcons_edit(id):
    data = request.get_json()
    link = data['link']
    icon = data['icon']
    query = db.session.query(Social).filter(Social.id==id).first()
    if link!='' and link!=None and link!='undefined':
        query.link = link
    if icon!='' and icon!=None and icon!='undefined':
        query.icon = icon
    db.session.commit()


@app.route('/ControlPanel/socialIcons/del/<id>/')
@auth.login_required
def socialIcons_del(id):
    db.session.delete(db.session.query(Social).filter(Social.id==id).first())
    db.session.commit()

###################################################

@app.route('/ControlPanel/news/')
@auth.login_required
def cpanel_news():
    page = request.args.get('page')
    show = request.args.get('show')
    lang = request.args.get('lang')
    if show:
        return render_template('cpanel_news.html',news = db.session.query(Post).filter(Post.lang==lang).all(),page=page,show=show)
    else:
        return render_template('cpanel_news.html',news = db.session.query(Post).filter(Post.lang==lang).all(),page=page,show='10')


@app.route('/ControlPanel/news/add/',methods=['POST','GET'])
@auth.login_required
def news_add():
    data = request.get_json()
    title = data['title']
    img = data['img']
    description = data['description']
    date = data['date']
    lang = data['lang']
    s1 = Post(title=title,img=img,description=description,lang=lang,date=date)
    db.session.add(s1)
    db.session.commit()
    return 'True'


@app.route('/ControlPanel/news/edit/<id>/',methods=['POST','GET'])
@auth.login_required
def news_edit(id):
    data = request.get_json()
    title = data['title']
    lang = data['lang']
    img = data['img']
    date = data['date']
    description = data['description']
    date = data['date']
    query = db.session.query(Post).filter(Post.id==id).first()
    if title!='' and title!=None and title!='undefined':
        query.title = title
    if description!='' and description!=None and description!='undefined':
        query.description = description
    if date!='' and date!=None and date!='undefined':
        query.date = date
    if img!='' and img!=None and img!='undefined':
        query.img = img
    if lang!='' and lang!=None and lang!='undefined':
        query.lang = lang
    if date!='' and date!=None and date!='undefined':
        query.date = date
    db.session.commit()


@app.route('/ControlPanel/news/del/<id>/')
@auth.login_required
def news_del(id):
    db.session.delete(db.session.query(Post).filter(Post.id==id).first())
    db.session.commit()

################################################


@app.route('/ControlPanel/certificates/')
@auth.login_required
def cpanel_certificates():
    page = request.args.get('page')
    show = request.args.get('show')
    if show:
        return render_template('cpanel_certificates.html',certificates = db.session.query(Certificate).all(),page=page,show=show)
    else:
        return render_template('cpanel_certificates.html',certificates = db.session.query(Certificate).all(),page=page,show='10')


@app.route('/ControlPanel/certificates/add/',methods=['POST','GET'])
@auth.login_required
def certificates_add():
    data = request.get_json()
    title = data['title']
    img = data['img']
    description = data['description']
    link = data['link']
    s1 = Certificate(title=title,img=img,description=description,link=link)
    db.session.add(s1)
    db.session.commit()
    return 'True'


@app.route('/ControlPanel/certificates/edit/<id>/',methods=['POST','GET'])
@auth.login_required
def certificates_edit(id):
    data = request.get_json()
    title = data['title']
    img = data['img']
    description = data['description']
    link = data['link']
    query = db.session.query(Certificate).filter(Certificate.id==id).first()
    if title!='' and title!=None and title!='undefined':
        query.title = title
    if description!='' and description!=None and description!='undefined':
        query.description = description
    if link!='' and link!=None and link!='undefined':
        query.link = link
    if img!='' and img!=None and img!='undefined':
        query.img = img
    db.session.commit()


@app.route('/ControlPanel/certificates/del/<id>/')
@auth.login_required
def certificates_del(id):
    db.session.delete(db.session.query(Certificate).filter(Certificate.id==id).first())
    db.session.commit()

###############################################


@app.route('/ControlPanel/approvals/')
@auth.login_required
def cpanel_approvals():
    page = request.args.get('page')
    show = request.args.get('show')
    lang = request.args.get('lang', session.get('preferred_admin_lang', 'en')).lower()
    if lang not in {'en', 'ar'}:
        lang = 'en'
    session['preferred_admin_lang'] = lang

    approvals_query = db.session.query(Approval).order_by(desc(Approval.id))

    if not page:
        page = '1'
    if not show:
        show = '10'

    return render_template(
        'cpanel_approvals.html',
        approvals=approvals_query.all(),
        page=page,
        show=show,
        lang=lang
    )


@app.route('/ControlPanel/approvals/add/',methods=['POST','GET'])
@auth.login_required
def approvals_add():
    data = request.get_json()
    title = data['title']
    img = data['img']
    description = data['description']
    link = data['link']
    s1 = Approval(title=title,img=img,description=description,link=link)
    db.session.add(s1)
    db.session.commit()
    return 'True'


@app.route('/ControlPanel/approvals/edit/<id>/',methods=['POST','GET'])
@auth.login_required
def approvals_edit(id):
    data = request.get_json()
    title = data['title']
    img = data['img']
    description = data['description']
    link = data['link']
    query = db.session.query(Approval).filter(Approval.id==id).first()
    if title!='' and title!=None and title!='undefined':
        query.title = title
    if description!='' and description!=None and description!='undefined':
        query.description = description
    if link!='' and link!=None and link!='undefined':
        query.link = link
    if img!='' and img!=None and img!='undefined':
        query.img = img
    db.session.commit()


@app.route('/ControlPanel/approvals/del/<id>/')
@auth.login_required
def approvals_del(id):
    db.session.delete(db.session.query(Approval).filter(Approval.id==id).first())
    db.session.commit()

################################################


@app.route('/ControlPanel/products/')
@auth.login_required
def cpanel_products():
    page = request.args.get('page')
    show = request.args.get('show')
    #lang = request.args.get('lang')
    lang = 'en'
    if show:
        return render_template('cpanel_products.html',products = db.session.query(Product).filter(Product.lang==lang).all(),page=page,show=show,categories = db.session.query(Category).all(),)
    else:
        return render_template('cpanel_products.html',products = db.session.query(Product).filter(Product.lang==lang).all(),page=page,show='10',categories = db.session.query(Category).all(),)


@app.route('/ControlPanel/products/add/',methods=['POST','GET'])
@auth.login_required
def products_add():
    data = json.loads(request.form['data'])
    name = data['name']
    img = data['img']
    desc = data['desc']
    category = data['category']
    country = data['country']
    lang = data['lang']
    if request.files:
        datasheet = request.files['file']
        upload = Upload(filename=datasheet.filename, data=datasheet.read())
        db.session.add(upload)
        db.session.commit()
        s1 = Product(name=name,img=img,desc=desc,category=category,country=country,lang=lang,datasheet=upload.id)
    else:
        s1 = Product(name=name,img=img,desc=desc,category=category,country=country,lang=lang,datasheet="")
    db.session.add(s1)
    #db.session.execute(insert(Product).values(s1))
    db.session.commit()
    return 'True'


@app.route('/ControlPanel/products/edit/<id>/',methods=['POST','GET'])
@auth.login_required
def products_edit(id):
    data = json.loads(request.form['data'])
    name = data['name']
    img = data['img']
    desc = data['desc']
    category = data['category']
    country = data['country']
    lang = data['lang']
    query = db.session.query(Product).filter(Product.id==id).first()
    if request.files:
        datasheet = request.files['file']
        if datasheet!='' and datasheet!=None and datasheet!='undefined':
            upload = db.session.query(Upload).filter(Upload.id==query.datasheet).first()
            if upload:
                upload.filename = datasheet.filename
                upload.data = datasheet.read()
            else:
                upload = Upload(filename=datasheet.filename, data=datasheet.read())
                db.session.add(upload)
                db.session.commit()
                query.datasheet = upload.id
    if name!='' and name!=None and name!='undefined':
        query.name = name
    if desc!='' and desc!=None and desc!='undefined':
        query.desc = desc
    if category!='' and category!=None and category!='undefined':
        query.category = category
    if country!='' and country!=None and country!='undefined':
        query.country = country
    if lang!='' and lang!=None and lang!='undefined':
        query.lang = lang
    if img!='' and img!=None and img!='undefined':
        query.img = img
    
    db.session.commit()
    return json.dumps(True)


@app.route('/ControlPanel/products/del/<id>/')
@auth.login_required
def products_del(id):
    db.session.delete(db.session.query(Product).filter(Product.id==id).first())
    db.session.commit()
    

################################################


@app.route('/ControlPanel/catalogs/')
@auth.login_required
def cpanel_catalogs():
    page = request.args.get('page')
    show = request.args.get('show')
    lang = request.args.get('lang', session.get('preferred_admin_lang', 'en')).lower()
    if lang not in {'en', 'ar'}:
        lang = 'en'
    session['preferred_admin_lang'] = lang

    catalogs_query = db.session.query(Catalog).filter(Catalog.lang == lang).order_by(desc(Catalog.id))
    categories = db.session.query(Category).order_by(Category.id).all()

    if not page:
        page = '1'
    if not show:
        show = '10'

    return render_template(
        'cpanel_catalogs.html',
        catalogs=catalogs_query.all(),
        page=page,
        show=show,
        categories=categories,
        lang=lang
    )


@app.route('/ControlPanel/catalogs/add/',methods=['POST','GET'])
@auth.login_required
def catalogs_add():
    data = json.loads(request.form['data'])
    name = data['name']
    img = data['img']
    category = data['category']
    lang = data['lang']
    country = data['country']
    upload_file = request.files.get('file')

    upload = None
    if upload_file and upload_file.filename:
        upload = Upload(filename=upload_file.filename, data=upload_file.read())
        db.session.add(upload)
        db.session.flush()

    catalog = Catalog(
        name=name,
        img=img,
        link=str(upload.id) if upload else None,
        category=category,
        country=country,
        lang=lang
    )
    db.session.add(catalog)
    db.session.commit()
    return 'True'


@app.route('/ControlPanel/catalogs/edit/<id>/',methods=['POST','GET'])
@auth.login_required
def catalogs_edit(id):
    data = json.loads(request.form['data'])
    name = data['name']
    img = data['img']
    category = data['category']
    lang = data['lang']
    country = data['country']
    query = db.session.query(Catalog).filter(Catalog.id==id).first()
    if not query:
        abort(404)

    upload_file = request.files.get('file')
    if upload_file and upload_file.filename:
        existing_upload = None
        if query.link and str(query.link).isdigit():
            existing_upload = db.session.query(Upload).filter(Upload.id == int(query.link)).first()

        if existing_upload:
            existing_upload.filename = upload_file.filename
            existing_upload.data = upload_file.read()
        else:
            new_upload = Upload(filename=upload_file.filename, data=upload_file.read())
            db.session.add(new_upload)
            db.session.flush()
            query.link = str(new_upload.id)
    if name!='' and name!=None and name!='undefined':
        query.name = name
    if category!='' and category!=None and category!='undefined':
        query.category = category
    if country!='' and country!=None and country!='undefined':
        query.country = country
    if lang!='' and lang!=None and lang!='undefined':
        query.lang = lang
    if img!='' and img!=None and img!='undefined':
        query.img = img
    db.session.commit()
    return 'True'


@app.route('/ControlPanel/catalogs/del/<id>/')
@auth.login_required
def catalogs_del(id):
    catalog = db.session.query(Catalog).filter(Catalog.id == id).first()
    if not catalog:
        abort(404)
    db.session.delete(catalog)
    db.session.commit()

####################################################


@app.route('/ControlPanel/TechnicalDatasheets/')
@auth.login_required
def cpanel_TechnicalDatasheets():
    page = request.args.get('page')
    show = request.args.get('show')
    #lang = request.args.get('lang')
    lang = 'en'
    if show:
        return render_template('cpanel_TechnicalDatasheets.html',TechnicalDatasheets = db.session.query(TechnicalDatasheet).filter(TechnicalDatasheet.lang==lang).all(),page=page,show=show,categories = db.session.query(Category).all(),)
    else:
        return render_template('cpanel_TechnicalDatasheets.html',TechnicalDatasheets = db.session.query(TechnicalDatasheet).filter(TechnicalDatasheet.lang==lang).all(),page=page,show='10',categories = db.session.query(Category).all(),)


@app.route('/ControlPanel/TechnicalDatasheets/add/',methods=['POST','GET'])
@auth.login_required
def TechnicalDatasheets_add():
    data = request.get_json()
    name = data['name']
    link = data['link']
    category = data['category']
    country = data['country']
    lang = data['lang']
    s1 = TechnicalDatasheet(name=name,link=link,category=category,country=country,lang=lang)
    db.session.add(s1)
    db.session.commit()
    return 'True'


@app.route('/ControlPanel/TechnicalDatasheets/edit/<id>/',methods=['POST','GET'])
@auth.login_required
def TechnicalDatasheets_edit(id):
    data = request.get_json()
    name = data['name']
    link = data['link']
    category = data['category']
    country = data['country']
    lang = data['lang']
    query = db.session.query(TechnicalDatasheet).filter(TechnicalDatasheet.id==id).first()
    if name!='' and name!=None and name!='undefined':
        query.name = name
    if link!='' and link!=None and link!='undefined':
        query.link = link
    if category!='' and category!=None and category!='undefined':
        query.category = category
    if country!='' and country!=None and country!='undefined':
        query.country = country
    if lang!='' and lang!=None and lang!='undefined':
        query.lang = lang
    db.session.commit()


@app.route('/ControlPanel/TechnicalDatasheets/del/<id>/')
@auth.login_required
def TechnicalDatasheets_del(id):
    db.session.delete(db.session.query(TechnicalDatasheet).filter(TechnicalDatasheet.id==id).first())
    db.session.commit()

####################################################


@app.route('/ControlPanel/')
@app.route('/ControlPanel/categories/')
@auth.login_required
def cpanel_categories():
    page = request.args.get('page')
    show = request.args.get('show')
    if page:
        if show:
            return render_template('cpanel_categories.html',categories = db.session.query(Category).all(),page=page,show=show)
        else:
            return render_template('cpanel_categories.html',categories = db.session.query(Category).all(),page=page,show='10')
    else:
        if show:
            return render_template('cpanel_categories.html',categories = db.session.query(Category).all(),page='1',show=show)
        else:
            return render_template('cpanel_categories.html',categories = db.session.query(Category).all(),page='1',show='10')


@app.route('/ControlPanel/categories/add/',methods=['POST','GET'])
@auth.login_required
def categories_add():
    data = request.get_json()
    name = data['name']
    nameArabic = data['namearabic']
    img = data['img']
    desc = data['desc']
    s1 = Category(name=name,img=img,desc=desc,nameArabic=nameArabic)
    db.session.add(s1)
    db.session.commit()
    return 'True'


@app.route('/ControlPanel/categories/edit/<id>/',methods=['POST','GET'])
@auth.login_required
def categories_edit(id):
    data = request.get_json()
    name = data['name']
    nameArabic = data['namearabic']
    img = data['img']
    desc = data['desc']
    query = db.session.query(Category).filter(Category.id==id).first()
    if name!='' and name!=None and name!='undefined':
        query.name = name
    if desc!='' and desc!=None and desc!='undefined':
        query.desc = desc
    if nameArabic!='' and nameArabic!=None and nameArabic!='undefined':
        query.nameArabic = nameArabic
    if img!='' and img!=None and img!='undefined':
        query.img = img
    db.session.commit()


@app.route('/ControlPanel/categories/del/<id>/')
@auth.login_required
def categories_del(id):
    db.session.delete(db.session.query(Category).filter(Category.id==id).first())
    db.session.commit()

@app.route('/getProducts/')
def get_products():
    lang = request.args.get('lang', 'en')
    if lang not in {'en', 'ar'}:
        lang = 'en'
    data, timestamp = get_cached_products(lang)
    etag = f'products-{lang}-{int(timestamp)}'

    if request.headers.get('If-None-Match') == etag:
        return '', 304

    response = jsonify(data)
    response.headers['Cache-Control'] = f'public, max-age={PRODUCT_CACHE_TTL}'
    response.headers['ETag'] = etag
    return response

@app.route('/getsocialIcons/')
def getsocialIcons():
    try:
        data, timestamp = get_cached_social_icons()
    except Exception as exc:
        print(f'Error loading social icons: {exc}')
        return jsonify([])

    etag = f'social-icons-{int(timestamp)}'
    if request.headers.get('If-None-Match') == etag:
        return '', 304

    response = jsonify(data)
    response.headers['Cache-Control'] = f'public, max-age={SOCIAL_CACHE_TTL}'
    response.headers['ETag'] = etag
    return response

################################################################

# Performance: Pre-warm the categories cache on startup
try:
    with app.app_context():
        print("Pre-warming categories cache...")
        get_cached_categories()
        print("Pre-warming product cache...")
        get_cached_products('en')
        get_cached_products('ar')
        print("Pre-warming social icons cache...")
        get_cached_social_icons()
        print("Application caches pre-warmed successfully")
except Exception as e:
    print(f"Could not pre-warm caches: {e}")
