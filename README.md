# Smart 99¢ Plus

E-commerce web application for Smart 99¢ Plus retail store.

**Store:** 66 NY-109, West Babylon, NY 11704  
**Phone:** 516-851-8097  
**Domain:** Smart99c.com

## Tech Stack

- **Backend:** Flask (Python)
- **Database:** PostgreSQL (Render) with SQLAlchemy ORM
- **Images:** Cloudinary
- **Payments:** Stripe
- **Hosting:** Render

## Local Setup

```bash
# Clone and enter directory
git clone <repo-url>
cd smart99c

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your credentials

# Initialize database
flask db init
flask db migrate -m "initial"
flask db upgrade

# Seed database
python seed.py

# Run development server
python run.py
```

## Deployment (Render)

Start command: `gunicorn run:app`

Set all environment variables from `.env.example` in Render dashboard.

## Admin Access

After seeding: `admin@smart99c.com` / `Admin123!`
