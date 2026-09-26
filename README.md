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

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python init_db.py && gunicorn run:app`
- **Python version:** pinned by `.python-version` (3.11.9)

Set all environment variables from `.env.example` in the Render dashboard.

`init_db.py` runs on every start. It creates missing tables and columns and,
if the database has no admin yet, creates one from `ADMIN_EMAIL` /
`ADMIN_PASSWORD`. It never deletes data.

### Recovering from a deleted database

Render deletes **free** Postgres databases after 30 days. The web service then
logs `failed to resolve host 'dpg-…'`. To recover:

1. Render → **New → PostgreSQL**, same region as the web service. Choose a
   paid plan (Basic) so it is not deleted again.
2. Copy the new database's **Internal Database URL**.
3. Web service → **Environment** → set `DATABASE_URL` to that URL, and set
   `ADMIN_EMAIL` / `ADMIN_PASSWORD`.
4. Web service → **Settings** → Start Command `python init_db.py && gunicorn run:app`.
5. **Manual Deploy → Deploy latest commit**, then log in at `/login`.
6. Re-add products with **Admin → Products → Import** (Excel).

## Admin Access

After seeding: `admin@smart99c.com` / `Admin123!`
